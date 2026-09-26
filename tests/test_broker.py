"""Safety tests need no IBKR installation or broker connection."""

import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from quantlab.broker import BrokerSafetyError, PaperOnlyPolicy, read_only_snapshot, submit_order


class PaperBoundaryTests(unittest.TestCase):
    def policy(self, enabled=False):
        return PaperOnlyPolicy("DU123456", frozenset({"DU123456"}), enabled)

    def test_execution_disabled_even_for_verified_account(self):
        with self.assertRaisesRegex(BrokerSafetyError, "disabled"):
            self.policy().authorize_order_account("DU123456", ["DU123456"])

    def test_matching_independently_configured_account(self):
        self.assertEqual(self.policy(True).authorize_order_account("DU123456", ["DU123456"]), "DU123456")

    def test_prefix_and_conventional_port_do_not_authorize(self):
        for port in (7497, 4002):
            with self.subTest(port=port), self.assertRaises(BrokerSafetyError):
                read_only_snapshot(PaperOnlyPolicy("DU123456"), port=port)

    def test_missing_wrong_live_and_ambiguous_managed_accounts_rejected(self):
        for accounts in ([], ["U123456"], ["DU999999"], ["DU123456", "U123456"],
                         ["DU123456", "DU123456"], ["DU123456 "], "DU123456"):
            with self.subTest(accounts=accounts), self.assertRaises(BrokerSafetyError):
                self.policy(True).authorize_order_account("DU123456", accounts)

    def test_two_allowlisted_accounts_still_require_unambiguous_session(self):
        policy = PaperOnlyPolicy("DU123456", frozenset({"DU123456", "DU999999"}), True)
        with self.assertRaises(BrokerSafetyError):
            policy.authorize_order_account("DU123456", ["DU123456", "DU999999"])

    def test_order_cannot_omit_or_override_account(self):
        for account in ("", "U123456", "DU999999", "du123456"):
            with self.subTest(account=account), self.assertRaises(BrokerSafetyError):
                self.policy(True).authorize_order_account(account, ["DU123456"])

    def test_truthy_execution_strings_rejected(self):
        for enabled in ("false", "true", 1, None):
            with self.subTest(enabled=enabled), self.assertRaises(ValueError):
                PaperOnlyPolicy(execution_enabled=enabled)

    def test_allowlist_cannot_be_raw_string_mutable_or_contain_empty_id(self):
        for allowlist in ("DU123456", {"DU123456"}, frozenset({""})):
            with self.subTest(allowlist=allowlist), self.assertRaises(ValueError):
                PaperOnlyPolicy(paper_account_allowlist=allowlist)

    def test_reconnect_to_different_account_fails_new_check(self):
        policy = self.policy(True)
        policy.authorize_order_account("DU123456", ["DU123456"])
        with self.assertRaises(BrokerSafetyError):
            policy.authorize_order_account("DU123456", ["U123456"])

    def test_no_order_transport_even_if_policy_passes(self):
        policy = self.policy(True)
        account = policy.authorize_order_account("DU123456", ["DU123456"])
        with self.assertRaisesRegex(BrokerSafetyError, "not implemented"):
            submit_order(account=account, symbol="SPY", quantity=1)

    def test_read_only_probe_rejects_remote_host_and_client_zero(self):
        for kwargs in ({"host": "example.com"}, {"client_id": 0}, {"timeout": float("nan")},
                       {"port": True}, {"port": 0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                read_only_snapshot(self.policy(), **kwargs)

    def fake_sdk(self, accounts="DU123456", position_account="DU123456", complete=True):
        """A callback fake verifies safety sequencing, without a socket."""
        calls = []

        class Wrapper:
            pass

        class Client:
            def __init__(self, wrapper):
                self.wrapper = wrapper
                self.connected = False

            def connect(self, host, port, clientId):
                calls.append("connect")
                self.connected = True
                self.wrapper.nextValidId(1)

            def run(self):
                pass

            def isConnected(self):
                return self.connected

            def reqManagedAccts(self):
                calls.append("accounts")
                self.wrapper.managedAccounts(accounts)

            def reqPositions(self):
                calls.append("positions")
                contract = SimpleNamespace(conId=123, symbol="SPY", secType="STK", currency="USD")
                self.wrapper.position(position_account, contract, 10, 100)
                if complete:
                    self.wrapper.positionEnd()

            def cancelPositions(self):
                calls.append("cancel_position_subscription")

            def disconnect(self):
                calls.append("disconnect")
                self.connected = False

        package = ModuleType("ibapi")
        client = ModuleType("ibapi.client")
        client.EClient = Client
        wrapper = ModuleType("ibapi.wrapper")
        wrapper.EWrapper = Wrapper
        return patch.dict("sys.modules", {"ibapi": package, "ibapi.client": client, "ibapi.wrapper": wrapper}), calls

    def test_snapshot_reads_verified_paper_account_then_disconnects(self):
        sdk, calls = self.fake_sdk()
        with sdk:
            snapshot = read_only_snapshot(self.policy())
        self.assertEqual(snapshot["positions"][0]["quantity"], "10")
        self.assertEqual(snapshot["transport"], "read_only")
        self.assertFalse(snapshot["execution_enabled"])
        self.assertEqual(calls, ["connect", "accounts", "positions", "cancel_position_subscription", "disconnect"])

    def test_unknown_connected_account_rejected_before_positions(self):
        sdk, calls = self.fake_sdk(accounts="U123456")
        with sdk, self.assertRaises(BrokerSafetyError):
            read_only_snapshot(self.policy())
        self.assertEqual(calls, ["connect", "accounts", "disconnect"])

    def test_snapshot_rejects_positions_from_another_account(self):
        sdk, calls = self.fake_sdk(position_account="U123456")
        with sdk, self.assertRaises(BrokerSafetyError):
            read_only_snapshot(self.policy())
        self.assertEqual(calls[-1], "disconnect")

    def test_incomplete_positions_are_not_accepted_as_complete(self):
        sdk, calls = self.fake_sdk(complete=False)
        with sdk, self.assertRaises(TimeoutError):
            read_only_snapshot(self.policy(), timeout=0.05)
        self.assertEqual(calls[-1], "disconnect")


if __name__ == "__main__":
    unittest.main()
