"""Calendar deadlines, frozen state, and first-observed prospective outcomes."""
import json
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import pandas as pd
from quantlab.data import calendar,digest,save_json
from quantlab.experiment import connect_db
from quantlab.phase2_forward import forecast_times,tables,predict,evaluate


class Phase2ForwardTests(unittest.TestCase):
    def setUp(self):
        root=Path(__file__).resolve().parents[1]/'.cache/tests'
        root.mkdir(parents=True,exist_ok=True)
        self.temp=TemporaryDirectory(dir=root); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        for target in ['quantlab.phase2_forward.ROOT','quantlab.experiment.ROOT']:
            patcher=patch(target,self.root); patcher.start(); self.addCleanup(patcher.stop)
        self.policy={'name':'test','execution_enabled':False,'role':'shadow_only','source_hashes':{},
                     'first_aggressive_entry':'2026-09-22','rebalance_every':5,'reference_capital_per_sleeve':500000,
                     'research_config':{'cost_bps':5,'max_trade_adv_fraction':.01},'outcome_convention':'isolated test'}
        save_json(self.root/'config/protocol.json',{'competition_end_assumption':'2026-10-20'})
        self.policy_path=self.root/'config/test.json';save_json(self.policy_path,self.policy)
        with closing(connect_db()) as db:
            tables(db); raw=json.dumps(self.policy,sort_keys=True,allow_nan=False)
            db.execute('INSERT INTO phase2_policies VALUES (?,?,?)',('test',digest(raw.encode()),raw));db.commit()
        self.snapshot=self.prices('2026-09-18','2026-09-19T12:00:00Z')

    def prices(self,end,fetched):
        dates=calendar('2024-01-01',end).sessions_in_range('2024-01-01',end)
        rows=[]
        for symbol in ['SPY','QQQ']:
            rows.append(pd.DataFrame({'date':dates,'symbol':symbol,'open':100.,'close':101.,'adj_open':100.,'adj_close':101.,'volume':10000000.}))
        bars=pd.concat(rows,ignore_index=True)
        meta={'id':end,'created_at_utc':fetched,'raw_sources':[{'symbol':s,'fetched_at_utc':fetched} for s in ['SPY','QQQ']]}
        return bars,meta,self.root

    def make_forecast(self):
        with patch('quantlab.phase2_forward.source_fingerprint',return_value={}),patch('quantlab.phase2_forward.source_archive',return_value='test'),patch('quantlab.phase2_forward.load_snapshot',return_value=self.snapshot),patch('quantlab.phase2_forward.now_utc',return_value='2026-09-21T17:00:00Z'):
            return predict(self.policy_path,'test','overnight')

    def test_overnight_can_be_frozen_before_close_but_not_after_deadline(self):
        t=forecast_times('2026-09-18','2026-09-21T17:00:00Z','overnight',self.policy)
        self.assertEqual(t['entry_date'],'2026-09-21'); self.assertEqual(t['exit_date'],'2026-09-22')
        with self.assertRaisesRegex(ValueError,'Late'):
            forecast_times('2026-09-18','2026-09-21T19:45:00Z','overnight',self.policy)
        with self.assertRaisesRegex(ValueError,'Stale'):
            forecast_times('2026-09-17','2026-09-21T17:00:00Z','overnight',self.policy)

    def test_aggressive_schedule_and_open_cutoff(self):
        t=forecast_times('2026-09-21','2026-09-21T22:00:00Z','aggressive',self.policy)
        self.assertEqual(t['entry_date'],'2026-09-22');self.assertEqual(t['exit_date'],'2026-09-29')
        with self.assertRaisesRegex(ValueError,'scheduled'):
            forecast_times('2026-09-22','2026-09-22T22:00:00Z','aggressive',self.policy)
        with self.assertRaisesRegex(ValueError,'Late'):
            forecast_times('2026-09-21','2026-09-22T13:28:00Z','aggressive',self.policy)

    def test_duplicate_and_modified_policy_are_rejected(self):
        self.make_forecast()
        with self.assertRaisesRegex(ValueError,'Duplicate'):self.make_forecast()
        save_json(self.policy_path,self.policy|{'reference_capital_per_sleeve':1})
        with self.assertRaisesRegex(ValueError,'modified'):self.make_forecast()

    def test_pending_realized_and_revised_data_keep_first_observation(self):
        dest=self.make_forecast()
        with patch('quantlab.phase2_forward.load_snapshot',return_value=self.snapshot),patch('quantlab.phase2_forward.now_utc',return_value='2026-09-21T18:00:00Z'):
            self.assertEqual(evaluate('test')[0]['status'],'pending')
        later=self.prices('2026-09-22','2026-09-22T22:00:00Z')
        with patch('quantlab.phase2_forward.load_snapshot',return_value=later),patch('quantlab.phase2_forward.now_utc',return_value='2026-09-23T10:00:00Z'):
            self.assertEqual(evaluate('test')[0]['status'],'frozen')
        first=(dest/'outcome.json').read_bytes()
        spy=next(r for r in json.loads(first)['results'] if r['model']=='SPY_overnight')
        self.assertAlmostEqual(spy['round_trip_net_return'],(100/101)*(1-.0005)/(1+.0005)-1)
        later[0].loc[later[0].date==pd.Timestamp('2026-09-22'),'adj_open']=200
        with patch('quantlab.phase2_forward.load_snapshot',return_value=later):
            self.assertEqual(evaluate('test')[0]['status'],'already_frozen')
        self.assertEqual((dest/'outcome.json').read_bytes(),first)

    def test_forecast_tampering_is_detected_before_outcome(self):
        dest=self.make_forecast()
        (dest/'position_intents.csv').write_text('changed')
        with patch('quantlab.phase2_forward.load_snapshot',return_value=self.snapshot),self.assertRaisesRegex(ValueError,'modified'):
            evaluate('test')


if __name__=='__main__':unittest.main()
