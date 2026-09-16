"""
在 AWS 上运行 (本机连不上 api.bitget.com): 抓 rToken / 美股合约 K 线, 连同来源记录存成 JSON,
再拉回本机做分析。只依赖标准库 + requests (AWS 没装 pandas)。

    python3 tools/fetch_bitget_cache.py

输出:
    data/cache/bitget/<symbol>_<market>_<interval>.json   {"provenance": ..., "columns": [...], "rows": [...]}
    data/calls_fetch_bitget.jsonl                         每次调用的来源记录 (哈希链)
"""
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from desk.provenance import CallLog  # noqa: E402
from desk.sources.bitget import COLUMNS, candles_raw  # noqa: E402

OUT = os.path.join(ROOT, "data", "cache", "bitget")
WEEKEND_TRADING_SINCE = (2026, 6)   # 实测真实周末交易从 2026-06 开始

# 45 页 x 200 根 ≈ 2025-03 起; 其余 20 页 ≈ 5.5 个月, 足够覆盖 6 月以来的全部周末
JOBS = [("RNVDAUSDT", "spot", "1h", 45)] + [
    (s, "spot", "1h", 20) for s in (
        "RTSLAUSDT", "RAAPLUSDT", "RSPYUSDT", "RQQQUSDT", "RCOINUSDT",
        "RMSTRUSDT", "RMSFTUSDT", "RAMZNUSDT", "RGOOGLUSDT", "RMETAUSDT")
] + [("NVDAUSDT", "futures", "1h", 20)]


def fmt(ms: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000))


def weekend_hours_since(rows, since=WEEKEND_TRADING_SINCE) -> int:
    n = 0
    for r in rows:
        g = time.gmtime(r[0] / 1000)
        if g.tm_wday >= 5 and (g.tm_year, g.tm_mon) >= since:
            n += 1
    return n


def main():
    os.makedirs(OUT, exist_ok=True)
    log = CallLog(os.path.join(ROOT, "data", "calls_fetch_bitget.jsonl"))
    for sym, market, interval, pages in JOBS:
        rows, prov = candles_raw(sym, market, interval, pages=pages)
        log.append(prov)
        if rows is None or not rows:
            print("FAIL %-11s %s" % (sym, prov.error or "no rows"))
            continue
        path = os.path.join(OUT, "%s_%s_%s.json" % (sym, market, interval))
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"provenance": prov.to_dict(), "columns": COLUMNS, "rows": rows}, f)
        print("OK   %-11s %-7s %s %5d 根  %s ~ %s  2026-06 起周末小时 %4d  (%d ms)" % (
            sym, market, interval, len(rows), fmt(rows[0][0]), fmt(rows[-1][0]),
            weekend_hours_since(rows), prov.latency_ms))


if __name__ == "__main__":
    main()
