"""
可执行调整方案: 每个方案都用同一套历史重演重新打分, 给出调整前后对比。计算全部由代码完成, 不经过大模型。

  scale_for_target   所有仓位同比例缩小, 找到让安全总分刚好达到目标分的最大比例 (二分)
  swap_btc_to_usdt   BTC 保证金按现价全部换成 USDT (USDT 按 100% 计入, 不再受 BTC 下跌影响)
  halve_position     把某一笔仓位砍半
"""
from __future__ import annotations

import copy

from . import portfolio as pf
from . import score

TARGET = 60.0     # 「稳健」分界, 与 score.grade 一致


def overall(a: pf.Account, panel: pf.Panel, horizon: int = 5) -> float:
    return score.portfolio_scorecard(a, panel, horizon).overall


def scaled(a: pf.Account, factor: float) -> pf.Account:
    b = copy.deepcopy(a)
    for p in b.positions:
        p.notional *= factor
    return b


def scale_for_target(a: pf.Account, panel: pf.Panel, target: float = TARGET, horizon: int = 5,
                     tol: float = 0.005) -> dict:
    """
    返回 {"status": "ok" | "already" | "infeasible", ...}。
    仓位越小, 仓位亏损和维持保证金都等比例变小, 而抵押品亏损不变 -> 总分随比例单调不增, 可二分。
    infeasible: 仓位缩到接近 0 仍达不到目标 (只剩抵押品自身下跌就不够)。
    """
    cur = overall(a, panel, horizon)
    if cur >= target:
        return {"status": "already", "score": cur}
    lo = 1e-4
    if overall(scaled(a, lo), panel, horizon) < target:
        return {"status": "infeasible", "score": cur}
    hi = 1.0
    while hi - lo > tol:
        mid = (lo + hi) / 2
        if overall(scaled(a, mid), panel, horizon) >= target:
            lo = mid
        else:
            hi = mid
    b = scaled(a, lo)
    return {"status": "ok", "factor": lo, "score_before": cur, "score": overall(b, panel, horizon),
            "gross_before": pf.gross_notional(a), "gross": pf.gross_notional(b), "account": b}


def swap_btc_to_usdt(a: pf.Account, btc_price: float) -> pf.Account:
    b = copy.deepcopy(a)
    b.usdt += b.btc * btc_price
    b.btc = 0.0
    return b


def swap_crypto_to_usdt(a: pf.Account, btc_price: float, eth_price: float) -> pf.Account:
    """BTC 和 ETH 保证金都按现价换成 USDT。"""
    b = copy.deepcopy(a)
    b.usdt += b.btc * btc_price + b.eth * eth_price
    b.btc = 0.0
    b.eth = 0.0
    return b


def halve_position(a: pf.Account, symbol: str, side: str) -> pf.Account:
    b = copy.deepcopy(a)
    for p in b.positions:
        if p.symbol == symbol and p.side == side:
            p.notional /= 2
    return b
