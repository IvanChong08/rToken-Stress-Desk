"""用本机缓存的 Bitget rToken 小时线 + Yahoo 正股日线, 跑周末跳空分析并打印。"""
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from desk.analysis import weekend_gap as wg  # noqa: E402
from desk.provenance import CallLog  # noqa: E402
from desk.sources import yahoo  # noqa: E402
from desk.sources.bitget import to_frame  # noqa: E402

CACHE = os.path.join(ROOT, "data", "cache", "bitget")
log = CallLog(os.path.join(ROOT, "data", "calls_weekend_gap.jsonl"))


def load(symbol):
    path = os.path.join(CACHE, "%s_spot_1h.json" % symbol)
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    return to_frame(d["rows"]), d["provenance"]


def pct(x):
    return "%+.2f%%" % (100 * x)


# ---- 主角: NVDA ----
rt, rprov = load("RNVDAUSDT")
ud, uprov = yahoo.ohlcv("NVDA", "1y", "1d")
log.append(uprov)
print("数据来源:")
print("  rToken  bitget | %s | %s | %d 根" % (rprov["endpoint"], rprov["params"], rprov["n_records"]))
print("  正股    %s" % uprov.label())
recs = wg.weekend_records(ud, rt)
s = wg.summarize(recs)
print("\nNVDA: %d 个周末 (%s ~ %s), 分级 = %s (%s)" % (s["n"], s.get("first_friday"), s.get("last_friday"), s["tier"], wg.TIER_TEXT[s["tier"]]))
print("  周五          周五收盘  周一开盘前rToken  周一开盘   周末漂移   重锚跳空   总跳空   周末最低(多头MAE)")
for r in recs:
    print("  %s  %8.2f  %10.2f  %9.2f  %8s  %8s  %8s  %8s" % (r.friday, r.fri_close, r.pre_open, r.mon_open,
                                                            pct(r.drift), pct(r.reanchor), pct(r.total), pct(r.mae_long)))
if s["n"]:
    print("  中位 |周末漂移| %s · 中位 |重锚跳空| %s (最大 %s) · 中位 |总跳空| %s · 最差多头 MAE %s" % (
        pct(s["median_abs_drift"]), pct(s["median_abs_reanchor"]), pct(s["max_abs_reanchor"]),
        pct(s["median_abs_total"]), pct(s["worst_mae_long"])))
    for lev in (2, 3, 5, 10):
        h = wg.liquidation_hits(recs, lev, "long")
        print("  %2dx 多头: 不利变动阈值 %s -> %d/%d 个周末触及 (维持保证金率按假设 %.0f%%)" % (
            lev, pct(-h["adverse_move_threshold"]), h["hits"], h["n"], 100 * h["maint_margin_assumed"]))

# ---- 其他 rToken 一行汇总 ----
print("\n其他 rToken (2026-06 起的周末):")
for path in sorted(glob.glob(os.path.join(CACHE, "R*_spot_1h.json"))):
    sym = os.path.basename(path).split("_")[0]
    if sym == "RNVDAUSDT":
        continue
    under = sym[1:-4]
    rt2, _ = load(sym)
    ud2, up2 = yahoo.ohlcv(under, "1y", "1d")
    log.append(up2)
    if ud2 is None:
        print("  %-10s Yahoo 取数失败: %s" % (sym, up2.error))
        continue
    rs = wg.weekend_records(ud2, rt2)
    ss = wg.summarize(rs)
    if not ss["n"]:
        print("  %-10s 没有可用周末 (可能不在周末交易名单)" % sym)
        continue
    print("  %-10s n=%2d  中位|漂移| %s  中位|重锚| %s  最大|重锚| %s  最差多头MAE %s  3x多头触及 %d" % (
        sym, ss["n"], pct(ss["median_abs_drift"]), pct(ss["median_abs_reanchor"]), pct(ss["max_abs_reanchor"]),
        pct(ss["worst_mae_long"]), wg.liquidation_hits(rs, 3, "long")["hits"]))
