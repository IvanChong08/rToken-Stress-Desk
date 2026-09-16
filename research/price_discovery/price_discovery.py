"""
探索: rToken 周末价格对周一开盘的预测力 (11 标的, 2026-06 起真实周末交易)。
预测对比:
  naive     周五收盘价 (不看 rToken)
  rtoken_t  周末某时点的 rToken 小时开盘价
误差 = 周一正股开盘 / 预测 - 1
"""
import os
import sys
import statistics as st
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

sys.path.insert(0, r"D:\ivan-agent\rtoken-stress-desk")
from desk.card import load_rtoken_cache  # noqa: E402
from desk.analysis import weekend_gap as wg  # noqa: E402
from desk.idea import SUPPORTED  # noqa: E402
from desk.sources import yahoo  # noqa: E402

SYMS = sorted(SUPPORTED)
with ThreadPoolExecutor(11) as ex:
    ydata = dict(zip(SYMS, ex.map(lambda s: yahoo.ohlcv(s, "1y", "1d"), SYMS)))

rows = []
checkpoints = {  # 相对周一 09:30 NY 开盘所在小时, 往前多少小时
    "周六 12:00 NY 附近 (开盘前约 45h)": 45,
    "周日 20:00 NY 附近 (开盘前约 13h)": 13,
    "周一 04:00 NY 附近 (开盘前约 5h)": 5,
    "开盘前最后 1 小时": 0,
}
for s in SYMS:
    ud, prov = ydata[s]
    if ud is None:
        print("yahoo fail", s, prov.error)
        continue
    rt, _ = load_rtoken_cache(s)
    h = rt.set_index("ts").sort_index()
    for r in wg.weekend_records(ud, rt):
        pre_hour = wg._ny_time_utc(pd.Timestamp(r.monday).date(), 9, 30).floor("h")
        rec = {"sym": s, "friday": r.friday, "F": r.fri_close, "M": r.mon_open,
               "naive_err": r.mon_open / r.fri_close - 1}
        for name, back in checkpoints.items():
            t = pre_hour - pd.Timedelta(hours=back)
            rec[name] = (r.mon_open / float(h.loc[t, "open"]) - 1) if t in h.index else None
        rows.append(rec)

df = pd.DataFrame(rows)
print("样本: %d 个 (标的×周末), %d 个不同周末, %d 个标的" % (len(df), df.friday.nunique(), df.sym.nunique()))
print("周末范围 %s ~ %s\n" % (df.friday.min(), df.friday.max()))


def summ(col):
    x = df[col].dropna()
    naive = df.loc[x.index, "naive_err"]
    sse_ratio = (x ** 2).sum() / (naive ** 2).sum()
    return len(x), st.median(x.abs()), st.median(naive.abs()), 1 - sse_ratio, (x.abs() < naive.abs()).mean()


print("%-34s %4s %10s %10s %8s %8s" % ("预测时点", "n", "中位|误差|", "朴素中位", "R2 vs朴素", "更准比例"))
for c in checkpoints:
    n, me, mn, r2, better = summ(c)
    print("%-34s %4d %9.3f%% %9.3f%% %8.3f %7.1f%%" % (c, n, 100 * me, 100 * mn, r2, 100 * better))

# 方向: rToken 周末变动方向 vs 周五->周一真实方向
c = "开盘前最后 1 小时"
d = df.dropna(subset=[c]).copy()
d["pred_move"] = (d.M / (1 + d[c])) / d.F - 1          # rToken 预开盘价 / 周五收盘 - 1
d["true_move"] = d.naive_err
big = d[d.true_move.abs() >= 0.01]
print("\n|真实周五->周一变动| ≥1%% 的样本 %d 个, 方向一致 %.1f%%" % (len(big), 100 * ((big.pred_move > 0) == (big.true_move > 0)).mean()))
print("全部样本方向一致 %.1f%% (n=%d)" % (100 * ((d.pred_move > 0) == (d.true_move > 0)).mean(), len(d)))

# 按周末聚合: 同一周末各标的高度相关 -> 独立单位是周末
wk = d.groupby("friday").apply(lambda g: pd.Series({
    "n_sym": len(g), "median_abs_err": g[c].abs().median(), "median_abs_naive": g.naive_err.abs().median()}))
print("\n按周末 (独立单位):")
print(wk.to_string(float_format=lambda x: "%.4f" % x))
print("rToken 中位误差 < 朴素中位误差 的周末: %d / %d" % ((wk.median_abs_err < wk.median_abs_naive).sum(), len(wk)))

# 按标的
print("\n按标的:")
bs = d.groupby("sym").apply(lambda g: pd.Series({"n": len(g), "med_err": g[c].abs().median(), "med_naive": g.naive_err.abs().median(),
                                                 "max_err": g[c].abs().max()}))
print(bs.to_string(float_format=lambda x: "%.4f" % x))

# 工作日隔夜: rToken 在美股收盘后 (NY 16:00 ~ 次日 09:30) 是否有真实成交
print("\n工作日隔夜检查 (2026-08-01 起): 小时线根数 / 价格有波动(high>low)的比例 / base_volume 中位")
print("  (成交量字段语义存疑: 工作日量级像正股成交, 只作参考)")
for s in SYMS:
    rt, _ = load_rtoken_cache(s)
    x = rt[rt.ts >= pd.Timestamp("2026-08-01", tz="UTC")].copy()
    ny = x.ts.dt.tz_convert("America/New_York")
    wd = ny.dt.weekday < 5
    hr = ny.dt.hour
    rth = wd & (hr >= 10) & (hr < 16)
    night = wd & ((hr >= 20) | (hr < 4))      # 明确的深夜时段 20:00-04:00 NY (避开盘前盘后)
    wkend = ~wd
    f = lambda m: (int(m.sum()), 100 * (x.loc[m, "high"] > x.loc[m, "low"]).mean(), x.loc[m, "base_volume"].median())  # noqa: E731
    print("  %-6s 盘中 %4d根 波动%5.1f%% 量%10.0f | 深夜 %4d根 波动%5.1f%% 量%10.0f | 周末 %4d根 波动%5.1f%% 量%10.0f" % (
        s, *f(rth), *f(night), *f(wkend)))
