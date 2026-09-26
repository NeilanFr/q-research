"""Synthetic PAPER commissioning, durable authorization and watcher regressions."""
from contextlib import ExitStack
from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import pandas as pd

from quantlab.data import ROOT, digest
from quantlab.paper import BrokerSafetyError, PaperLedger, build_plan, canonical
from quantlab.paper_autonomy import (ArmStore, check_wire_arm, consume_for_transmission,
    no_unresolved, require_active_arm, validate_arm, watch, watcher_lock, window)
from test_paper_execution import fixtures, NOW


class AutonomyTests(unittest.TestCase):
    def setUp(self):
        (ROOT / '.cache/tests').mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.cache/tests')
        self.root = Path(self.temp.name)
        self.ledger = PaperLedger(self.root / 'paper.sqlite')
        self.store = ArmStore(self.ledger)
        self.cfg, self.policy, self.batch, self.snapshot, self.contracts, self.quotes = fixtures()
        self.plan = build_plan(self.batch, self.snapshot, self.contracts, self.quotes, self.cfg, self.policy, NOW)
        start, end = window(self.batch, self.policy)
        self.value = {'forecast_batch':self.batch['id'], 'model_version':self.batch['version'],
            'account_sha256':digest(self.cfg['expected_account'].encode()), 'entry_at':self.batch['entry_at'],
            'forecast_sha256':digest(canonical(self.batch).encode()), 'armed_at':'2026-09-22T03:00:00+00:00',
            'window_start':start.isoformat(), 'window_end':end.isoformat(),
            'expires_at':(end+pd.Timedelta(seconds=30)).isoformat(), 'source_sha256':'tested',
            'read_only_api_disabled_confirmed':True}
        self.stack = ExitStack()
        self.stack.enter_context(patch('quantlab.paper_autonomy.now_utc', return_value=NOW))
        self.stack.enter_context(patch('quantlab.paper_cli.require_tested'))
        self.stack.enter_context(patch('quantlab.paper_cli.source_fingerprint', return_value='tested'))
        self.stack.enter_context(patch('quantlab.paper_autonomy.connection_config', side_effect=lambda:deepcopy(self.cfg)))
        self.broker = SimpleNamespace(ledger=self.ledger, cfg={**self.cfg,'execution_enabled':True},
                                      policy=self.policy, arm_authorization=None)

    def tearDown(self):
        self.stack.close()
        self.ledger.close()
        self.temp.cleanup()

    def test_immutable_arm_and_terminal_rows(self):
        record = self.store.create(self.value)
        self.assertEqual(self.store.get(self.batch['id']),record)
        for sql in ('UPDATE paper_arms SET payload=payload', 'DELETE FROM paper_arms'):
            with self.assertRaises(sqlite3.IntegrityError):
                self.ledger.db.execute(sql)
            self.ledger.db.rollback()
        self.store.finish(record,'EXPIRED')
        for sql in ('UPDATE paper_arm_terminal SET kind=kind', 'DELETE FROM paper_arm_terminal'):
            with self.assertRaises(sqlite3.IntegrityError):
                self.ledger.db.execute(sql)
            self.ledger.db.rollback()
        with self.assertRaises(BrokerSafetyError):
            self.store.create(self.value)

    def test_arm_creation_never_creates_orders(self):
        with patch('quantlab.tws.EClient.placeOrder') as wire:
            self.store.create(self.value)
            wire.assert_not_called()
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM orders').fetchone()[0],0)

    def test_arm_command_inspects_without_order_transport(self):
        from quantlab.paper_autonomy import arm
        evidence = {'source_sha256':'tested', 'snapshot':self.snapshot}
        with ExitStack() as stack:
            stack.enter_context(patch('quantlab.paper_autonomy.ROOT',self.root))
            stack.enter_context(patch('quantlab.paper_autonomy.now_utc',return_value='2026-09-22T03:00:00Z'))
            stack.enter_context(patch('quantlab.paper_autonomy.frozen_batch',return_value=(self.batch,self.policy)))
            stack.enter_context(patch('quantlab.paper_autonomy.PaperLedger',return_value=self.ledger))
            stack.enter_context(patch.object(self.ledger,'close'))
            inspect = stack.enter_context(patch('quantlab.paper_autonomy.inspect_connection',return_value=evidence))
            stack.enter_context(patch('builtins.input',return_value='ARM PAPER'))
            stack.enter_context(patch('builtins.print'))
            wire = stack.enter_context(patch('quantlab.tws.EClient.placeOrder'))
            record = arm(self.batch['id'])
            inspect.assert_called_once_with(self.batch['id'],self.ledger)
            wire.assert_not_called()
        self.assertNotIn(self.cfg['expected_account'],canonical(record))
        self.assertEqual(self.store.get(self.batch['id']),record)

    def test_adapter_consumes_arm_before_wire_and_rejects_replay(self):
        from quantlab.tws import PaperTWS
        from ibapi.contract import Contract
        broker = PaperTWS({**self.cfg,'execution_enabled':True},self.policy,self.ledger)
        broker.accounts, broker.healthy, broker.next_id = [self.cfg['expected_account']], True, 100
        broker.resolved, broker.resolved_objects = self.contracts, {s:Contract() for s in self.contracts}
        self.store.create(self.value)
        previews = [{'at':NOW,'plan_id':self.plan['id'],'order_key':o['key'],'what_if':True} for o in self.plan['orders']]
        for p in previews:
            self.ledger.observe('what_if_passed',p)
        with ExitStack() as stack:
            stack.enter_context(patch.object(broker,'isConnected',return_value=True))
            stack.enter_context(patch.object(broker,'clock_check'))
            stack.enter_context(patch.object(broker,'current_quotes',return_value=self.quotes))
            stack.enter_context(patch('quantlab.tws.now_utc',return_value=NOW))
            stack.enter_context(patch('quantlab.tws.load_forecast',return_value=self.batch))
            def wire(obj,order_id,contract,order):
                self.assertEqual(self.store.terminal(self.store.get(self.batch['id']))['kind'],'CONSUMED')
                event = self.ledger.db.execute('SELECT kind FROM order_events WHERE order_id=? ORDER BY seq DESC LIMIT 1',(order_id,)).fetchone()
                self.assertEqual(event[0],'SUBMITTING')
                self.assertFalse(order.whatIf)
            spy = stack.enter_context(patch('quantlab.tws.EClient.placeOrder',autospec=True,side_effect=wire))
            broker.transmit_batch(self.plan,self.batch,self.snapshot,previews)
            self.assertEqual(spy.call_count,len(self.plan['orders']))
            with self.assertRaisesRegex(BrokerSafetyError,'consumed'):
                broker.transmit_batch(self.plan,self.batch,self.snapshot,previews)
            self.assertEqual(spy.call_count,len(self.plan['orders']))

    def test_second_active_arm_is_rejected(self):
        self.store.create(self.value)
        with self.assertRaisesRegex(BrokerSafetyError,'existing'):
            self.store.create({**self.value,'forecast_batch':'some_other_batch'})

    def test_consumed_arm_never_retries(self):
        self.store.create(self.value)
        self.broker.arm_authorization = consume_for_transmission(self.broker,self.batch,self.plan,self.snapshot)
        check_wire_arm(self.broker,self.batch,self.plan)
        with self.assertRaisesRegex(BrokerSafetyError,'consumed'):
            consume_for_transmission(self.broker,self.batch,self.plan,self.snapshot)
        restarted = PaperLedger(self.root / 'paper.sqlite')
        try:
            with self.assertRaisesRegex(BrokerSafetyError,'consumed'):
                require_active_arm(restarted,self.batch,self.cfg,self.policy)
        finally:
            restarted.close()

    def test_arm_expiry_no_catch_up(self):
        record = self.store.create(self.value)
        self.store.expire(self.value['expires_at'])
        self.assertEqual(self.store.terminal(record)['kind'],'EXPIRED')
        with self.assertRaisesRegex(BrokerSafetyError,'expired'):
            validate_arm(record,self.cfg,self.batch,self.policy,self.value['expires_at'])

    def test_source_config_account_batch_changes_fail_closed(self):
        record = self.store.create(self.value)
        variants = [{'source_sha256':'changed'}, {'account_sha256':'other'},
                    {'forecast_batch':'other'}, {'model_version':'changed'}, {'forecast_sha256':'changed'},
                    {'entry_at':'2026-09-23T13:30:00Z'}, {'window_end':self.value['entry_at']},
                    {'read_only_api_disabled_confirmed':False}]
        for variant in variants:
            with self.subTest(variant=variant), self.assertRaises(BrokerSafetyError):
                validate_arm({**record,**variant},self.cfg,self.batch,self.policy,NOW)

    def test_wire_rejects_changed_config_after_consumption(self):
        self.store.create(self.value)
        self.broker.arm_authorization = consume_for_transmission(self.broker,self.batch,self.plan,self.snapshot)
        self.cfg['port'] = 4002
        with self.assertRaisesRegex(BrokerSafetyError,'config mismatch'):
            check_wire_arm(self.broker,self.batch,self.plan)

    def test_early_and_late_consumption_never_enables_wire(self):
        self.store.create(self.value)
        for now in ('2026-09-22T12:45:00Z','2026-09-22T13:25:00Z'):
            with patch('quantlab.paper_autonomy.now_utc',return_value=now), self.assertRaisesRegex(BrokerSafetyError,'window'):
                consume_for_transmission(self.broker,self.batch,self.plan,self.snapshot)
        self.assertIsNone(self.store.terminal(self.store.get(self.batch['id'])))

    def test_unresolved_reserved_or_partial_orders_block_arm_consumption(self):
        self.ledger.reserve(self.plan,721,100)
        self.store.create(self.value)
        with self.assertRaisesRegex(BrokerSafetyError,'Unresolved'):
            consume_for_transmission(self.broker,self.batch,self.plan,self.snapshot)
        self.assertIsNone(self.store.terminal(self.store.get(self.batch['id'])))

    def test_no_arm_is_never_authorization(self):
        with self.assertRaisesRegex(BrokerSafetyError,'No explicit'):
            require_active_arm(self.ledger,self.batch,self.cfg,self.policy)
        with self.assertRaisesRegex(BrokerSafetyError,'requires a consumed arm'):
            check_wire_arm(self.broker,self.batch,self.plan)

    def test_duplicate_watcher_process(self):
        lock_path = self.root / 'watcher.lock'
        code = 'import sys; from pathlib import Path; from quantlab.paper_autonomy import watcher_lock;\nwith watcher_lock(Path(sys.argv[1])): pass'
        with watcher_lock(lock_path):
            result = subprocess.run([sys.executable,'-c',code,str(lock_path)],cwd=ROOT,capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('already holds',result.stderr)
        result = subprocess.run([sys.executable,'-c',code,str(lock_path)],cwd=ROOT,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def watch_fixture(self):
        fake = Mock()
        fake.cfg = self.cfg
        fake.snapshot.return_value = self.snapshot
        self.stack.enter_context(patch('quantlab.paper_autonomy.ROOT',self.root))
        self.stack.enter_context(patch('quantlab.paper_autonomy.PaperLedger',return_value=self.ledger))
        self.stack.enter_context(patch.object(self.ledger,'close'))
        self.stack.enter_context(patch('quantlab.paper_autonomy.load_forecast',return_value=self.batch))
        self.stack.enter_context(patch('quantlab.paper_autonomy.execution_policy',return_value=self.policy))
        self.stack.enter_context(patch('quantlab.paper_autonomy.frozen_batch',return_value=(self.batch,self.policy)))
        self.stack.enter_context(patch('quantlab.tws.PaperTWS',return_value=fake))
        self.stack.enter_context(patch('quantlab.paper_autonomy.time.sleep'))
        self.stack.enter_context(patch('builtins.print'))
        return fake

    def test_monitor_only_in_window_never_previews_or_executes(self):
        fake = self.watch_fixture()
        with patch('quantlab.paper_autonomy.time.monotonic',side_effect=[0,2]), patch('quantlab.paper_cli.commission') as execute:
            result = watch(self.batch['id'],monitor_only=True,max_seconds=1)
        execute.assert_not_called()
        fake.what_if.assert_not_called()
        fake.transmit_batch.assert_not_called()
        self.assertEqual(result['successful_checks'],1)

    def test_watcher_waits_before_window(self):
        self.store.create(self.value)
        fake = self.watch_fixture()
        self.snapshot['observed_at'] = '2026-09-22T12:45:00Z'
        with patch('quantlab.paper_autonomy.now_utc',return_value='2026-09-22T12:45:00Z'), \
             patch('quantlab.paper_autonomy.time.sleep',side_effect=KeyboardInterrupt), \
             patch('quantlab.paper_cli.commission') as execute:
            with self.assertRaises(KeyboardInterrupt):
                watch(self.batch['id'])
        execute.assert_not_called()
        state = json.loads((self.root/'state/paper_checks/watcher.json').read_text())
        self.assertEqual(state['status'],'armed; waiting for frozen opening window')

    def test_ambiguous_network_attempt_is_consumed_then_only_reconciles(self):
        self.store.create(self.value)
        self.watch_fixture()
        def ambiguous(*args,**kwargs):
            consume_for_transmission(self.broker,self.batch,self.plan,self.snapshot)
            raise ConnectionError('ambiguous network send')
        with patch('quantlab.paper_autonomy.time.sleep',side_effect=[None,KeyboardInterrupt]), \
             patch('quantlab.paper_cli.commission',side_effect=ambiguous) as execute:
            with self.assertRaises(KeyboardInterrupt):
                watch(self.batch['id'])
        self.assertEqual(execute.call_count,1)
        state = json.loads((self.root/'state/paper_checks/watcher.json').read_text())
        self.assertEqual(state['status'],'disarmed; reconciliation only')

    def test_connection_failure_never_executes(self):
        self.store.create(self.value)
        fake = self.watch_fixture()
        fake.start.side_effect = BrokerSafetyError('Disconnected')
        with patch('quantlab.paper_autonomy.time.monotonic',side_effect=[0,2]), patch('quantlab.paper_cli.commission') as execute:
            with self.assertRaisesRegex(BrokerSafetyError,'startup test failed'):
                watch(self.batch['id'],monitor_only=True,max_seconds=1)
        execute.assert_not_called()


if __name__ == '__main__':
    unittest.main()
