"""
组合研究任务: 持仓 -> 5 年历史重演 + 多日回撤 + 预设情景 + 反向压力 -> 安全分 -> 可执行调整方案 -> 结论。

规则与单笔结论卡相同:
  1. 每个数字由确定性代码算出, 带来源或标注「用户假设」
  2. 分数和调整方案由公开规则算出, 不由大模型决定
  3. 大模型只写人话; 输出里出现事实表以外的数字就弃用, 改用模板
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass, field

from . import llm
from . import portfolio as pf
from . import links, score, suggest
from .card import Fact, unverified_numbers
from .provenance import CallLog

LLM_TIMEOUT = 90.0   # 结论由用户按按钮触发, 不阻塞打分; 实测见 README


@dataclass
class PortfolioReport:
    account: dict
    scorecard: dict
    facts: list[Fact]
    worst_days: list[dict]
    multi_days: list[dict]
    presets: list[dict]
    suggestions: list[dict]
    narrative: str
    narrative_by: str
    warnings: list[str] = field(default_factory=list)
    provenance: list[dict] = field(default_factory=list)
    # 界面「5 年地震图」: 按日期排序的每日账户亏损 (extreme 口径) 与强平线对应的亏损比例
    daily_dates: list[str] = field(default_factory=list)
    daily_loss: list[float] = field(default_factory=list)
    liq_loss: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _usd(x: float) -> str:
    return "%s USDT" % format(round(x), ",")


def build_report(a: pf.Account, panel: pf.Panel, horizon: int = 5, use_llm: bool = True,
                 log: CallLog | None = None) -> PortfolioReport:
    err = pf.validate_account(a)
    if err:
        raise ValueError(err)
    cn = pf.crypto_names(a)          # 这个组合里的加密抵押品, 如 BTC / ETH / BTC/ETH
    ysrc = [p.label() for s, p in sorted(panel.provenance.items()) if s not in (pf.BTC, pf.ETH)]
    bsrc = [panel.provenance[k].label() for k in (pf.BTC, pf.ETH)
            if k in panel.provenance and (k == pf.BTC or a.eth > 0)]
    allsrc = ysrc + bsrc
    assumption = ("用户假设: 维持保证金率 %.1f%%, BTC 抵押折算率 %.0f%%%s (非 Bitget 官方数值)"
                  % (100 * a.maint_margin, 100 * a.btc_haircut,
                     ", ETH 抵押折算率 %.0f%%" % (100 * a.eth_haircut) if a.eth > 0 else ""))
    e0, m0, gross = pf.equity0(a, panel.btc_price, panel.eth_price), pf.maint0(a), pf.gross_notional(a)
    sc = score.portfolio_scorecard(a, panel, horizon)

    rep = pf.replay(a, panel, "extreme")
    w = rep.iloc[0]
    wloss = e0 - float(w.equity)
    coll_share = (-float(w.collateral_pnl) / wloss) if wloss > 0 else 0.0
    md = pf.multi_day_worst(a, panel, horizon)
    mw = md.iloc[0]
    x_follow = pf.uniform_move_to_liquidation(a, panel.btc_price, True, panel.eth_price)
    x_flat = pf.uniform_move_to_liquidation(a, panel.btc_price, False, panel.eth_price)

    def xdisp(x):
        return "不利变动 %.1f%%" % (100 * x) if x is not None and x < 1 else "价格归零也不强平"

    items = {i.key: i for i in sc.items}
    facts = [
        Fact("score", "安全总分", sc.overall, "%.0f 分 (%s) · 短板: %s" % (sc.overall, score.grade(sc.overall), sc.weakest_label),
             ["规则: " + score.SCORE_RULE] + allsrc + [assumption]),
        Fact("score_single", items["single_day"].label, items["single_day"].score,
             "%.0f 分 · %s" % (items["single_day"].score, items["single_day"].detail), [items["single_day"].basis] + allsrc),
        Fact("score_multi", items["multi_day"].label, items["multi_day"].score,
             "%.0f 分 · %s" % (items["multi_day"].score, items["multi_day"].detail), [items["multi_day"].basis] + allsrc),
        Fact("score_presets", items["presets"].label, items["presets"].score,
             "%.0f 分 · %s" % (items["presets"].score, items["presets"].detail), [items["presets"].basis] + bsrc),
        Fact("equity0", "开仓时账户权益 (按抵押折算)", e0, _usd(e0), bsrc + [assumption]),
        Fact("buffer", "离强平的距离 (权益 − 维持保证金)", e0 - m0, _usd(e0 - m0), bsrc + [assumption]),
        Fact("gross", "仓位名义价值合计", gross, _usd(gross), ["用户输入"]),
        Fact("eff_lev", "有效杠杆 (名义 ÷ 权益)", gross / e0, "%.2f 倍" % (gross / e0), bsrc + [assumption]),
        Fact("btc_px", "BTC 现价", panel.btc_price, "%s (%s)" % (_usd(panel.btc_price), panel.btc_price_date), bsrc[:1]),
        *([Fact("eth_px", "ETH 现价", panel.eth_price, "%s (%s)" % (_usd(panel.eth_price), panel.eth_price_date), bsrc[1:])]
          if a.eth > 0 else []),
        Fact("sd_loss", "5 年最差单日 (各资产最差价同时出现)", float(w.loss_pct),
             "%s · 账户亏损 %.1f%%" % (w.date, 100 * w.loss_pct), allsrc),
        Fact("sd_coll", "其中 %s 抵押品缩水" % cn, -float(w.collateral_pnl),
             "%s (占当日亏损 %.0f%%)" % (_usd(-float(w.collateral_pnl)), 100 * coll_share), bsrc + [assumption]),
        Fact("sd_liq", "5 年里触及强平的交易日", int(rep.liquidated.sum()), "%d / %d 天" % (int(rep.liquidated.sum()), len(rep)),
             allsrc + [assumption]),
        Fact("md_loss", "连续 %d 日最差 (收盘价)" % horizon, float(mw.loss_pct),
             "%s 起持有 %d 天 · 账户亏损 %.1f%%" % (mw.start, int(mw.days), 100 * mw.loss_pct), allsrc),
        Fact("x_follow", "所有仓位同步不利、%s 同跌时, 多少会强平" % cn, x_follow, xdisp(x_follow), [assumption, "计算: 二分求解"]),
        Fact("x_flat", "所有仓位同步不利、%s 不动时, 多少会强平" % cn, x_flat, xdisp(x_flat), [assumption, "计算: 二分求解"]),
    ]

    # ---- 可执行调整方案 ----
    suggestions = []
    s1 = suggest.scale_for_target(a, panel, suggest.TARGET, horizon)
    if s1["status"] == "ok":
        txt = "所有仓位同比例缩到 %.0f%% (名义 %s → %s), 总分 %.0f → %.0f" % (
            100 * s1["factor"], format(round(s1["gross_before"]), ","), format(round(s1["gross"]), ","),
            s1["score_before"], s1["score"])
        suggestions.append({"key": "scale", "text": txt, "score": s1["score"], "account": s1["account"].to_dict(),
                            "title": "仓位同比例缩到 %.0f%%" % (100 * s1["factor"]), "score_before": sc.overall,
                            "detail": "名义 %s → %s USDT" % (format(round(s1["gross_before"]), ","), format(round(s1["gross"]), ","))})
        facts.append(Fact("sug_scale", "调整方案: 同比例缩仓到 %.0f 分" % suggest.TARGET, s1["factor"], txt,
                          ["规则: 二分搜索让总分 ≥ %.0f 的最大比例" % suggest.TARGET] + allsrc))
    elif s1["status"] == "infeasible":
        txt = "只缩仓位达不到 %.0f 分: 抵押品自身的下跌就已经不够, 需要换保证金或追加保证金" % suggest.TARGET
        suggestions.append({"key": "scale", "text": txt, "score": None, "account": None, "score_before": sc.overall,
                            "title": "只缩仓位达不到 %.0f 分" % suggest.TARGET, "detail": "抵押品自身的下跌就已经不够, 需要换保证金或追加保证金"})
        facts.append(Fact("sug_scale", "调整方案: 同比例缩仓", None, txt, allsrc))
    if a.btc > 0 or a.eth > 0:
        b = suggest.swap_crypto_to_usdt(a, panel.btc_price, panel.eth_price)
        sb = suggest.overall(b, panel, horizon)
        txt = "%s 保证金按现价全换成 USDT (%s), 总分 %.0f → %.0f" % (cn, _usd(b.usdt - a.usdt), sc.overall, sb)
        suggestions.append({"key": "swap", "text": txt, "score": sb, "account": b.to_dict(), "score_before": sc.overall,
                            "title": "%s 保证金换 USDT" % cn, "detail": "按现价换成 %s" % _usd(b.usdt - a.usdt)})
        facts.append(Fact("sug_swap", "调整方案: %s 保证金换 USDT" % cn, sb, txt, bsrc + allsrc))
    drag = Counter(rep.head(10).worst_position).most_common(1)
    if drag and len(a.positions) > 1:
        sym, side = drag[0][0].split(" ")
        c = suggest.halve_position(a, sym, side)
        scc = suggest.overall(c, panel, horizon)
        txt = "把最拖累的 %s %s 砍半 (最差 10 天里 %d 天是它亏最多), 总分 %.0f → %.0f" % (
            sym, side, drag[0][1], sc.overall, scc)
        suggestions.append({"key": "halve", "text": txt, "score": scc, "account": c.to_dict(), "score_before": sc.overall,
                            "title": "%s %s砍半" % (sym, "做多" if side == "long" else "做空"),
                            "detail": "最差 10 天里 %d 天是它亏最多" % drag[0][1]})
        facts.append(Fact("sug_halve", "调整方案: 砍半最拖累的仓位", scc, txt, allsrc))

    worst_days = [{"日期": r.date, "仓位盈亏": round(r.positions_pnl), "%s 抵押品盈亏" % cn: round(r.collateral_pnl),
                   "账户亏损 %": round(100 * r.loss_pct, 1), "强平距离用掉 %": round(100 * r.buffer_used, 1),
                   "BTC 当日最低": "%+.1f%%" % (100 * r.btc_move),
                   **({"ETH 当日最低": "%+.1f%%" % (100 * r.eth_move)} if a.eth > 0 else {}),
                   "最拖累": r.worst_position, "强平": "是" if r.liquidated else ""}
                  for r in rep.head(10).itertuples()]
    multi_days = [{"起点": r.start, "持有天数": int(r.days), "账户亏损 %": round(100 * r.loss_pct, 1),
                   "BTC 期间": "%+.1f%%" % (100 * r.btc_move), "强平": "是" if r.liquidated else ""}
                  for r in md.head(5).itertuples()]
    presets = []
    for p in pf.PRESETS:
        r = pf.shock(a, panel.btc_price, pf.preset_moves(a, p), panel.eth_price)
        presets.append({"假设情景": p["name"], "账户亏损 %": round(100 * r.loss_pct, 1),
                        "强平距离用掉 %": round(100 * r.buffer_used, 1), "强平": "是" if r.liquidated else "",
                        "得分": round(score.from_buffer_used(r.buffer_used, r.liquidated))})

    warnings = [
        "安全分规则: " + score.SCORE_RULE,
        "「各资产最差价同时出现」偏保守; 收盘价口径偏乐观; 两者之间才是真实情况",
        "维持保证金率、加密抵押折算率是你设定的假设, 不是 Bitget 官方数值; 未计手续费、资金费、滑点、分档维持保证金",
        "BTC-USD / ETH-USD 是 UTC 日线, 美股是纽约交易日, 同日对齐是近似; 预设情景里 BTC 和 ETH 按同幅变动假设",
        "历史不代表未来; 分数和方案只是开仓前的参考, 由你自己决定",
    ]

    for s in suggestions:   # 每个方案: 每只标的要从多少调到多少 + Bitget 交易页面链接
        if s["account"] is not None:
            d = s["account"]
            s["steps"] = links.adjustment_steps(a, pf.account_from_dict(d))
        else:
            s["steps"] = []

    chrono = rep.sort_values("date")
    report = PortfolioReport(a.to_dict(), sc.to_dict(), facts, worst_days, multi_days, presets, suggestions,
                             _template(facts, sc), "template", warnings,
                             [p.to_dict() for p in panel.provenance.values()],
                             list(chrono.date), [float(x) for x in chrono.loss_pct], (e0 - m0) / e0)
    if use_llm:
        narrate(report, horizon, log)
    return report


def narrate(report: PortfolioReport, horizon: int = 5, log: CallLog | None = None, timeout: float = LLM_TIMEOUT) -> PortfolioReport:
    """让大模型把事实表写成人话 (原地更新 report)。数字核对不过或调用失败时保留模板结论。"""
    text, lprov = llm.chat([{"role": "system", "content": NARRATIVE_PROMPT},
                            {"role": "user", "content": json.dumps(
                                {"facts": [{"label": f.label, "display": f.display} for f in report.facts]},
                                ensure_ascii=False)}], timeout=timeout)
    report.provenance.append(lprov.to_dict())
    if log is not None:
        log.append(lprov)
    if text is None:
        report.narrative_by = "template (大模型不可用: %s)" % (lprov.error or "")[:80]
        return report
    bad = unverified_numbers(text, report.facts, extra=(str(horizon), "60"))
    if bad:
        report.narrative_by = "template (大模型输出含事实表以外的数字 %s, 已弃用)" % bad[:5]
    else:
        report.narrative, report.narrative_by = text.strip(), "llm:%s · %.1f 秒" % (
            lprov.params.get("model"), lprov.latency_ms / 1000)
    return report


def _template(facts: list[Fact], sc: score.Scorecard) -> str:
    """分点结论 (Markdown 列表): 很多人不读整段文字, 每点一个结论。"""
    f = {x.key: x.display for x in facts}
    lines = [
        "- **安全总分**: %s" % f["score"],
        "- **最差单日**: %s, 其中抵押品缩水 %s" % (f["sd_loss"], f["sd_coll"]),
        "- **连续持有最差**: %s" % f["md_loss"],
        "- **同步下跌多少会强平**: 加密抵押品同跌时%s; 不动时%s" % (f["x_follow"], f["x_flat"]),
    ]
    sug = [f[k] for k in ("sug_scale", "sug_swap", "sug_halve") if k in f]
    if sug:
        lines.append("- **可以考虑**:")
        lines += ["    - %s" % s for s in sug]
    lines.append("- 历史不代表未来, 最终由你决定")
    return "\n".join(lines)


NARRATIVE_PROMPT = """你是交易风控助理。根据给定的事实表, 用中文写结论, 面向持有跨资产统一账户的散户交易者。
格式: Markdown 分点列表, 4-6 个要点, 每个要点以「- 」开头、一句话说完, 要点开头用 **粗体** 写 2-6 个字的小标题。不要写成整段文字。
硬性规则:
- 只能使用事实表 display 字段里出现过的数字, 原样引用; 不许计算新数字, 不许写任何事实表以外的数字 (包括年份、概率、价格)
- 第一个要点给出安全总分和短板
- 有一个要点说明加密抵押品 (BTC/ETH) 在最差日子里的作用 (双重打击)
- 列出事实表里的调整方案, 最后一个要点说明最终由用户自己决定
- 不要预测涨跌, 不要解释历史日期发生了什么事件"""
