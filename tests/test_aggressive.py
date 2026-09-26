"""Hand accounting and causality checks for the broader stock sleeve."""
import unittest
import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal
from quantlab.aggressive import feature_panel, ridge_score, simulate_close, top_weights
from quantlab.phase2 import combine_sleeves, window_stats


class AggressiveTests(unittest.TestCase):
    def simple(self, intraday=False, cost=0):
        dates=pd.bdate_range('2020-01-01',periods=3)
        o=pd.DataFrame({'A':[100.,100.,121.]},index=dates)
        c=pd.DataFrame({'A':[100.,110.,133.1]},index=dates)
        w=c*0+1
        return simulate_close(o,c,w,str(dates[1].date()),str(dates[2].date()),cost,intraday=intraday)

    def test_gap_belongs_to_old_holdings_and_pnl_reconciles(self):
        r=self.simple()
        self.assertAlmostEqual(r['daily'].nav.iloc[-1],1.331)
        self.assertAlmostEqual(r['daily'].overnight_pnl_fraction.iloc[-1],.10)
        self.assertAlmostEqual(r['daily'].intraday_pnl_fraction.iloc[-1],.11)
        self.assertAlmostEqual(r['contributions'].overnight_pnl.sum(),.11)
        self.assertAlmostEqual(r['contributions'].intraday_pnl.sum(),.221)

    def test_entry_and_final_exit_fee_on_actual_notionals(self):
        r=self.simple(cost=100)
        self.assertAlmostEqual(r['daily'].nav.iloc[-1],1.331*.99/1.01)
        gross=r['contributions'][['overnight_pnl','intraday_pnl']].to_numpy().sum()
        self.assertAlmostEqual(gross-r['trades'].fees_per_initial_dollar.sum(),r['daily'].nav.iloc[-1]-1)

    def test_intraday_excludes_gap_and_charges_four_legs(self):
        r=self.simple(intraday=True,cost=100)
        self.assertAlmostEqual(r['daily'].nav.iloc[-1],(1.1*.99/1.01)**2)
        self.assertEqual(r['daily'].overnight_pnl_fraction.sum(),0)
        self.assertEqual(len(r['trades']),4)

    def test_signal_at_close_cannot_change_same_open(self):
        dates=pd.bdate_range('2020-01-01',periods=7)
        o=pd.DataFrame(100.,index=dates,columns=['A','B'])
        w=o*0; w['A']=1
        changed=w.copy(); changed.loc[dates[3]:]=[0,1]
        args=(str(dates[1].date()),str(dates[-1].date()),0)
        a=simulate_close(o,o,w,*args,cadence=1)
        b=simulate_close(o,o,changed,*args,cadence=1)
        assert_frame_equal(a['weights'].loc[:dates[3]],b['weights'].loc[:dates[3]])
        self.assertEqual(b['weights'].loc[dates[4],'B'],1)

    def test_intraday_keeps_five_session_ranking_schedule(self):
        dates=pd.bdate_range('2020-01-01',periods=8)
        o=pd.DataFrame(100.,index=dates,columns=['A','B'])
        w=o*0; w['A']=1; w.loc[dates[1]:]=[0,1]
        r=simulate_close(o,o,w,str(dates[1].date()),str(dates[-1].date()),0,intraday=True)
        self.assertTrue((r['weights'].loc[dates[1]:dates[5],'A']==1).all())
        self.assertEqual(r['weights'].loc[dates[6],'B'],1)

    def test_cutoff_ties_share_slots_and_unavailable_stocks_get_zero(self):
        scores=pd.DataFrame([[3.,2.,2.,1.],[1.,1.,1.,1.]])
        eligible=pd.DataFrame([[True]*4,[True,True,False,True]])
        w=top_weights(scores,eligible,2)
        np.testing.assert_allclose(w.iloc[0],[.5,.25,.25,0])
        np.testing.assert_allclose(w.iloc[1],[1/3,1/3,0,1/3])

    def test_holdout_missing_price_and_liquidity_rejected(self):
        dates=pd.bdate_range('2022-12-28',periods=3)
        o=pd.DataFrame(100.,index=dates,columns=['A'])
        args=(str(dates[1].date()),str(dates[-1].date()),5)
        with self.assertRaisesRegex(ValueError,'Liquidity'):
            simulate_close(o,o,o*0+1,*args,adv=o*0+100)
        bad=o.copy(); bad.iloc[1]=np.nan
        with self.assertRaisesRegex(ValueError,'prices'): simulate_close(bad,o,o*0+1,*args)
        future=pd.concat([o,pd.DataFrame(100.,index=pd.DatetimeIndex(['2023-01-03']),columns=['A'])])
        with self.assertRaisesRegex(ValueError,'holdout'): simulate_close(future,future,future*0+1,*args)

    def test_five_session_ridge_purges_future_labels(self):
        rng=np.random.default_rng(14)
        dates=pd.bdate_range('2010-01-01',periods=720)
        columns=list('ABCDEFGHIJ')
        opens=pd.DataFrame(np.exp(np.cumsum(rng.normal(0,.02,(720,10)),axis=0))*100,index=dates,columns=columns)
        names=['momentum','acceleration','breakout','volume_momentum','reversal','gap_continuation','residual_momentum','volatility']
        features={name:pd.DataFrame(rng.normal(size=(720,10)),index=dates,columns=columns) for name in names}
        eligible=opens.notna()
        cfg={'discovery_start':str(dates[100].date()),'discovery_end':str(dates[500].date())}
        a,_,ma=ridge_score(features,eligible,opens,cfg)
        shocked=opens.copy(); shocked.loc[dates[501]:]*=4
        b,_,mb=ridge_score(features,eligible,shocked,cfg)
        self.assertEqual(ma,mb)
        assert_frame_equal(a,b)

    def test_future_bars_cannot_change_stock_features_or_eligibility(self):
        rng=np.random.default_rng(91)
        dates=pd.bdate_range('2010-01-01',periods=420)
        frames=[]
        for symbol in ['A','B','SPY']:
            c=100*np.exp(np.cumsum(rng.normal(0,.01,len(dates))))
            frames.append(pd.DataFrame({'date':dates,'symbol':symbol,'open':c*.999,'close':c,
                          'adj_open':c*.999,'adj_close':c,'high':c*1.02,'low':c*.98,'volume':1e7}))
        bars=pd.concat(frames,ignore_index=True)
        cfg={'min_dollar_volume':2e7}
        before,eligible=feature_panel(bars,['A','B'],cfg)
        shock=bars.copy(); mask=shock.date>=dates[310]
        for field in ['open','close','adj_open','adj_close','high','low','volume']: shock.loc[mask,field]*=3
        after,other=feature_panel(shock,['A','B'],cfg)
        assert_frame_equal(eligible.loc[:dates[309]],other.loc[:dates[309]])
        for key in before: assert_frame_equal(before[key].loc[:dates[309]],after[key].loc[:dates[309]])

    def test_50_50_does_not_silently_rebalance_sleeves(self):
        a=self.simple()
        o={'daily':pd.DataFrame({'nav_before':[1.,.9],'nav':[.9,.99],'exposure':[1.,1.]},index=a['daily'].index)}
        c=combine_sleeves(a,o,500,500)
        self.assertAlmostEqual(c.equity.iloc[-1],1160.5)
        self.assertAlmostEqual(c.aggressive_start.iloc[1],550)
        self.assertAlmostEqual(c.overnight_start.iloc[1],450)
        self.assertAlmostEqual(c.aggressive_pnl.sum()+c.overnight_pnl.sum(),160.5)
        o['daily']=o['daily'].iloc[1:]
        with self.assertRaisesRegex(ValueError,'dates'): combine_sleeves(a,o)

    def test_twenty_session_drawdown_includes_initial_capital(self):
        r=np.r_[-.2,np.zeros(19)]
        s=window_stats(r)
        self.assertAlmostEqual(s['worst_20_drawdown'],-.2)
        self.assertAlmostEqual(s['mean_20'],-.2)
        self.assertEqual(s['prob_20_drawdown_below_minus10'],1)


if __name__=='__main__': unittest.main()
