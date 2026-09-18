"""
一次完整研究任务: 交易想法 -> 历史数据 -> 压力测试 -> 结论卡。

规则:
  1. 结论卡里的每个数字都由确定性代码算出, 并带来源 (数据调用记录) 或明确标注为「用户假设」。
  2. 风险等级、建议杠杆上限由公开写明的规则推出, 不由大模型判断。
  3. 大模型只把事实写成人话; 它输出里的每个数字都会被核对, 出现事实表以外的数字就弃用, 改用模板。
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field

from . import llm, score
from .analysis import overnight_gap as og
from .analysis import weekend_gap as wg
from .idea import TradeIdea
from .provenance import CallLog, Provenance
from .sources import mcp, yahoo
from .sources.bitget import to_frame

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(ROOT, "data", "cache", "bitget")

# 风险规则 (公开写明, 界面展示)
BUFFER = 0.7    # 历史最差不利变动不超过强平线的 70% 才算「低」, 也用于推算建议杠杆上限
RISK_RULE = ("强平风险 高: 历史上出现过触及近似强平线; 中: 历史最差不利变动 ≥ 强平线的 %d%%; 低: 其余。"
             "建议杠杆上限 = 让历史最差不利变动恰好等于强平线的 %d%% 时的杠杆。"
             "注意「强平风险低」不等于「不会大亏」, 请同时看「历史最差对应的保证金亏损」。"
             % (BUFFER * 100, BUFFER * 100))


@dataclass
class Fact:
    key: str
    label: str
    value: float | int | str | None
    display: str
    sources: list[str]


@dataclass
class Card:
    idea: dict
    headline: str
    risk_level: str
    tier: str
    tier_text: str
    facts: list[Fact]
    narrative: str
    narrative_by: str
    warnings: list[str] = field(default_factory=list)
    provenance: list[dict] = field(default_factory=list)
    scorecard: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _pct(x: float) -> str:
    """带正负号的变动。"""
    return "%+.2f%%" % (100 * x)


def _pct_abs(x: float) -> str:
    """绝对值, 不带正负号。"""
    return "%.2f%%" % (100 * abs(x))


def load_rtoken_cache(symbol: str, cache_dir: str = CACHE_DIR):
    path = os.path.join(cache_dir, "R%sUSDT_spot_1h.json" % symbol)
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    return to_frame(d["rows"]), Provenance(**d["provenance"])


def _log(log, prov):
    if log is not None:
        log.append(prov)


def build_card(idea: TradeIdea, maint_margin: float = 0.01, cache_dir: str = CACHE_DIR,
               log: CallLog | None = None, use_llm: bool = True, use_mcp: bool = True,
               mcp_timeout: float = 12.0) -> Card:
    provs: list[Provenance] = []
    rt, rprov = load_rtoken_cache(idea.symbol, cache_dir)
    provs.append(rprov)
    ud, uprov = yahoo.ohlcv(idea.symbol, "5y", "1d")
    provs.append(uprov)
    _log(log, uprov)
    if ud is None:
        raise RuntimeError("正股行情取数失败: %s" % uprov.error)
    rsrc, usrc = rprov.label(), uprov.label()
    assumption = "用户假设: 维持保证金率 %.1f%% (非 Bitget 官方数值)" % (100 * maint_margin)
    lev = idea.leverage
    long_side = idea.side == "long"
    thr = 1.0 / lev - maint_margin
    warnings: list[str] = []

    facts: list[Fact] = [Fact("liq_threshold", "近似强平线 (不利变动)", -thr, _pct(-thr), [assumption])]

    # ---- 周末 (rToken 真实周末交易) ----
    recs = wg.weekend_records(ud, rt)
    s = wg.summarize(recs)
    wk_worst = None
    if s["n"]:
        wk_adverse = [(-r.mae_long if long_side else r.mae_short) for r in recs]
        wk_worst = max(wk_adverse)
        wk_hits = wg.liquidation_hits(recs, lev, idea.side, maint_margin)["hits"]
        both = [rsrc, usrc]
        facts += [
            Fact("wk_n", "真实周末交易样本", s["n"], "%d 个周末 (%s ~ %s)" % (s["n"], s["first_friday"], s["last_friday"]), both),
            Fact("wk_drift", "周末 rToken 漂移 (中位绝对值)", s["median_abs_drift"], _pct_abs(s["median_abs_drift"]), both),
            Fact("wk_reanchor", "周一开盘重锚跳空 (中位绝对值, 相对开盘前 1 小时 rToken 价)", s["median_abs_reanchor"],
                 _pct_abs(s["median_abs_reanchor"]), both + ["研究: research/price_discovery/ —— 开盘前 rToken 已跟上正股盘前价, "
                                                              "周五 16:00 至周日 19:00 (纽约) 的 rToken 价格预测周一开盘不如周五收盘价"]),
            Fact("wk_reanchor_max", "周一开盘重锚跳空 (最大绝对值)", s["max_abs_reanchor"], _pct_abs(s["max_abs_reanchor"]), both),
            Fact("wk_worst", "周末期间最差不利变动", -wk_worst, _pct(-wk_worst), both),
            Fact("wk_worst_equity", "周末最差情况对应的保证金亏损", -min(wk_worst * lev, 1.0),
                 _pct(-min(wk_worst * lev, 1.0)), both + ["计算: 最差不利变动 x 杠杆, 封顶 -100%"]),
            Fact("wk_hits", "周末样本中触及强平线", wk_hits, "%d/%d" % (wk_hits, s["n"]), both + [assumption]),
        ]
    else:
        warnings.append("没有可用的真实周末交易样本")
    tier = s["tier"]

    # ---- 正股 5 年单日 (尾部参考) ----
    days = og.gap_days(ud)
    t = og.tail_counts(days)
    br = og.leverage_breaches(days, lev, idea.side, maint_margin)
    worst_day = og.worst_days(days, idea.side, 1)[0]
    on_worst = -worst_day.mae_long if long_side else worst_day.mae_short
    facts += [
        Fact("on_days", "正股历史样本", t["n_days"], "%d 个交易日 (%s ~ %s)" % (t["n_days"], t["first"], t["last"]), [usrc]),
        Fact("on_gap10", "|开盘跳空| ≥10% 次数", t["abs_gap_ge"][0.10], "%d 次" % t["abs_gap_ge"][0.10], [usrc]),
        Fact("on_worst", "5 年最差单日不利变动 (前收 -> 当日极值)", -on_worst,
             "%s (%s)" % (_pct(-on_worst), worst_day.date), [usrc]),
        Fact("on_worst_equity", "5 年最差单日对应的保证金亏损", -min(on_worst * lev, 1.0),
             _pct(-min(on_worst * lev, 1.0)), [usrc, "计算: 最差不利变动 x 杠杆, 封顶 -100%"]),
        Fact("on_hits", "5 年单日触及强平线", br["hits"], "%d 次" % br["hits"], [usrc, assumption]),
    ]

    # ---- Skill (bitget-signal MCP): 若用 BTC 作保证金, 抵押品自身的波动参考 ----
    if use_mcp:
        client = mcp.MCPClient(timeout=mcp_timeout)
        try:
            client.connect()
            atr, aprov = client.call("technical_analysis", action="atr", symbol="BTC/USDT", timeframe="1d")
        except Exception as e:  # 连接本身失败
            atr, aprov = None, Provenance("mcp", "technical_analysis.atr", {}, 0, 0, False, error=str(e)[:160])
        provs.append(aprov)
        _log(log, aprov)
        if atr is not None and isinstance(atr, dict) and atr.get("atr_pct") is not None:
            facts.append(Fact("btc_atr", "BTC 日均真实波幅 ATR14 (若用 BTC 作保证金)", float(atr["atr_pct"]) / 100,
                              "%.2f%%" % float(atr["atr_pct"]), [aprov.label()]))
        else:
            warnings.append("Bitget Skill 数据源 (technical_analysis) 暂不可用: %s" % (aprov.error or "")[:80])

    # ---- 规则推出的强平风险等级与建议杠杆上限 ----
    worst_all = max(on_worst, wk_worst or 0.0)
    any_hit = br["hits"] > 0 or any(f.key == "wk_hits" and f.value > 0 for f in facts)
    risk = "高" if any_hit else ("中" if worst_all >= BUFFER * thr else "低")
    lev_cap = 1.0 / (worst_all / BUFFER + maint_margin) if worst_all > 0 else None
    facts.append(Fact("lev_cap", "参考杠杆上限 (历史尾部规则推算)", lev_cap,
                      ("%.1f 倍" % lev_cap) if lev_cap else "n/a", ["规则: " + RISK_RULE, assumption]))

    side_cn = "做多" if long_side else "做空"
    scen_cn = "持仓过周末" if idea.scenario == "weekend" else "隔夜/财报"
    headline = "%s rToken %s倍%s · %s · 强平风险 %s" % (idea.symbol, "%g" % lev, side_cn, scen_cn, risk)

    if s["n"]:
        warnings.insert(0, wg.TIER_TEXT[tier] + " (周末样本仅覆盖 %s ~ %s)" % (s["first_friday"], s["last_friday"]))
    warnings += [
        "近似强平线基于%s, 未计手续费、资金费、滑点, 也未计用 BTC 等作抵押时抵押品自身的下跌" % assumption.replace("用户假设: ", ""),
        "单日不利变动只看前收到当日极值, 未计多日持仓的累计回撤",
        "历史不代表未来; 结论只是开仓前的参考, 由你自己决定",
    ]

    sc = score.trade_scorecard(lev, maint_margin, on_worst, br["hits"], t["n_days"], t["last"],
                               wk_worst, next((f.value for f in facts if f.key == "wk_hits"), 0), s["n"])
    c = Card(idea.to_dict(), headline, risk, tier, wg.TIER_TEXT[tier], facts, _template(facts, sc), "template",
             warnings, [p.to_dict() for p in provs], sc.to_dict())
    if use_llm:
        narrate(c, log)
    return c


def narrate(c: Card, log: CallLog | None = None, timeout: float = 90.0) -> Card:
    """让大模型把事实表写成人话 (原地更新卡片)。数字核对不过或调用失败时保留模板结论。"""
    idea = TradeIdea(**{k: c.idea[k] for k in ("symbol", "side", "leverage", "scenario", "raw_text", "parsed_by", "note")})
    safety = score_text(c.scorecard) if c.scorecard else ""
    text, lprov = _llm_narrative(idea, c.facts, safety, c.tier, timeout)
    c.provenance.append(lprov.to_dict())
    _log(log, lprov)
    if text is None:
        c.narrative_by = "template (大模型不可用: %s)" % (lprov.error or "")[:80]
        return c
    bad = unverified_numbers(text, c.facts, idea, extra=(c.tier_text, safety))   # 提示词要求提到样本分级和安全分
    if bad:
        c.narrative_by = "template (大模型输出含事实表以外的数字 %s, 已弃用)" % bad[:5]
    else:
        c.narrative, c.narrative_by = text.strip(), "llm:%s · %.1f 秒" % (lprov.params.get("model"), lprov.latency_ms / 1000)
    return c


def score_text(sc: dict) -> str:
    return "%.0f 分 (%s) · 短板: %s" % (sc["overall"], score.grade(sc["overall"]), sc["weakest_label"])


def _template(facts: list[Fact], sc) -> str:
    """分点结论 (Markdown 列表): 很多人不读整段文字, 每点一个结论。"""
    f = {x.key: x.display for x in facts}
    lines = ["- **安全分**: %s" % score_text(sc.to_dict()),
             "- **强平线**: 不利变动到 %s 左右就会触及近似强平线" % f["liq_threshold"]]
    if "wk_n" in f:
        lines.append("- **周末持仓** (%s): 最差不利变动 %s, 保证金亏 %s, 触及强平 %s"
                     % (f["wk_n"], f["wk_worst"], f["wk_worst_equity"], f["wk_hits"]))
        lines.append("- **注意**: 周一开盘重锚跳空中位只有 %s, 是因为开盘前 rToken 已跟上正股盘前价; "
                     "周末波动更像噪音, 照样可能触发强平" % f["wk_reanchor"])
    lines.append("- **5 年最差单日** (%s): 不利变动 %s, 放到你的杠杆上保证金亏 %s; 单日触及强平 %s"
                 % (f["on_days"], f["on_worst"], f["on_worst_equity"], f["on_hits"]))
    if "btc_atr" in f:
        lines.append("- **若用 BTC 作保证金**: 还要叠加 BTC 自身的日均波幅 %s" % f["btc_atr"])
    lines.append("- **参考杠杆上限**: 约 %s —— 这是「历史最差不利变动恰好吃掉 %d%% 强平距离」倒推出来的, 依赖历史样本和你填的维持保证金率, 不是建议, 更不是安全承诺" % (f["lev_cap"], int(BUFFER * 100)))
    return "\n".join(lines)


NARRATIVE_PROMPT = """你是交易风控助理。根据给定的事实表, 用中文写结论, 面向一个散户交易者。
格式: Markdown 分点列表, 4-6 个要点, 每个要点以「- 」开头、一句话说完, 要点开头用 **粗体** 写 2-6 个字的小标题。不要写成整段文字。
硬性规则:
- 只能使用事实表 display 字段、safety_score、tier_text 里出现过的数字, 原样引用; 不许计算新数字, 不许写任何其他数字 (包括年份、概率、价格)
- 第一个要点给出安全分 (safety_score)
- 有一个要点提到样本局限 (tier_text)
- 有一个要点提到「历史最差对应的保证金亏损」
- 最后一个要点给出建议杠杆上限, 并说明最终由用户自己决定
- 不要预测涨跌"""


def _llm_narrative(idea: TradeIdea, facts: list[Fact], safety: str, tier: str, timeout: float = 90.0):
    payload = {"idea": idea.to_dict(), "safety_score": safety, "tier_text": wg.TIER_TEXT[tier],
               "facts": [{"key": f.key, "label": f.label, "display": f.display} for f in facts]}
    return llm.chat([{"role": "system", "content": NARRATIVE_PROMPT},
                     {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}], timeout=timeout)


_NUM = re.compile(r"[-+]?\d+(?:\.\d+)?")


def unverified_numbers(text: str, facts: list[Fact], idea: TradeIdea | None = None, extra=()) -> list[str]:
    """找出大模型文字里、不在事实表 label / display (以及用户自己给的杠杆、extra) 中出现过的数字。
    label 由代码生成 (如「5 年最差单日」), 里面的数字同样可引用。"""
    allowed = set()
    for x in extra:
        for m in _NUM.findall(str(x)):
            allowed.add(m.lstrip("+"))
            allowed.add(m.lstrip("+-"))
    for f in facts:
        for m in _NUM.findall(f.label + " " + f.display):
            allowed.add(m.lstrip("+"))
            allowed.add(m.lstrip("+-"))
    if idea is not None:
        allowed.add("%g" % idea.leverage)
    return [m for m in _NUM.findall(text) if m.lstrip("+") not in allowed and m.lstrip("+-") not in allowed]
