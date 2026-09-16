"""
Bitget 公共行情 (免 key): rToken 现货 (如 RNVDAUSDT) 与美股 USDT 合约 (如 NVDAUSDT)。

⚠️ 马来西亚本地网络解析 api.bitget.com 会超时 (疑似运营商屏蔽), 需在 AWS 上运行。
   AWS 没装 pandas -> 取数 (candles_raw) 只依赖 requests; 需要 DataFrame 时才用 candles()。

⚠️ 数据语义 (2026-09-14 实测, 未经官方确认):
- RNVDAUSDT 日线可翻到 2018 年 -> rToken 上线前的部分是回填的
- 真实周末交易从 2026-06 开始 (之前每月周末小时线只有个位数)
- 工作日 19:00 UTC 小时收盘与 NVDA 正股收盘中位偏差 0.02% (n=125)
- 工作日成交额量级接近正股在纳斯达克的成交额, 不像 rToken 自身成交 -> 成交量字段别当 rToken 流动性用
"""
from __future__ import annotations

import time

import requests

from ..provenance import Provenance

BASE = "https://api.bitget.com"
PATHS = {"spot": "/api/v2/spot/market/history-candles", "futures": "/api/v2/mix/market/history-candles"}
GRAN = {"spot": {"1h": "1h", "4h": "4h", "1d": "1day"}, "futures": {"1h": "1H", "4h": "4H", "1d": "1D"}}
COLUMNS = ["ts_ms", "open", "high", "low", "close", "base_volume"]


def candles_raw(symbol: str, market: str = "spot", interval: str = "1h", pages: int = 10,
                timeout: float = 20.0, pause: float = 0.12):
    """
    向前翻页取历史 K 线, 不依赖 pandas。
    返回 (rows | None, Provenance); rows 按时间升序, 每行 = COLUMNS。
    """
    url = BASE + PATHS[market]
    gran = GRAN[market][interval]
    params_log = {"symbol": symbol, "market": market, "granularity": gran, "pages": pages}
    t0 = time.time()
    got: dict[int, list] = {}
    try:
        end = int(time.time() * 1000)
        for _ in range(pages):
            p = {"symbol": symbol, "granularity": gran, "endTime": end, "limit": 200}
            if market == "futures":
                p["productType"] = "USDT-FUTURES"
            j = requests.get(url, params=p, timeout=timeout).json()
            if j.get("code") != "00000":
                raise RuntimeError("bitget code=%s msg=%s" % (j.get("code"), j.get("msg")))
            data = j.get("data") or []
            if not data:
                break
            for r in data:
                got[int(r[0])] = r
            new_end = min(int(r[0]) for r in data) - 1
            if new_end >= end:
                break
            end = new_end
            time.sleep(pause)
        rows = [[t] + [float(got[t][i]) for i in range(1, 6)] for t in sorted(got)]
        return rows, Provenance("bitget", url, params_log, time.time(),
                                int((time.time() - t0) * 1000), True, n_records=len(rows))
    except Exception as e:
        return None, Provenance("bitget", url, params_log, time.time(),
                                int((time.time() - t0) * 1000), False,
                                error="%s: %s" % (type(e).__name__, str(e)[:200]))


def to_frame(rows: list[list]):
    """rows -> DataFrame (列: ts(UTC), open, high, low, close, base_volume)。"""
    import pandas as pd
    df = pd.DataFrame(rows, columns=COLUMNS)
    df.insert(0, "ts", pd.to_datetime(df.pop("ts_ms"), unit="ms", utc=True))
    return df


def candles(symbol: str, market: str = "spot", interval: str = "1h", pages: int = 10,
            timeout: float = 20.0, pause: float = 0.12):
    """同 candles_raw, 但返回 DataFrame。"""
    rows, prov = candles_raw(symbol, market, interval, pages, timeout, pause)
    return (to_frame(rows) if rows is not None else None), prov
