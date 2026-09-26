"""Offline commissioning tests: synthetic broker data stays in temp ledgers."""
from copy import deepcopy
from contextlib import ExitStack
from decimal import Decimal
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from quantlab.data import ROOT, digest
from quantlab.forward import forecast_times
from quantlab.paper import (BrokerSafetyError, PaperLedger, build_plan, canonical,
                           execution_policy, load_forecast, validate_contract, validate_forecast,
                           validate_plan, validate_quote, validate_snapshot, verify_account)
from quantlab.paper_cli import schedule


NOW = "2026-09-22T13:11:00+00:00"


def fixtures():
    policy = json.loads((ROOT/"config/paper_execution_v1.json").read_text())
    policy["transmission_suspended"] = False
    cfg = {"mode":"PAPER","expected_account":"DU123456","paper_account_allowlist":["DU123456"],"execution_enabled":False,
           "independently_verified_paper":True,"client_id":721,"host":"127.0.0.1","port":7497}
    frozen = {"policy":{"name":"test_fixture","active_model":"equal_weight"},"research_config":{},"model_source_sha256":"test_only"}
    version = digest(json.dumps(frozen,sort_keys=True).encode())[:20]
    policy["research_policy_version"] = version
    issued = "2026-09-21T21:01:00+00:00"
    batch = {"id":"synthetic_fixture","version":version,"policy":frozen,"active_model":"equal_weight",
             "issued_at_utc":issued,**forecast_times("2026-09-21",issued),
             "position_intents":[{"symbol":s,"model":"equal_weight","role":"active","target_weight":1/9} for s in policy["symbols"]]}
    snapshot = {"account":cfg["expected_account"],"managed_accounts":[cfg["expected_account"]],"observed_at":NOW,"complete":True,
                "base_currency":"USD","connection_healthy":True,"nav":123456.0,"cash":123456.0,"available_funds":123456.0,
                "buying_power":246912.0,"positions":[],"open_orders":[],"executions":[],"completed_orders":[]}
    contracts = {s:{"symbol":s,"sec_type":"STK","exchange":"SMART","primary_exchange":"ARCA","currency":"USD","con_id":100+i,"min_tick":.01,"order_types":["LMT","MKT"]} for i,s in enumerate(policy["symbols"])}
    quotes = {s:{"bid":100.,"ask":100.02,"last":100.01,"bid_at":NOW,"ask_at":NOW,"market_data_type":1} for s in policy["symbols"]}
    return cfg,policy,batch,snapshot,contracts,quotes


class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.cfg,self.policy,self.batch,self.snapshot,self.contracts,self.quotes = fixtures()

    def plan(self):
        return build_plan(self.batch,self.snapshot,self.contracts,self.quotes,self.cfg,self.policy,NOW)

    def test_reject_live_even_if_misconfigured_allowlist(self):
        self.cfg.update(expected_account="U123456",paper_account_allowlist=["U123456"],execution_enabled=True)
        with self.assertRaisesRegex(BrokerSafetyError,"Live"):
            verify_account(self.cfg,["U123456"],True)

    def test_wrong_multiple_and_unconfigured_account(self):
        for accounts in (["DU99999"],["DU123456","U123456"],[],"DU123456"):
            with self.subTest(accounts=accounts), self.assertRaises(BrokerSafetyError):
                verify_account(self.cfg,accounts)
        self.cfg["paper_account_allowlist"] = []
        with self.assertRaises(BrokerSafetyError):
            verify_account(self.cfg,["DU123456"])

    def test_enabled_requires_explicit_true_and_mode(self):
        for value in (False,1,"true",None):
            self.cfg["execution_enabled"] = value
            with self.subTest(value=value),self.assertRaises(BrokerSafetyError):
                verify_account(self.cfg,["DU123456"],True)
        self.cfg["execution_enabled"] = True
        for mode in ("LIVE","paper","DRY_RUN",None):
            self.cfg["mode"] = mode
            with self.assertRaises(BrokerSafetyError):
                verify_account(self.cfg,["DU123456"],True)

    def test_independent_environment_assertion_required(self):
        self.cfg["independently_verified_paper"] = False
        with self.assertRaises(BrokerSafetyError):
            self.plan()

    def test_stale_forecast_and_late_issuance(self):
        with self.assertRaisesRegex(BrokerSafetyError,"Stale"):
            validate_forecast(self.batch,self.policy,"2026-09-22T13:30:00Z")
        self.batch["issued_at_utc"] = "2026-09-22T13:29:00Z"
        with self.assertRaises(ValueError):
            self.plan()

    def test_real_repository_expired_forecast(self):
        batch = load_forecast("batch_82b72b259653437c")
        with self.assertRaisesRegex(BrokerSafetyError,"Stale"):
            validate_forecast(batch,json.loads((ROOT/"config/paper_execution_v1.json").read_text()),"2026-09-21T18:00:00Z")

    def test_no_arbitrary_intraday_or_closed_session(self):
        for now in ("2026-09-22T13:09:59Z","2026-09-22T13:25:00Z","2026-09-22T18:00:00Z"):
            with self.assertRaises(BrokerSafetyError):
                validate_forecast(self.batch,self.policy,now,True)
        self.batch["entry_at"] = "2026-09-26T13:30:00+00:00"
        with self.assertRaises(BrokerSafetyError):
            self.plan()

    def test_stale_and_delayed_quotes(self):
        for changes in ({"bid_at":"2026-09-22T13:10:00Z"},{"ask_at":"2026-09-22T13:12:00Z"},
                        {"market_data_type":2},{"market_data_type":3},{"market_data_type":4},
                        {"bid":0},{"bid":float("nan")},{"bid":101},{"ask":110}):
            q = {**self.quotes["XLB"],**changes}
            with self.subTest(changes=changes),self.assertRaises(BrokerSafetyError):
                validate_quote(q,self.policy,NOW)

    def test_incomplete_stale_account_reconnect(self):
        for changes in ({"complete":False},{"connection_healthy":False},{"observed_at":"2026-09-22T13:09:00Z"},
                        {"base_currency":"CAD"},{"cash":-1},{"nav":float("inf")}):
            with self.subTest(changes=changes),self.assertRaises(BrokerSafetyError):
                validate_snapshot({**self.snapshot,**changes},self.cfg,self.policy,NOW)

    def test_contract_ambiguity_and_currency(self):
        c = self.contracts["XLB"]
        for rows in ([],[c,c],[{**c,"con_id":0}],[{**c,"currency":"CAD"}],[{**c,"symbol":"SPY"}]):
            with self.assertRaises(BrokerSafetyError):
                validate_contract("XLB",rows)

    def test_whole_share_actual_nav_and_costs(self):
        plan = self.plan()
        self.assertEqual(len(plan["orders"]),9)
        self.assertTrue(all(type(o["quantity"]) is int and o["quantity"]>0 for o in plan["orders"]))
        spent = sum(o["estimated_notional"]+o["estimated_cost"] for o in plan["orders"])
        self.assertLessEqual(spent,self.snapshot["cash"]-self.snapshot["nav"]*.0025)
        self.assertLess(spent,124000)
        self.assertTrue(all(o["tif"]=="OPG" and o["order_type"]=="LMT" for o in plan["orders"]))

    def test_existing_positions_only_delta(self):
        target = self.plan()["targets"]["XLB"]["desired_quantity"]
        self.snapshot["positions"] = [{**self.contracts["XLB"],"account":"DU123456","quantity":target-3}]
        row = next(o for o in self.plan()["orders"] if o["symbol"]=="XLB")
        self.assertEqual(row["quantity"],3)
        self.assertEqual(row["resulting_quantity"],target)

    def test_cash_constrained_buys_do_not_spend_sale_proceeds(self):
        self.snapshot["positions"] = [{**self.contracts["XLB"],"account":"DU123456","quantity":250}]
        self.snapshot["cash"] = 1000
        self.snapshot["available_funds"] = 1000
        plan = self.plan()
        self.assertTrue(any(o["side"]=="SELL" for o in plan["orders"]))
        buys = [o for o in plan["orders"] if o["side"]=="BUY"]
        self.assertLessEqual(sum(o["estimated_notional"]+o["estimated_cost"] for o in buys),1000-123456*.0025)

    def test_unknown_fractional_short_holdings(self):
        for symbol,quantity in (("SPY",1),("XLB",.5),("XLB",-1)):
            self.snapshot["positions"] = [{**self.contracts["XLB"],"symbol":symbol,"account":"DU123456","quantity":quantity}]
            with self.assertRaises(BrokerSafetyError):
                self.plan()

    def test_shadow_cannot_become_active(self):
        self.batch["position_intents"][0]["model"] = "trend_pullback"
        with self.assertRaises(BrokerSafetyError):
            self.plan()

    def test_unknown_or_duplicate_target(self):
        self.batch["position_intents"][0]["symbol"] = "XLE"
        with self.assertRaises(BrokerSafetyError):
            self.plan()

    def test_tampered_order_and_zero_quantity(self):
        for qty in (0,-1,1.5,10**12):
            plan = self.plan()
            plan["orders"][0]["quantity"] = qty
            plan["id"] = digest(canonical({k:v for k,v in plan.items() if k!="id"}).encode())
            with self.assertRaises(BrokerSafetyError):
                validate_plan(plan,self.batch,self.snapshot,self.contracts,self.quotes,self.cfg,self.policy,NOW)

    def test_changed_position_and_plan_integrity(self):
        plan = self.plan()
        plan["orders"][0]["limit_price"] += 1
        with self.assertRaisesRegex(BrokerSafetyError,"modified"):
            validate_plan(plan,self.batch,self.snapshot,self.contracts,self.quotes,self.cfg,self.policy,NOW)
        plan = self.plan()
        self.snapshot["positions"] = [{**self.contracts["XLB"],"account":"DU123456","quantity":1}]
        with self.assertRaisesRegex(BrokerSafetyError,"Positions changed"):
            validate_plan(plan,self.batch,self.snapshot,self.contracts,self.quotes,self.cfg,self.policy,NOW)

    def test_nyse_weekend_and_early_close(self):
        s = schedule("2026-09-26T15:00:00Z")
        self.assertFalse(s["market_open"])
        self.assertEqual(s["next_commission_at"],"2026-09-28T13:10:00+00:00")
        s = schedule("2026-11-27T18:00:00Z")
        self.assertEqual(s["next_decision_at"],"2026-11-27T19:00:00+00:00")


class LedgerTests(unittest.TestCase):
    def setUp(self):
        (ROOT/".cache/tests").mkdir(parents=True,exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/".cache/tests")
        self.path = Path(self.temp.name)/"paper.sqlite"
        self.ledger = PaperLedger(self.path)
        self.cfg,self.policy,self.batch,self.snapshot,self.contracts,self.quotes = fixtures()
        self.plan = build_plan(self.batch,self.snapshot,self.contracts,self.quotes,self.cfg,self.policy,NOW)

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def reserve(self):
        return self.ledger.reserve(self.plan,721,100)

    def test_intention_dry_run_is_neither_submission_nor_fill(self):
        self.ledger.observe("dry_run",self.plan)
        self.assertEqual(self.ledger.db.execute("SELECT count(*) FROM orders").fetchone()[0],0)
        self.assertEqual(self.ledger.db.execute("SELECT count(*) FROM executions").fetchone()[0],0)
        self.reserve()
        states = [r[0] for r in self.ledger.db.execute("SELECT kind FROM order_events")]
        self.assertEqual(set(states),{"GENERATED"})

    def test_duplicate_batch_survives_restart_and_changed_client(self):
        self.reserve()
        self.ledger.close()
        self.ledger = PaperLedger(self.path)
        with self.assertRaisesRegex(BrokerSafetyError,"Duplicate"):
            self.ledger.reserve(self.plan,722,900)
        self.assertEqual(self.ledger.db.execute("SELECT count(*) FROM orders").fetchone()[0],9)

    def test_network_ambiguity_cannot_resubmit(self):
        self.reserve()
        self.ledger.once_submitting(721,100)
        self.ledger.close()
        self.ledger = PaperLedger(self.path)
        with self.assertRaisesRegex(BrokerSafetyError,"Repeated"):
            self.ledger.once_submitting(721,100)
        state = self.ledger.reconcile(self.snapshot)[0]
        self.assertEqual(state["state"],"UNKNOWN_RECONCILE")

    def fill(self,quantity=3,identifier="TEST.01"):
        order = self.plan["orders"][0]
        return {"execution_id":identifier,"order_id":100,"client_id":721,"account":"DU123456","order_ref":order["order_ref"],"symbol":order["symbol"],"side":"BUY","quantity":str(quantity),"price":100.01}

    def test_partial_fills_broker_truth_and_deduplication(self):
        self.reserve()
        fill = self.fill()
        self.ledger.fill(fill)
        self.ledger.fill(fill)
        self.snapshot["executions"] = [fill]
        row = self.ledger.reconcile(self.snapshot)[0]
        self.assertEqual(row["state"],"PARTIALLY_FILLED")
        self.assertEqual(row["remaining"],self.plan["orders"][0]["quantity"]-3)
        self.assertEqual(row["commissions"],[None])
        with self.assertRaises(BrokerSafetyError):
            self.reserve()

    def test_filled_status_without_execution_is_not_filled(self):
        self.reserve()
        self.ledger.once_submitting(721,100)
        self.ledger.event("STATUS",721,100,{"status":"Filled","filled":"136"})
        self.assertEqual(self.ledger.reconcile(self.snapshot)[0]["state"],"UNKNOWN_RECONCILE")

    def test_fill_commission_and_correction(self):
        self.reserve()
        qty = self.plan["orders"][0]["quantity"]
        self.ledger.fill(self.fill(qty))
        self.ledger.commission({"execution_id":"TEST.01","commission":1.2,"currency":"USD"})
        row = self.ledger.reconcile(self.snapshot)[0]
        self.assertEqual(row["state"],"FILLED")
        self.assertEqual(row["commissions"][0]["commission"],1.2)
        self.ledger.fill(self.fill(qty-1,"TEST.02"))
        self.assertEqual(self.ledger.reconcile(self.snapshot)[0]["filled"],qty-1)

    def test_cancellation_after_restart_from_completed_orders(self):
        self.reserve()
        self.snapshot["completed_orders"] = [{"order_ref":self.plan["orders"][0]["order_ref"],"status":"Cancelled"}]
        self.assertEqual(self.ledger.reconcile(self.snapshot)[0]["state"],"CANCELLED")

    def test_final_positions_require_broker_reconciliation(self):
        self.reserve()
        fill = self.fill()
        self.ledger.fill(fill)
        self.assertTrue(self.ledger.reconcile_positions(self.snapshot)["differences"])
        self.snapshot["positions"] = [{"symbol":fill["symbol"],"quantity":3}]
        self.assertEqual(self.ledger.reconcile_positions(self.snapshot)["differences"],[])


class OfficialAdapterTests(unittest.TestCase):
    def setUp(self):
        from quantlab.tws import PaperTWS
        (ROOT/".cache/tests").mkdir(parents=True,exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/".cache/tests")
        self.ledger = PaperLedger(Path(self.temp.name)/"paper.sqlite")
        self.cfg,self.policy,self.batch,self.snapshot,self.contracts,self.quotes = fixtures()
        self.broker = PaperTWS(self.cfg,self.policy,self.ledger)

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def test_direct_transport_is_disabled(self):
        with self.assertRaises(BrokerSafetyError):
            self.broker.placeOrder(1,None,None)
        with self.assertRaises(BrokerSafetyError):
            self.broker.placeOrderProtoBuf(None)

    def test_official_order_has_exact_account_opg_and_whole_shares(self):
        from quantlab.tws import make_order
        plan = build_plan(self.batch,self.snapshot,self.contracts,self.quotes,self.cfg,self.policy,NOW)
        row = plan["orders"][0]
        order = make_order(row,"DU123456")
        self.assertEqual(order.account,"DU123456")
        self.assertEqual(order.totalQuantity,Decimal(row["quantity"]))
        self.assertEqual(order.tif,"OPG")
        self.assertFalse(order.whatIf)
        self.assertTrue(make_order(row,"DU123456",True).whatIf)

    def test_reconnect_error_never_self_heals(self):
        self.broker.accounts = ["DU123456"]
        self.broker.healthy = True
        self.broker.error(-1,1234567890,1100,"lost connection","")
        self.broker.error(-1,1234567890,1102,"restored","")
        self.assertFalse(self.broker.healthy)
        with patch.object(self.broker,"isConnected",return_value=True),self.assertRaises(BrokerSafetyError):
            self.broker.assert_session(True)

    def test_preview_callbacks_never_create_actual_orders(self):
        self.broker.preview_ids.add(100)
        from ibapi.order_state import OrderState
        self.broker.openOrder(100,None,None,OrderState())
        self.broker.orderStatus(100,"PreSubmitted",Decimal(0),Decimal(2),0,0,0,0,721,"",0)
        self.assertEqual(self.ledger.db.execute("SELECT count(*) FROM order_events").fetchone()[0],0)
        self.assertFalse(self.broker.open_orders)

    def test_live_account_stops_constructor_before_socket(self):
        from quantlab.tws import PaperTWS
        self.cfg.update(expected_account="U123456",paper_account_allowlist=["U123456"])
        with self.assertRaises(BrokerSafetyError):
            PaperTWS(self.cfg,self.policy,self.ledger)

    def transmission_fixture(self, stack):
        from ibapi.contract import Contract
        self.broker.cfg["execution_enabled"] = True
        self.broker.accounts,self.broker.healthy,self.broker.next_id = ["DU123456"],True,100
        self.broker.resolved = self.contracts
        self.broker.resolved_objects = {s:Contract() for s in self.policy["symbols"]}
        stack.enter_context(patch.object(self.broker,"isConnected",return_value=True))
        stack.enter_context(patch.object(self.broker,"clock_check"))
        stack.enter_context(patch.object(self.broker,"current_quotes",return_value=self.quotes))
        stack.enter_context(patch("quantlab.tws.now_utc",return_value=NOW))
        stack.enter_context(patch("quantlab.tws.load_forecast",return_value=self.batch))
        stack.enter_context(patch("quantlab.paper_cli.require_tested"))
        # Arm persistence and wire authorization have separate integration tests.
        stack.enter_context(patch("quantlab.paper_autonomy.consume_for_transmission",return_value={"test":True}))
        stack.enter_context(patch("quantlab.paper_autonomy.check_wire_arm"))
        plan = build_plan(self.batch,self.snapshot,self.contracts,self.quotes,self.cfg,self.policy,NOW)
        previews = [{"at":NOW,"plan_id":plan["id"],"order_key":o["key"],"what_if":True} for o in plan["orders"]]
        for preview in previews:
            self.ledger.observe("what_if_passed",preview)
        return plan,previews

    def test_guarded_wire_reserves_before_send_then_restart_rejects(self):
        with ExitStack() as stack:
            plan,previews = self.transmission_fixture(stack)
            def wire(obj,order_id,contract,order):
                row = self.ledger.db.execute("SELECT kind FROM order_events WHERE order_id=? ORDER BY seq DESC LIMIT 1",(order_id,)).fetchone()
                self.assertEqual(row[0],"SUBMITTING")
                self.assertEqual(order.account,"DU123456")
                self.assertFalse(order.whatIf)
            spy = stack.enter_context(patch("quantlab.tws.EClient.placeOrder",autospec=True,side_effect=wire))
            self.broker.transmit_batch(plan,self.batch,self.snapshot,previews)
            self.assertEqual(spy.call_count,9)
            self.assertEqual(self.ledger.db.execute("SELECT count(*) FROM executions").fetchone()[0],0)
            with self.assertRaisesRegex(BrokerSafetyError,"Duplicate"):
                self.broker.transmit_batch(plan,self.batch,self.snapshot,previews)
            self.assertEqual(spy.call_count,9)

    def test_guards_prevent_sdk_wire_call(self):
        for failure in ("disabled","wrong_account","quote","snapshot","preview","frozen_batch"):
            with self.subTest(failure=failure), ExitStack() as stack:
                plan,previews = self.transmission_fixture(stack)
                spy = stack.enter_context(patch("quantlab.tws.EClient.placeOrder"))
                snapshot = deepcopy(self.snapshot)
                if failure == "disabled":
                    self.broker.cfg["execution_enabled"] = False
                elif failure == "wrong_account":
                    self.broker.accounts = ["U123456"]
                elif failure == "quote":
                    stack.enter_context(patch.object(self.broker,"current_quotes",return_value={s:{**q,"market_data_type":3} for s,q in self.quotes.items()}))
                elif failure == "snapshot":
                    snapshot["complete"] = False
                elif failure == "preview":
                    previews = previews[:-1]
                else:
                    stack.enter_context(patch("quantlab.tws.load_forecast",return_value={}))
                with self.assertRaises(BrokerSafetyError):
                    self.broker.transmit_batch(plan,self.batch,snapshot,previews)
                spy.assert_not_called()

    def test_sdk_protobuf_serialization_preserves_guarded_parameters(self):
        from ibapi.order import Order
        from ibapi.contract import Contract
        from ibapi import client_utils
        from ibapi.protobuf.PlaceOrderRequest_pb2 import PlaceOrderRequest
        from quantlab.tws import make_order
        plan = build_plan(self.batch,self.snapshot,self.contracts,self.quotes,self.cfg,self.policy,NOW)
        request = client_utils.createPlaceOrderRequestProto(100,Contract(),make_order(plan["orders"][0],"DU123456",True))
        self.assertEqual(request.orderId,100)
        self.assertEqual(request.order.account,"DU123456")
        self.assertTrue(request.order.whatIf)


if __name__ == "__main__":
    unittest.main()
