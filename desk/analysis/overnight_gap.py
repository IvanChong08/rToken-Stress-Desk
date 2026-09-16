"""
正股开盘跳空的长历史分布 (Yahoo 5 年日线) —— 给小样本的 rToken 周末分析补尾部参考。

为什么需要: rToken 真实周末交易 2026-06 才开始, 周末样本只有十几个, 而且这段时间行情偏平稳;
正股 5 年里的大跳空 (NVDA ≥10% 共 6 次) 都不在这批周末里。

每个交易日 (相对前一交易日收盘):
  gap        = open / prev_close - 1      开盘跳空
  mae_long   = low  / prev_close - 1      隔夜 + 当日对多头最不利的位置
  mae_short  = high / prev_close - 1      隔夜 + 当日对空头最不利的位置

注意: rToken 7x24 交易, 正股隔夜的变动在 rToken 上是一段连续价格路径 (可能在盘前就触发强平),
所以这里用「前收 -> 当日最低/最高」作为不利变动的历史参考, 而不是只看开盘价。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd


@dataclass
class GapDay:
    date: str
    prev_close: float
    open: float
    low: float
    high: float
    gap: float
    mae_long: float
    mae_short: float

    def to_dict(self) -> dict:
        return asdict(self)


def gap_days(daily: pd.DataFrame) -> list[GapDay]:
    """daily: Yahoo 日线 (列 ts, open, high, low, close)。"""
    d = daily.sort_values("ts").reset_index(drop=True)
    out = []
    for i in range(1, len(d)):
        pc = float(d.loc[i - 1, "close"])
        o, lo, hi = float(d.loc[i, "open"]), float(d.loc[i, "low"]), float(d.loc[i, "high"])
        out.append(GapDay(str(d.loc[i, "ts"].date()), pc, o, lo, hi, o / pc - 1, lo / pc - 1, hi / pc - 1))
    return out


def tail_counts(days: list[GapDay], thresholds=(0.05, 0.10, 0.15)) -> dict:
    return {
        "n_days": len(days),
        "first": days[0].date if days else None,
        "last": days[-1].date if days else None,
        "abs_gap_ge": {t: sum(abs(x.gap) >= t for x in days) for t in thresholds},
        "down_gap_ge": {t: sum(x.gap <= -t for x in days) for t in thresholds},
    }


def worst_days(days: list[GapDay], side: str = "long", k: int = 5) -> list[GapDay]:
    key = (lambda x: x.mae_long) if side == "long" else (lambda x: -x.mae_short)
    return sorted(days, key=key)[:k]


def leverage_breaches(days: list[GapDay], leverage: float, side: str = "long",
                      maint_margin: float = 0.01) -> dict:
    """
    近似强平判定: 不利变动 ≥ (1/杠杆 - 维持保证金率)。
    maint_margin 是【用户假设参数】, 不是 Bitget 官方数值。不含手续费、资金费、滑点。
    """
    thr = 1.0 / leverage - maint_margin
    adverse = [(-x.mae_long if side == "long" else x.mae_short) for x in days]
    hits = [x.date for x, a in zip(days, adverse) if a >= thr]
    return {"leverage": leverage, "side": side, "maint_margin_assumed": maint_margin,
            "adverse_move_threshold": thr, "n_days": len(days), "hits": len(hits), "hit_dates": hits}
