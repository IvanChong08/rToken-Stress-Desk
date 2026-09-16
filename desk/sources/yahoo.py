"""
美股 / ETF 历史行情 (Yahoo Finance chart 接口, 免 key)。
2026-09-14 实测: 本机与 AWS 均可直连, NVDA 5 年日线 1,255 根约 0.2 秒。
"""
from __future__ import annotations

import time

import pandas as pd
import requests

from ..provenance import Provenance

URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"


def ohlcv(symbol: str, range_: str = "5y", interval: str = "1d", timeout: float = 20.0):
    """返回 (DataFrame | None, Provenance)。列: ts(UTC), open, high, low, close, volume。"""
    url = URL.format(symbol=symbol)
    params = {"range": range_, "interval": interval}
    t0 = time.time()
    try:
        r = requests.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout)
        r.raise_for_status()
        res = r.json()["chart"]["result"][0]
        q = res["indicators"]["quote"][0]
        df = pd.DataFrame({
            "ts": pd.to_datetime(res["timestamp"], unit="s", utc=True),
            "open": q["open"], "high": q["high"], "low": q["low"], "close": q["close"], "volume": q["volume"],
        }).dropna(subset=["open", "close"]).reset_index(drop=True)
        return df, Provenance("yahoo", url, {"symbol": symbol, **params}, time.time(),
                              int((time.time() - t0) * 1000), True, n_records=len(df))
    except Exception as e:  # 失败要留痕, 不静默
        return None, Provenance("yahoo", url, {"symbol": symbol, **params}, time.time(),
                                int((time.time() - t0) * 1000), False,
                                error="%s: %s" % (type(e).__name__, str(e)[:200]))
