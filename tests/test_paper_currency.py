"""Currency normalization and transport regression tests; no broker orders."""
from copy import deepcopy
import unittest
from unittest.mock import Mock

from test_paper_execution import fixtures, NOW
from quantlab.paper import (BrokerSafetyError, build_plan, currency_cash, usd_amounts,
                           validate_plan, validate_snapshot, verify_account)
from quantlab.tws import PaperTWS


class CurrencyTests(unittest.TestCase):
    def setUp(self):
        self.cfg,self.policy,self.batch,self.snapshot,self.contracts,self.quotes = fixtures()
        self.fx = {"symbol":"USD","currency":"CAD","sec_type":"CASH","exchange":"IDEALPRO",
                   "bid":1.3999,"ask":1.4,"bid_at":NOW,"ask_at":NOW,"market_data_type":1}
        self.base = {"currency":"CAD","nav":140000.,"cash":112000.,"available_funds":98000.,
                     "buying_power":280000.,"total_cash_value":112000.}

    def normalized(self):
        return {**self.snapshot, **usd_amounts(self.base,self.fx,self.policy,NOW),
                "cash_by_currency":{"USD":80000., "CAD":0.},
                "base_currency":"CAD","execution_currency":"USD","base_amounts":deepcopy(self.base),"fx_quote":deepcopy(self.fx)}

    def test_cad_divides_by_ask_and_preserves_originals(self):
        snapshot = self.normalized()
        self.assertAlmostEqual(snapshot["nav"],100000)
        self.assertAlmostEqual(snapshot["cash"],80000)
        self.assertAlmostEqual(snapshot["available_funds"],70000)
        self.assertAlmostEqual(snapshot["buying_power"],200000)
        self.assertEqual(snapshot["base_amounts"],self.base)
        validate_snapshot(snapshot,self.cfg,self.policy,NOW)

    def test_usd_sizing_and_caps_match_equivalent_usd_account(self):
        snapshot = self.normalized()
        plan = build_plan(self.batch,snapshot,self.contracts,self.quotes,self.cfg,self.policy,NOW)
        usd = {**snapshot,"base_currency":"USD"}
        equivalent = build_plan(self.batch,usd,self.contracts,self.quotes,self.cfg,self.policy,NOW)
        self.assertEqual(plan["orders"],equivalent["orders"])
        spent = sum(o["estimated_notional"]+o["estimated_cost"] for o in plan["orders"])
        self.assertLessEqual(spent,69750)
        self.assertTrue(all(o["resulting_estimated_weight"] <= self.policy["max_weight"] for o in plan["orders"]))
        self.assertLessEqual(sum(o["resulting_estimated_weight"] for o in plan["orders"]),self.policy["max_gross"])

    def test_invalid_missing_stale_delayed_and_reversed_fx_fail(self):
        variants = [None,{}, {**self.fx,"symbol":"CAD","currency":"USD"}]
        variants += [{**self.fx,**change} for change in (
            {"bid":0},{"ask":float("nan")},{"ask":float("inf")},{"bid":1.5},
            {"bid":.01,"ask":.010001},{"bid":4,"ask":4.0001},
            {"market_data_type":2},{"market_data_type":3},{"market_data_type":4},
            {"bid_at":"2026-09-22T13:00:00Z"},{"ask_at":"2026-09-22T14:00:00Z"})]
        for fx in variants:
            with self.subTest(fx=fx), self.assertRaises(BrokerSafetyError):
                usd_amounts(self.base,fx,self.policy,NOW)

    def test_cad_cash_valuation_does_not_fund_usd_buys(self):
        snapshot = self.normalized()
        snapshot["cash_by_currency"] = {"CAD":112000., "USD":0.}
        plan = build_plan(self.batch,snapshot,self.contracts,self.quotes,self.cfg,self.policy,NOW)
        self.assertEqual(plan["orders"], [])

    def test_partial_usd_funding_caps_buys_and_revalidation(self):
        snapshot = self.normalized()
        snapshot["cash_by_currency"] = {"CAD":98000., "USD":10000.}
        plan = build_plan(self.batch,snapshot,self.contracts,self.quotes,self.cfg,self.policy,NOW)
        spent = sum(o["estimated_notional"] + o["estimated_cost"] for o in plan["orders"])
        self.assertGreater(spent, 0)
        self.assertLessEqual(spent, 9750.)
        snapshot["cash_by_currency"]["USD"] = 100.
        with self.assertRaisesRegex(BrokerSafetyError, "funded USD"):
            validate_plan(plan,self.batch,snapshot,self.contracts,self.quotes,self.cfg,self.policy,NOW)

    def test_missing_or_negative_currency_cash_fails(self):
        for balances in (None, {}, {"USD":-1}, {"USD":100, "CAD":-1}, {"USD":100, "EUR":1}):
            with self.subTest(balances=balances), self.assertRaises(BrokerSafetyError):
                validate_snapshot({**self.normalized(), "cash_by_currency":balances},self.cfg,self.policy,NOW)

    def test_legacy_and_prefixed_cash_callbacks(self):
        for prefix in ("", "$LEDGER-"):
            values = {("DU123456", prefix+"CashBalance", "CAD"):"140000",
                      ("DU123456", prefix+"CashBalance", "BASE"):"140000"}
            self.assertEqual(currency_cash(values, "DU123456", "CAD"), {"CAD":140000., "USD":0.})
            values[("DU123456", prefix+"CashBalance", "USD")] = "1000"
            self.assertEqual(currency_cash(values, "DU123456", "CAD")["USD"], 1000.)

    def test_conflicting_foreign_missing_and_negative_cash_callbacks_fail(self):
        variants = [{}, {("DU123456", "CashBalance", "USD"):"-1"},
                    {("DU123456", "$LEDGER-CashBalance", "EUR"):"1"},
                    {("DU123456", "CashBalance", "USD"):"1", ("DU123456", "$LEDGER-CashBalance", "USD"):"2"}]
        for values in variants:
            with self.subTest(values=values), self.assertRaises(BrokerSafetyError):
                currency_cash(values, "DU123456", "CAD")

    def test_tampering_and_cad_equals_usd_fail(self):
        for name in ("nav","cash","available_funds","buying_power","total_cash_value"):
            snapshot = self.normalized()
            snapshot[name] = self.base[name]
            with self.subTest(name=name),self.assertRaises(BrokerSafetyError):
                validate_snapshot(snapshot,self.cfg,self.policy,NOW)

    def test_only_exact_paper_endpoint(self):
        for change in ({"host":"localhost"},{"host":"192.168.1.2"},{"port":7496},{"port":4002}):
            with self.subTest(change=change),self.assertRaises(BrokerSafetyError):
                verify_account({**self.cfg,**change},[self.cfg["expected_account"]])

    def test_fx_transport_only_requests_market_data(self):
        from quantlab.data import now_utc
        broker = PaperTWS(self.cfg,self.policy,Mock())
        broker.assert_session = Mock()
        broker.reqMarketDataType = Mock()
        broker.cancelMktData = Mock()
        broker._send_guarded = Mock()
        def quote(req,contract,*args):
            self.assertEqual((contract.symbol,contract.currency,contract.secType,contract.exchange),
                             ("USD","CAD","CASH","IDEALPRO"))
            broker.quotes[req] = {**self.fx,"bid_at":now_utc(),"ask_at":now_utc()}
        broker.reqMktData = Mock(side_effect=quote)
        result = broker.usd_cad_quote()
        self.assertEqual(result["ask"],1.4)
        broker.reqMarketDataType.assert_called_once_with(1)
        broker.cancelMktData.assert_called_once()
        broker._send_guarded.assert_not_called()
        broker.reqMktData = Mock()
        with self.assertRaisesRegex(BrokerSafetyError,"FX unavailable"):
            broker.usd_cad_quote(timeout=0)
        broker._send_guarded.assert_not_called()
