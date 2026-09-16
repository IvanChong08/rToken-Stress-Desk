"""正股 5 年开盘跳空与隔夜不利变动: 给 rToken 周末小样本补尾部参考。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from desk.analysis import overnight_gap as og  # noqa: E402
from desk.provenance import CallLog  # noqa: E402
from desk.sources import yahoo  # noqa: E402

log = CallLog(os.path.join(ROOT, "data", "calls_overnight_gap.jsonl"))


def pct(x):
    return "%+.2f%%" % (100 * x)


for sym in ("NVDA", "TSLA", "MSTR", "COIN", "SPY"):
    df, prov = yahoo.ohlcv(sym, "5y", "1d")
    log.append(prov)
    if df is None:
        print("%s 取数失败: %s" % (sym, prov.error))
        continue
    days = og.gap_days(df)
    t = og.tail_counts(days)
    print("\n%s  %s ~ %s, %d 个交易日  (来源: %s)" % (sym, t["first"], t["last"], t["n_days"], prov.label()))
    print("  |开盘跳空| ≥5%%: %d 次 · ≥10%%: %d 次 · ≥15%%: %d 次;  向下跳空 ≥10%%: %d 次" % (
        t["abs_gap_ge"][0.05], t["abs_gap_ge"][0.10], t["abs_gap_ge"][0.15], t["down_gap_ge"][0.10]))
    print("  对多头最不利的 5 天 (前收 -> 当日最低):", ", ".join("%s %s(开盘跳空 %s)" % (w.date, pct(w.mae_long), pct(w.gap))
                                                     for w in og.worst_days(days, "long", 5)))
    print("  隔夜+当日触及近似强平线 (维持保证金率假设 1%):", " · ".join(
        "%dx %d 次" % (lev, og.leverage_breaches(days, lev, "long")["hits"]) for lev in (2, 3, 5, 10)))
