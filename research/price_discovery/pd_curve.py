"""按「距周一开盘小时数」画 rToken 预测力曲线: 找出预测力在哪个时点跳升。"""
import os
import sys
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

sys.path.insert(0, r"D:\ivan-agent\rtoken-stress-desk")
from desk.card import load_rtoken_cache  # noqa: E402
from desk.analysis import weekend_gap as wg  # noqa: E402
from desk.idea import TRADE_SUPPORTED  # noqa: E402   # 有 rToken 小时线缓存的那 11 只
from desk.sources import yahoo  # noqa: E402

SYMS = sorted(TRADE_SUPPORTED)
with ThreadPoolExecutor(11) as ex:
    yd = dict(zip(SYMS, ex.map(lambda s: yahoo.ohlcv(s, "1y", "1d"), SYMS)))

recs = []
for s in SYMS:
    ud, prov = yd[s]
    rt, _ = load_rtoken_cache(s)
    h = rt.set_index("ts").sort_index()
    for r in wg.weekend_records(ud, rt):
        pre = wg._ny_time_utc(pd.Timestamp(r.monday).date(), 9, 30).floor("h")
        for back in range(0, 66):
            t = pre - pd.Timedelta(hours=back)
            # 用「≤t 的最近一根」的收盘价, 避免周末无成交小时缺根导致样本偏
            past = h[h.index <= t]
            if past.empty or past.index[-1] < wg._ny_time_utc(pd.Timestamp(r.friday).date(), 16, 0):
                continue
            px = float(past["close"].iloc[-1]) if back > 0 else float(h.loc[t, "open"])
            recs.append({"sym": s, "fri": r.friday, "back": back,
                         "ny": t.tz_convert("America/New_York").strftime("%a %H:%M"),
                         "err": r.mon_open / px - 1, "naive": r.mon_open / r.fri_close - 1})
df = pd.DataFrame(recs)
out = df.groupby("back").apply(lambda g: pd.Series({
    "ny": g.ny.iloc[0], "n": len(g),
    "R2": 1 - (g.err ** 2).sum() / (g.naive ** 2).sum(),
    "med_err%": 100 * g.err.abs().median(),
    "wk_better": g.groupby("fri").apply(lambda w: w.err.abs().median() < w.naive.abs().median()).mean()}))
print(out.to_string(float_format=lambda x: "%.3f" % x))
