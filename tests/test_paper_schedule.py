"""Requested opening, frozen cadence and read-only scheduling regressions."""
from contextlib import ExitStack, nullcontext
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from quantlab.competition import model_times
from quantlab.data import ROOT
from quantlab.paper import BrokerSafetyError
from quantlab.paper_autonomy import opening_schedule, readiness, scheduled_opening


class OpeningScheduleTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(dir=ROOT / '.cache/tests')))
        (self.root / 'config/model_v1').mkdir(parents=True)
        self.policy = {'rebalance_anchor':'2026-09-22', 'spec':{'cadence':5}}
        (self.root / 'config/model_v1/COMPETITION_V1.json').write_text(json.dumps(self.policy))
        self.stack.enter_context(patch('quantlab.paper_autonomy.ROOT', self.root))
        self.stack.enter_context(patch('quantlab.competition.verify_frozen_policy'))
        self.configure('2026-09-28', None, 'readiness')

    def configure(self, session, batch, mode):
        (self.root / 'config/paper_schedule.json').write_text(json.dumps(dict(session=session,batch=batch,mode=mode)))

    def test_monday_is_open_but_tuesday_is_next_frozen_rebalance(self):
        monday = opening_schedule('2026-09-28')
        self.assertFalse(monday['eligible_rebalance'])
        self.assertEqual(monday['next_rebalance_session'], '2026-09-29')
        self.assertEqual(monday['required_feature_session'], '2026-09-28')
        self.assertEqual(monday['startup_vancouver'], '2026-09-28T05:45:00-07:00')
        self.assertTrue(opening_schedule('2026-09-29')['eligible_rebalance'])
        with self.assertRaisesRegex(BrokerSafetyError, 'five-session'):
            model_times('2026-09-25', '2026-09-26T17:00:00Z', self.policy)
        self.assertEqual(model_times('2026-09-28', '2026-09-28T21:01:00Z', self.policy)['entry_date'], '2026-09-29')

    def test_holiday_and_weekend_are_not_openings(self):
        for day in ('2026-09-26', '2026-11-26'):
            with self.subTest(day=day), self.assertRaisesRegex(BrokerSafetyError, 'exchange session'):
                opening_schedule(day)

    def test_readiness_schedule_never_loads_a_stale_or_latest_batch(self):
        with patch('quantlab.paper_autonomy.load_forecast') as load:
            result = scheduled_opening()
        self.assertEqual(result['task_name'], 'QuantResearch-PAPER-20260928')
        self.assertIsNone(result['batch'])
        load.assert_not_called()

    def test_off_cadence_watch_cannot_be_scheduled(self):
        self.configure('2026-09-28', 'model_old', 'watch')
        with patch('quantlab.paper_autonomy.frozen_batch') as load:
            with self.assertRaisesRegex(BrokerSafetyError, 'cadence'):
                scheduled_opening()
        load.assert_not_called()

    def test_watch_requires_exact_fresh_session_batch(self):
        self.configure('2026-09-29', 'model_old', 'watch')
        with patch('quantlab.paper_autonomy.frozen_batch', return_value=({'entry_at':'2026-09-22T13:30:00+00:00'},{})):
            with self.assertRaisesRegex(BrokerSafetyError, 'batch/session mismatch'):
                scheduled_opening()
        with patch('quantlab.paper_autonomy.frozen_batch', side_effect=BrokerSafetyError('Stale forecast')):
            with self.assertRaisesRegex(BrokerSafetyError, 'Stale'):
                scheduled_opening()

    def test_readiness_cannot_select_batch_and_unknown_mode_fails(self):
        for batch, mode in (('model_old','readiness'), (None,'execute')):
            self.configure('2026-09-28',batch,mode)
            with self.assertRaises(BrokerSafetyError):
                scheduled_opening()

    def test_readiness_reports_off_cadence_missing_batch_and_expiring_tests(self):
        checks = self.root / 'state/paper_checks'
        checks.mkdir(parents=True)
        (checks / 'passed.json').write_text(json.dumps({'at':'2026-09-26T17:00:00Z'}))
        (checks / 'latest-tests.txt').write_text('test_duplicate_watcher_process test_consumed_arm_never_retries test_wire_rejects_changed_config_after_consumption')
        with ExitStack() as stack:
            stack.enter_context(patch('quantlab.paper_cli.require_tested'))
            stack.enter_context(patch('quantlab.paper_autonomy.now_utc', return_value='2026-09-26T17:00:00Z'))
            stack.enter_context(patch('quantlab.paper_autonomy.watcher_lock', side_effect=lambda: nullcontext()))
            stack.enter_context(patch('quantlab.paper_autonomy.connection_config', return_value={
                'host':'127.0.0.1','port':7497,'mode':'PAPER','execution_enabled':False,'client_id':1,'expected_account':'DU123456'}))
            inspect = stack.enter_context(patch('quantlab.paper_autonomy.inspect_connection', side_effect=BrokerSafetyError('No live FX')))
            load = stack.enter_context(patch('quantlab.paper_autonomy.load_forecast'))
            stack.enter_context(patch('subprocess.run', return_value=Mock(returncode=0,stdout=json.dumps({
                'passed':True,'session':'2026-09-28','batch':None}),stderr='')))
            result = readiness()
        self.assertFalse(result['ready'])
        self.assertIsNone(result['batch'])
        for name in ('frozen_rebalance_cadence', 'test_attestation_covers_requested_opening',
                     'COMPETITION_V1_and_exact_frozen_batch_timing_integrity', 'scheduled_execution_mode',
                     'explicit_one_batch_immutable_arm'):
            self.assertEqual(result['checks'][name]['status'], 'FAIL', name)
        self.assertEqual(result['checks']['one_time_Windows_scheduled_task']['status'], 'PASS')
        inspect.assert_called_once_with(None)
        load.assert_not_called()


if __name__ == '__main__':
    unittest.main()
