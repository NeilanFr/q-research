"""Small deterministic tests for causal features, sizing and accounting."""
import unittest
import numpy as np
import pandas as pd
from quantlab.working_model import target_weights,simulate,features


class WorkingModelTests(unittest.TestCase):
    def panel(self):
        idx=pd.bdate_range("2016-01-01",periods=280)
        rng=np.random.default_rng(9)
        rows=[]
        for s in ("AAA","BBB","SPY"):
            close=100*np.cumprod(1+rng.normal(.0002,.01,len(idx)))
            for i,date in enumerate(idx):
                rows.append({"date":date,"symbol":s,"open":close[i]*.999,"adj_open":close[i]*.999,
                             "close":close[i],"adj_close":close[i],"high":close[i]*1.01,"low":close[i]*.99,"volume":1e7})
        cfg={"min_dollar_volume":1e6,"capital":1e6,"max_trade_adv_fraction":.01}
        bars=pd.DataFrame(rows)
        return bars,cfg,features(bars,["AAA","BBB"],cfg)

    def test_future_prices_do_not_change_prior_features(self):
        bars,cfg,p=self.panel()
        cutoff=p["opens"].index[240]
        later=bars.date>cutoff
        for c in ("open","adj_open","close","adj_close","high","low"):
            bars.loc[later,c]*=2
        changed=features(bars,["AAA","BBB"],cfg)
        np.testing.assert_allclose(p["x"][:241],changed["x"][:241],equal_nan=True)

    def test_weight_caps_and_cash_when_few_names(self):
        score=pd.DataFrame([[9,3,-5],[9,np.nan,np.nan]],columns=["A","B","C"])
        w=target_weights(score,score.notna(),score*0+.02,3,"signal",.995,.4)
        self.assertLessEqual(w.to_numpy().max(),.4+1e-10)
        self.assertAlmostEqual(w.iloc[0].sum(),.995)
        self.assertAlmostEqual(w.iloc[1].sum(),.4)

    def test_self_financing_attribution_and_cost_stress(self):
        _,cfg,p=self.panel()
        target=p["closes"]*0+.49
        for night in (False,True):
            sim=simulate(p,target,str(target.index[220].date()),str(target.index[-1].date()),cfg,5,5,night)
            stress=simulate(p,target,str(target.index[220].date()),str(target.index[-1].date()),cfg,10,5,night)
            np.testing.assert_allclose(sim["contributions"].sum(axis=1),sim["daily"].net_return,atol=1e-12)
            self.assertLess(stress["daily"].nav.iloc[-1],sim["daily"].nav.iloc[-1])

    def test_overnight_uses_lagged_close_features_and_correct_return(self):
        _,cfg,p=self.panel()
        p["opens"].iloc[:]=101
        p["closes"].iloc[:]=100
        target=p["closes"]*0
        target.iloc[220,0]=.5
        result=simulate(p,target,str(target.index[222].date()),str(target.index[223].date()),cfg,0,1,True)
        self.assertAlmostEqual(result["daily"].net_return.iloc[0],.005)
        self.assertAlmostEqual(result["daily"].net_return.iloc[1],0)


if __name__=="__main__":
    unittest.main()
