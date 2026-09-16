"""
支持的标的清单。数据来自 `tools/fetch_rtoken_universe.py`:
Bitget 现货 rToken 全量 -> 逐个用 Yahoo 5 年日线核对 -> 只收录真的取得到行情的。

三个集合, 不要混用:
  SUPPORTED  组合模式能填的标的 (有 Bitget rToken 现货 + Yahoo 有 5 年日线)
  LEVERAGE   其中还能开杠杆的 (Bitget 有对应的 USDT 永续合约; 没有就只能当现货拿)
  CACHED     单笔模式能分析的 (本地存了 rToken 小时线缓存, 才能算周末重锚跳空)

Bitget 的合约清单里股票永续和币永续同名同构 (BCHUSDT 是币, NVDAUSDT 是股), API 没有字段能分开,
所以「有没有永续」只在已确认是股票的标的上判断。
"""
from __future__ import annotations

import glob
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UNIVERSE_PATH = os.path.join(ROOT, "data", "rtoken_universe.json")
CACHE_DIR = os.path.join(ROOT, "data", "cache", "bitget")

# 清单文件缺失时的兜底: 最早接入、且本地有 rToken 小时线缓存的 11 只
FALLBACK = {"NVDA", "TSLA", "AAPL", "SPY", "QQQ", "COIN", "MSTR", "MSFT", "AMZN", "GOOGL", "META"}


def _load(path: str = UNIVERSE_PATH) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


_U = _load()
META: dict[str, dict] = _U.get("symbols", {})
ASOF: str = _U.get("asof_bitget", "")
N_BITGET: int = int(_U.get("n_bitget", 0))

SUPPORTED: frozenset[str] = frozenset(META) or frozenset(FALLBACK)
LEVERAGE: frozenset[str] = frozenset(s for s, m in META.items() if m.get("futures")) or frozenset(FALLBACK)


def _cached() -> frozenset[str]:
    """本地有 rToken 小时线缓存的标的 —— 单笔模式的周末分析要用它, 没有就分析不了。"""
    got = {os.path.basename(p)[1:-len("USDT_spot_1h.json")]
           for p in glob.glob(os.path.join(CACHE_DIR, "R*USDT_spot_1h.json"))}
    return frozenset(got & SUPPORTED) or frozenset(FALLBACK)


CACHED: frozenset[str] = _cached()


def sample(n: int = 6, pool: frozenset[str] | None = None) -> str:
    """给提示语和报错用: 列几个例子, 不要把上千个代码全铺出来。"""
    pool = SUPPORTED if pool is None else pool
    picks = [s for s in ("NVDA", "TSLA", "AAPL", "MSFT", "AMZN", "GOOGL", "META", "SPY", "QQQ", "COIN", "MSTR")
             if s in pool][:n]
    return "、".join(picks or sorted(pool)[:n])


def describe() -> str:
    """一句话说明支持范围 (给界面和提示语)。"""
    return "Bitget 上架的 %d 只美股 rToken (其中 %d 只有永续合约可加杠杆), 例如 %s" % (
        len(SUPPORTED), len(LEVERAGE), sample())


def why_unsupported(symbol: str) -> str:
    s = (symbol or "").upper()
    if s in SUPPORTED:
        return ""
    return "「%s」不在支持范围: %s。BTC、ETH 只能当保证金, 不能开仓。" % (s, describe())
