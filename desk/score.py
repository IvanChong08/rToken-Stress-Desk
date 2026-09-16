"""
打分: 安全分 + 证据可信度。两者分开展示, 互不影响。

安全分 (每项 0-100, 公式公开, 没有人为权重):
  每项得分 = 100 x (1 - 情景亏损 / 开仓时离强平的距离), 截断到 [0, 100]; 情景中触及强平 = 0
  「离强平的距离」: 组合 = 初始权益 - 初始维持保证金; 单笔 = 近似强平线 (1/杠杆 - 维持保证金率)
总分 = 各项中最低的一项 (短板决定风险; 取平均会把致命风险稀释掉)。

证据可信度: 不打分, 逐项列出 ok / warn —— 样本量、数据源调用是否成功、数据新鲜度、用了哪些用户假设参数。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date

from . import portfolio as pf
from .analysis.weekend_gap import confidence_tier

SCORE_RULE = ("每项得分 = 100 × (1 − 情景亏损 ÷ 开仓时离强平的距离), 截断到 0–100, 情景中触及强平记 0 分; "
              "总分取最低的一项 (短板决定风险, 不取平均)。")
SCORE_RULE_SPOT = ("全是现货, 不会被强平: 每项得分 = 100 × (1 − 情景亏损 ÷ 初始权益), 截断到 0–100; "
                   "总分取最低的一项 (短板决定风险, 不取平均)。")


@dataclass
class ScoreItem:
    key: str
    label: str
    score: float
    basis: str               # 这一项用什么数据算的 (给界面展示)
    detail: str              # 例: 「2024-08-05 · 亏损用掉 59.0% 的强平距离」
    liquidated: bool = False


@dataclass
class Evidence:
    label: str
    status: str              # ok | warn
    text: str


@dataclass
class Scorecard:
    overall: float
    weakest: str             # 最低一项的 key
    weakest_label: str
    items: list[ScoreItem]
    evidence: list[Evidence] = field(default_factory=list)
    rule: str = SCORE_RULE

    def to_dict(self) -> dict:
        return asdict(self)


def from_buffer_used(buffer_used: float, liquidated: bool = False) -> float:
    if liquidated:
        return 0.0
    return max(0.0, min(100.0, 100.0 * (1.0 - buffer_used)))


def grade(score: float) -> str:
    """只用于配色/文字提示, 分界公开写明。"""
    return "危险" if score < 30 else ("警惕" if score < 60 else "稳健")


def _overall(items: list[ScoreItem], evidence: list[Evidence]) -> Scorecard:
    w = min(items, key=lambda x: x.score)
    return Scorecard(w.score, w.key, w.label, items, evidence)


# ---------------------------------------------------------------- 组合

def portfolio_scorecard(a: pf.Account, panel: pf.Panel, horizon: int = 5, today: date | None = None) -> Scorecard:
    e0, m0 = pf.equity0(a, panel.btc_price, panel.eth_price), pf.maint0(a)
    buf = e0 - m0
    items: list[ScoreItem] = []
    # 全是现货时维持保证金为 0, 分母退化成初始权益 —— 分数照算, 但不能再说「强平距离」, 现货压根不会被强平
    spot_only = bool(a.positions) and all(p.kind == "spot" for p in a.positions)
    used = (lambda pct: "亏掉 %.1f%% 的本金" % pct) if spot_only else (lambda pct: "亏损用掉 %.1f%% 的强平距离" % pct)

    rep = pf.replay(a, panel, "extreme")
    worst = rep.iloc[0]
    n_liq = int(rep.liquidated.sum())
    items.append(ScoreItem(
        "single_day", "单日极端行情", from_buffer_used(float(worst.buffer_used), n_liq > 0),
        "5 年逐日重演, 各资产取当日最不利价格 (同时发生, 偏保守)",
        ("%d 个交易日里有 %d 天触及强平" % (len(rep), n_liq)) if n_liq else
        "最差 %s · %s" % (worst.date, used(100 * worst.buffer_used)), n_liq > 0))

    md = pf.multi_day_worst(a, panel, horizon)
    mw = md.iloc[0]
    md_liq = int(md.liquidated.sum())
    md_bu = (e0 - float(mw.equity)) / buf if buf > 0 else float("inf")
    items.append(ScoreItem(
        "multi_day", "连续 %d 日下跌" % horizon, from_buffer_used(md_bu, md_liq > 0),
        "5 年里每个起点持有 1–%d 个交易日, 收盘价口径, 不调仓" % horizon,
        ("%d 个起点触及强平" % md_liq) if md_liq else
        "最差 %s 起持有 %d 天 · %s" % (mw.start, int(mw.days), used(100 * md_bu)), md_liq > 0))

    pres = [(p, pf.shock(a, panel.btc_price, pf.preset_moves(a, p), panel.eth_price)) for p in pf.PRESETS]
    pw, rw = min(pres, key=lambda x: from_buffer_used(x[1].buffer_used, x[1].liquidated))
    items.append(ScoreItem(
        "presets", "预设双重打击情景", from_buffer_used(rw.buffer_used, rw.liquidated),
        "%d 个假设情景中最差的一个 (情景参数是假设, 不是预测)" % len(pf.PRESETS),
        "最差「%s」· %s" % (pw["name"], "触及强平" if rw.liquidated else used(100 * rw.buffer_used)),
        rw.liquidated))

    ev = [Evidence("历史样本", "ok" if confidence_tier(len(rep)) == "stats" else "warn",
                   "%d 个交易日 (%s ~ %s)" % (len(rep), rep.date.min(), rep.date.max()))]
    failed = [s for s, p in panel.provenance.items() if not p.ok]
    if panel.provenance:
        ev.append(Evidence("数据源调用", "warn" if failed else "ok",
                           ("失败: %s" % ", ".join(failed)) if failed else
                           "%d 次行情调用全部成功 (Yahoo Finance)" % len(panel.provenance)))
    ev.append(_freshness(max(rep.date), today))
    haircuts = "BTC 抵押折算率 %.0f%%" % (100 * a.btc_haircut)
    if a.eth > 0:
        haircuts += "、ETH 抵押折算率 %.0f%%" % (100 * a.eth_haircut)
    ev.append(Evidence("用户假设参数", "warn", "维持保证金率 %.1f%%、%s —— 不是 Bitget 官方数值"
                       % (100 * a.maint_margin, haircuts)))
    ev.append(Evidence("模型简化", "warn", "未计手续费、资金费、滑点、分档维持保证金; 股票与加密的日期对齐是近似"))
    card = _overall(items, ev)
    if spot_only:
        card.rule = SCORE_RULE_SPOT
    return card


# ---------------------------------------------------------------- 单笔

def trade_scorecard(leverage: float, maint_margin: float, worst_day_adverse: float, day_hits: int, n_days: int,
                    last_date: str, weekend_worst_adverse: float | None = None, weekend_hits: int = 0,
                    n_weekends: int = 0, today: date | None = None) -> Scorecard:
    """adverse 为正数 (不利变动幅度)。单笔「离强平的距离」= 1/杠杆 - 维持保证金率。"""
    thr = 1.0 / leverage - maint_margin
    items = [ScoreItem("single_day", "单日极端行情", from_buffer_used(worst_day_adverse / thr, day_hits > 0),
                       "正股 5 年单日 (前收 → 当日极值)",
                       ("5 年里 %d 天触及强平" % day_hits) if day_hits else
                       "最差不利变动用掉 %.1f%% 的强平距离" % (100 * worst_day_adverse / thr), day_hits > 0)]
    ev = [Evidence("正股历史样本", "ok" if confidence_tier(n_days) == "stats" else "warn", "%d 个交易日" % n_days)]
    if weekend_worst_adverse is not None:
        items.append(ScoreItem("weekend", "周末持仓", from_buffer_used(weekend_worst_adverse / thr, weekend_hits > 0),
                               "rToken 真实周末交易 (2026-06 起)",
                               ("%d 个周末触及强平" % weekend_hits) if weekend_hits else
                               "最差周末不利变动用掉 %.1f%% 的强平距离" % (100 * weekend_worst_adverse / thr),
                               weekend_hits > 0))
        ev.append(Evidence("周末样本", "ok" if confidence_tier(n_weekends) == "stats" else "warn",
                           "%d 个周末 (样本 <30, 只能当案例看)" % n_weekends if n_weekends < 30 else "%d 个周末" % n_weekends))
    ev.append(_freshness(last_date, today))
    ev.append(Evidence("用户假设参数", "warn", "维持保证金率 %.1f%% —— 不是 Bitget 官方数值" % (100 * maint_margin)))
    return _overall(items, ev)


def _freshness(last: str, today: date | None) -> Evidence:
    today = today or date.today()
    age = (today - date.fromisoformat(str(last))).days
    # 周末 + 时区差, 最新交易日落后 ≤4 天属正常
    return Evidence("数据新鲜度", "ok" if age <= 4 else "warn", "行情截至 %s (距今 %d 天)" % (last, age))
