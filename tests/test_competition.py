"""Generic frozen targets retain the existing PAPER safety boundary."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from quantlab.competition import model_times,policy_version
from quantlab.paper import BrokerSafetyError,build_plan,validate_forecast,validate_plan
from tests.test_paper_execution import fixtures,NOW


class GenericTargetsTests(unittest.TestCase):
    def setUp(self):
        self.cfg,self.execution,self.batch,self.snapshot,old_contracts,old_quotes=fixtures()
        self.policy={"name":"synthetic_model_policy","rebalance_anchor":"2026-09-22","spec":{"cadence":5}}
        version=policy_version(self.policy)
        self.execution.update(active_model="ridge10",research_policy_version=version,symbols=["AAA","BBB","CCC","OLD"],max_weight=.4,max_trade_adv_fraction=.01)
        self.batch.update(schema="frozen_targets_v1",policy=self.policy,version=version,active_model="ridge10")
        self.batch.update(model_times(self.batch["decision_date"],self.batch["issued_at_utc"],self.policy))
        self.batch["position_intents"]=[{"symbol":s,"model":"ridge10","role":"active","target_weight":w} for s,w in [("AAA",.30),("BBB",.20),("CCC",.10)]]
        self.batch["dollar_adv"]={s:50_000_000 for s in self.execution["symbols"]}
        self.contracts={s:{**old_contracts["XLB"],"symbol":s,"con_id":100+i} for i,s in enumerate(self.execution["symbols"])}
        self.quotes={s:deepcopy(old_quotes["XLB"]) for s in self.execution["symbols"]}
        self.verify=patch("quantlab.competition.verify_frozen_policy")
        self.verify.start()
        self.addCleanup(self.verify.stop)

    def plan(self):
        return build_plan(self.batch,self.snapshot,self.contracts,self.quotes,self.cfg,self.execution,NOW)

    def test_variable_symbols_weights_and_cash(self):
        plan=self.plan()
        self.assertEqual({o["symbol"] for o in plan["orders"]},{"AAA","BBB","CCC"})
        shares={o["symbol"]:o["quantity"] for o in plan["orders"]}
        self.assertGreater(shares["AAA"],shares["BBB"])
        self.assertGreater(shares["BBB"],shares["CCC"])
        self.assertLess(sum(o["estimated_notional"] for o in plan["orders"]),self.snapshot["nav"]*.61)

    def test_exit_positions_omitted_from_new_target(self):
        self.snapshot["positions"]=[{**self.contracts["OLD"],"account":"DU123456","quantity":50}]
        sale=next(o for o in self.plan()["orders"] if o["symbol"]=="OLD")
        self.assertEqual((sale["side"],sale["quantity"]),("SELL",50))
        self.assertEqual(sale["target_weight"],0)

    def test_cash_forecast_is_valid_and_generates_no_orders(self):
        self.batch["position_intents"]=[]
        self.assertEqual(self.plan()["orders"],[])

    def test_shadow_unknown_and_excess_risk_rejected(self):
        for change in ({"symbol":"UNKNOWN"},{"model":"boost10"},{"target_weight":.5},{"target_weight":-1}):
            batch=deepcopy(self.batch)
            batch["position_intents"][0].update(change)
            with self.assertRaises(BrokerSafetyError):
                validate_forecast(batch,self.execution,NOW)

    def test_portfolio_adv_cap_and_no_replay_to_other_policy(self):
        self.batch["dollar_adv"]["AAA"]=500_000
        plan=self.plan()
        buy=next(o for o in plan["orders"] if o["symbol"]=="AAA")
        self.assertLessEqual(buy["estimated_notional"],5000)
        policy={**self.execution,"research_policy_version":"wrong"}
        with self.assertRaises(BrokerSafetyError):
            validate_plan(plan,self.batch,self.snapshot,self.contracts,self.quotes,self.cfg,policy,NOW)

    def test_frozen_cadence_and_no_intraday_catchup(self):
        self.assertEqual(self.batch["exit_date"],"2026-09-29")
        with self.assertRaises(BrokerSafetyError):
            model_times("2026-09-22","2026-09-22T21:01:00Z",self.policy)
        with self.assertRaises(BrokerSafetyError):
            validate_forecast(self.batch,self.execution,"2026-09-22T13:30:00Z",True)


if __name__=="__main__":
    unittest.main()
