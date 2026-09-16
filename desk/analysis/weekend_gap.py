"""
周末重锚跳空 + 周末持仓压力测试 (rToken 专属风险)。

背景 (Bitget 官方学院): rToken 可 7x24 交易; 美股休市时的价格来自做市商报价 / 用户订单,
美股开盘时 rToken 价格重新锚定到正股实时价格, 可能产生跳空。
实测 (2026-09-14): 真实周末交易从 2026-06 开始 -> 默认只分析这之后的周末。

对每个「周五收盘 -> 周一开盘」(中间恰好隔一个周末; 节假日跳过):
  fri_close        正股周五收盘 (Yahoo 日线)
  pre_open         周一美股开盘所在小时的 rToken 小时线开盘价 = 开盘前能拿到的最后一个 rToken 价
  mon_open         正股周一开盘 (Yahoo 日线)
  weekend_low/high 周五收盘后到周一开盘前, rToken 小时线最低 / 最高价
派生:
  drift      = pre_open / fri_close - 1     周末 rToken 自己走了多少
  reanchor   = mon_open / pre_open - 1      开盘时剩余的重锚跳空
  total      = mon_open / fri_close - 1     正股周五 -> 周一总跳空
  mae_long   = weekend_low / fri_close - 1  周末期间对多头最不利的位置 (rToken 7x24, 周末就可能被强平)
  mae_short  = weekend_high / fri_close - 1 周末期间对空头最不利的位置

开盘 / 收盘时刻用纽约时区换算 (09:30 / 16:00 America/New_York), 自动处理夏令时。
"""
from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass
from datetime import datetime, time as dtime, timezone
from zoneinfo import ZoneInfo

import pandas as pd

NY = ZoneInfo("America/New_York")
WEEKEND_TRADING_SINCE = pd.Timestamp("2026-06-01", tz="UTC")


@dataclass
class WeekendRecord:
    friday: str
    monday: str
    fri_close: float
    pre_open: float
    mon_open: float
    weekend_low: float
    weekend_high: float
    drift: float
    reanchor: float
    total: float
    mae_long: float
    mae_short: float
    n_weekend_bars: int

    def to_dict(self) -> dict:
        return asdict(self)


def _ny_time_utc(day, hh: int, mm: int) -> pd.Timestamp:
    return pd.Timestamp(datetime.combine(day, dtime(hh, mm), tzinfo=NY).astimezone(timezone.utc))


def weekend_records(underlying_daily: pd.DataFrame, rtoken_hourly: pd.DataFrame,
                    since: pd.Timestamp = WEEKEND_TRADING_SINCE) -> list[WeekendRecord]:
    """
    underlying_daily: Yahoo 日线 (列 ts, open, close), ts 为 UTC
    rtoken_hourly:    Bitget rToken 小时线 (列 ts, open, high, low, close), ts 为小时开始时刻 UTC
    """
    u = underlying_daily.copy()
    u["date"] = u["ts"].dt.tz_convert(NY).dt.date
    u = u.drop_duplicates("date", keep="last").set_index("date").sort_index()
    h = rtoken_hourly.set_index("ts").sort_index()
    dates = list(u.index)
    out: list[WeekendRecord] = []
    for fri, mon in zip(dates, dates[1:]):
        if fri.weekday() != 4 or (mon - fri).days != 3:
            continue
        close_utc = _ny_time_utc(fri, 16, 0)
        if close_utc < since:
            continue
        pre_hour = _ny_time_utc(mon, 9, 30).floor("h")
        if pre_hour not in h.index:
            continue
        window = h[(h.index >= close_utc) & (h.index < pre_hour)]
        if window.empty:
            continue
        fc, mo = float(u.loc[fri, "close"]), float(u.loc[mon, "open"])
        po = float(h.loc[pre_hour, "open"])
        lo, hi = float(window["low"].min()), float(window["high"].max())
        out.append(WeekendRecord(str(fri), str(mon), fc, po, mo, lo, hi,
                                 po / fc - 1, mo / po - 1, mo / fc - 1, lo / fc - 1, hi / fc - 1, len(window)))
    return out


def confidence_tier(n: int) -> str:
    """样本量分级: 决定界面怎么呈现, 不让小样本冒充统计结论。"""
    if n >= 30:
        return "stats"
    if n >= 10:
        return "case_study"
    return "tail"


TIER_TEXT = {
    "stats": "样本 ≥30: 可以看统计分布",
    "case_study": "样本 10–29: 逐个列出历史案例, 不做统计推断",
    "tail": "样本 <10: 只能当极端情景参考",
}


def summarize(records: list[WeekendRecord]) -> dict:
    n = len(records)
    if not n:
        return {"n": 0, "tier": "tail"}

    def med(xs):
        return statistics.median(xs)

    return {
        "n": n,
        "tier": confidence_tier(n),
        "median_abs_drift": med([abs(r.drift) for r in records]),
        "median_abs_reanchor": med([abs(r.reanchor) for r in records]),
        "max_abs_reanchor": max(abs(r.reanchor) for r in records),
        "median_abs_total": med([abs(r.total) for r in records]),
        "worst_mae_long": min(r.mae_long for r in records),
        "worst_mae_short": max(r.mae_short for r in records),
        "first_friday": records[0].friday,
        "last_friday": records[-1].friday,
    }


def liquidation_hits(records: list[WeekendRecord], leverage: float, side: str = "long",
                     maint_margin: float = 0.01) -> dict:
    """
    近似强平判定: 周末期间不利变动 ≥ (1/杠杆 - 维持保证金率) 视为触及强平。
    maint_margin 是【用户假设参数】, 不是 Bitget 官方数值 —— 界面必须标注这一点。
    不含手续费、资金费、滑点, 也不含用 BTC 等作抵押时抵押品自身的下跌。
    """
    thr = 1.0 / leverage - maint_margin
    adverse = [(-r.mae_long if side == "long" else r.mae_short) for r in records]
    hits = [r.friday for r, a in zip(records, adverse) if a >= thr]
    return {
        "leverage": leverage, "side": side, "maint_margin_assumed": maint_margin,
        "adverse_move_threshold": thr, "n": len(records), "hits": len(hits), "hit_weekends": hits,
        "worst_adverse": max(adverse) if adverse else None,
    }
