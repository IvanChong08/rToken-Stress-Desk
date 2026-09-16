"""
网页样式与 HTML 片段 —— 定稿设计「B1 危险警示风格 + B2 排版」(设计稿 2026-09-16)。

只负责展示: 这里不计算任何数字, 所有数值都来自 portfolio_card / card / score 已经算好的结果。
注意: st.markdown 会把行首缩进 4 格的内容当代码块, 所以所有 HTML 都拼成单行。
"""
from __future__ import annotations

import html
import re

BG, PANEL, LINE, LINE2 = "#121210", "#1a1a17", "#2e2d28", "#25241f"
TEXT, MUTED, SOFT, STENCIL = "#ece7dc", "#85806f", "#c9c3b5", "#5a574c"
ORANGE, RED, GREEN = "#f08a24", "#ff4a3d", "#7fd67a"

CSS = """<style>
@import url('https://fonts.googleapis.com/css2?family=Chakra+Petch:wght@500;600;700&family=JetBrains+Mono:wght@400;500;700&display=swap');
html, body, .stApp, [data-testid="stAppViewContainer"] { background: #121210; color: #ece7dc; }
.stApp, .stApp p, .stApp label, .stApp li, .stApp td, .stApp th, .stApp button, .stApp input, .stApp textarea, .stApp [data-testid="stMarkdownContainer"] { font-family: "Chakra Petch", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif; }
[data-testid="stIconMaterial"], .material-symbols-rounded { font-family: "Material Symbols Rounded" !important; }
[data-testid="stHeader"] { background: transparent; }
[data-testid="stMainBlockContainer"] { padding-top: 0.6rem; max-width: 1480px; }
.m, .m * { font-family: "JetBrains Mono", ui-monospace, Menlo, monospace !important; font-variant-numeric: tabular-nums; }
.rsd-tape { height: 10px; background: repeating-linear-gradient(135deg, #f08a24 0 14px, #121210 14px 28px); }
.rsd-tape-thin { background: repeating-linear-gradient(135deg, #f08a24 0 6px, #121210 6px 12px); }
.rsd-stencil { font-family: "JetBrains Mono", ui-monospace, monospace !important; font-weight: 700; color: transparent; -webkit-text-stroke: 1px #5a574c; font-size: 38px; line-height: 1; }
div[class*="st-key-panel"] { background: #1a1a17; border: 1px solid #2e2d28; padding: 20px 22px 16px; position: relative; }
div[class*="st-key-panel"]::before { content: ""; position: absolute; top: -1px; left: -1px; width: 14px; height: 14px; border-top: 3px solid #f08a24; border-left: 3px solid #f08a24; pointer-events: none; }
div[class*="st-key-panel"]::after { content: ""; position: absolute; bottom: -1px; right: -1px; width: 14px; height: 14px; border-bottom: 3px solid #f08a24; border-right: 3px solid #f08a24; pointer-events: none; }
.stButton button, [data-testid="stPopover"] button { border-radius: 0 !important; border: 1px solid #2e2d28 !important; background: #121210 !important; color: #ece7dc !important; }
.stButton button:hover, [data-testid="stPopover"] button:hover { border-color: #f08a24 !important; color: #f08a24 !important; }
.stButton button[kind="primary"], [data-testid^="stBaseButton-primary"] { background: #f08a24 !important; color: #121210 !important; border: none !important; font-weight: 700 !important; letter-spacing: 0.1em; clip-path: polygon(0 0, 100% 0, 100% 70%, 96% 100%, 0 100%); }
[data-testid="stLinkButton"] a { border-radius: 0 !important; border: 1px solid #f08a24 !important; color: #f08a24 !important; background: transparent !important; }
[data-baseweb="input"], [data-baseweb="base-input"], [data-baseweb="select"] > div { border-radius: 0 !important; background: #121210 !important; }
/* 标签栏方案 2: 标签紧贴顶栏下沿, 做成同一条栏的一部分 */
[data-baseweb="tab-list"] { gap: 0; border: 1px solid #2e2d28; width: fit-content; margin-top: -30px; }
[data-baseweb="tab"] { padding: 0 34px !important; height: 46px; }
[data-baseweb="tab"] p { font-size: 16px !important; letter-spacing: 0.06em; }
/* 输入框变主角; 示例按钮缩成小 chip */
div[class*="st-key-pf_query"] input, div[class*="st-key-query"] input { height: 34px; font-size: 15px; }
div[class*="st-key-pf_ex_"] button, div[class*="st-key-pf_fu_"] button, div[class*="st-key-tr_ex_"] button {
  min-height: 30px !important; padding: 2px 10px !important; }
div[class*="st-key-pf_ex_"] button p, div[class*="st-key-pf_fu_"] button p, div[class*="st-key-tr_ex_"] button p {
  font-size: 12.5px !important; }
[data-baseweb="tab"][aria-selected="true"] { background: #f08a24; }
[data-baseweb="tab"][aria-selected="true"] p { color: #121210 !important; font-weight: 700; }
[data-baseweb="tab-highlight"], [data-baseweb="tab-border"] { display: none; }
[data-testid="stExpander"] details { border-radius: 0; border-color: #2e2d28; }
@keyframes rsdblink { 0%, 100% { opacity: 1; } 50% { opacity: 0.15; } }
.rsd-blink { animation: rsdblink 1s ease-in-out infinite; }
.rsd-table { border-collapse: collapse; width: 100%; }
.rsd-table th { text-align: left; font-weight: 600; color: #85806f; font-size: 12px; padding: 8px 10px; border-bottom: 2px solid #2e2d28; letter-spacing: 0.08em; }
.rsd-table td { padding: 9px 10px; font-size: 14px; border-bottom: 1px solid #25241f; color: #ece7dc; }
</style>"""


def esc(x) -> str:
    return html.escape(str(x))


def grade_color(s: float) -> str:
    return RED if s < 30 else (ORANGE if s < 60 else GREEN)


YELLOW = "#f5c542"


def _status_item(ok, text: str) -> str:
    """ok: True 正常(绿) / False 失败(红) / None 未运行(灰) / "pending" 检测中(黄色闪烁)。"""
    if ok == "pending":
        return ('<span class="rsd-blink" style="display:flex;align-items:center;gap:6px;color:%s;">'
                '<svg width="8" height="8"><rect width="8" height="8" fill="%s"></rect></svg>%s</span>' % (YELLOW, YELLOW, esc(text)))
    return ('<span style="display:flex;align-items:center;gap:6px;"><svg width="8" height="8"><rect width="8" height="8" fill="%s"></rect></svg>%s</span>'
            % (GREEN if ok is True else (RED if ok is False else MUTED), esc(text)))


def header(status: list[tuple], log_text: str) -> str:
    items = "".join(_status_item(ok, t) for ok, t in status)
    return ('<div class="rsd-tape"></div>'
            '<div style="display:flex;align-items:center;gap:22px;padding:14px 2px 12px;border-bottom:1px solid %s;flex-wrap:wrap;">'
            '<div style="display:flex;align-items:center;gap:12px;"><svg width="26" height="26" viewBox="0 0 28 28"><path d="M14 3 L26 24 H2 Z" fill="none" stroke="%s" stroke-width="2.5"></path><path d="M14 11 V17 M14 20 V21" stroke="%s" stroke-width="2.5"></path></svg>'
            '<span style="font-size:21px;font-weight:700;letter-spacing:0.08em;">RTOKEN STRESS DESK</span></div>'
            '<span style="font-size:13px;color:%s;">开仓前的压力测试台 · 每个数字都有来源 · 最终由你决定</span>'
            '<div class="m" style="margin-left:auto;display:flex;align-items:center;gap:16px;font-size:12px;color:%s;flex-wrap:wrap;">%s'
            '<span style="border:1px solid %s;padding:3px 8px;color:%s;">%s</span></div></div>'
            % (LINE, ORANGE, ORANGE, MUTED, MUTED, items, LINE, SOFT, esc(log_text)))


def panel_title(num: str, title: str, sub: str = "") -> str:
    return ('<div style="display:flex;align-items:baseline;gap:12px;margin-bottom:6px;">'
            '<span class="rsd-stencil">%s</span><span style="font-size:18px;font-weight:700;letter-spacing:0.06em;">%s</span>'
            '<span style="font-size:12px;color:%s;margin-left:auto;">%s</span></div>' % (esc(num), esc(title), MUTED, esc(sub)))


def seismograph(dates: list[str], losses: list[float], liq_loss: float, height: int = 200) -> str:
    """5 年地震图: 每个交易日的账户亏损 (向下), 强平线画成警示胶带。"""
    n = len(losses)
    if n < 2:
        return '<div style="color:%s;">样本不足, 无法绘制</div>' % MUTED
    w = n - 1
    ys = [100 * min(max(x, 0.0), 1.0) for x in losses]
    pts = " ".join("%d,%.1f" % (i, y) for i, y in enumerate(ys))
    liq_top = height * min(max(liq_loss, 0.0), 1.0)
    # 年份刻度
    ticks, seen = [], set()
    for i, d in enumerate(dates):
        y = d[:4]
        if y not in seen:
            seen.add(y)
            if i > 0:
                ticks.append('<span style="position:absolute;left:%.2f%%;">%s</span>' % (100 * i / w, y))
    # 最深的两根 (x 相隔 8% 以上), 标注日期与亏损
    order = sorted(range(n), key=lambda i: -losses[i])
    marks = []
    for i in order:
        if len(marks) == 2:
            break
        if all(abs(i - j) / w > 0.08 for j in marks):
            marks.append(i)
    labels = ""
    for i in marks:
        x = 100 * i / w
        top = height * min(max(losses[i], 0), 1) + 6
        shift = -92 if x > 85 else (-8 if x < 12 else -50)
        labels += ('<div style="position:absolute;left:%.2f%%;top:%.0fpx;height:16px;border-left:2px solid %s;"></div>'
                   '<div class="m" style="position:absolute;left:%.2f%%;top:%.0fpx;transform:translateX(%d%%);font-size:11px;font-weight:700;color:%s;background:%s;padding:1px 6px;white-space:nowrap;">%s −%.1f%%</div>'
                   % (x, top, TEXT, x, top + 18, shift, BG, TEXT, esc(dates[i]), 100 * losses[i]))
    return ('<div style="position:relative;height:%dpx;">'
            '<svg width="100%%" height="%d" viewBox="0 0 %d 100" preserveAspectRatio="none" style="position:absolute;left:0;top:0;">'
            '<line x1="0" y1="0.5" x2="%d" y2="0.5" stroke="%s" stroke-width="1" vector-effect="non-scaling-stroke"></line>'
            '<line x1="0" y1="25" x2="%d" y2="25" stroke="%s" stroke-dasharray="3 5" vector-effect="non-scaling-stroke"></line>'
            '<line x1="0" y1="50" x2="%d" y2="50" stroke="%s" stroke-dasharray="3 5" vector-effect="non-scaling-stroke"></line>'
            '<line x1="0" y1="75" x2="%d" y2="75" stroke="%s" stroke-dasharray="3 5" vector-effect="non-scaling-stroke"></line>'
            '<polygon points="0,0 %s %d,0" fill="%s" fill-opacity="0.16"></polygon>'
            '<polyline points="%s" fill="none" stroke="%s" stroke-width="1" vector-effect="non-scaling-stroke"></polyline></svg>'
            '<div class="rsd-tape-thin" style="position:absolute;left:0;right:0;top:%.0fpx;height:7px;"></div>'
            '<div class="m" style="position:absolute;right:0;top:%.0fpx;font-size:11px;font-weight:700;color:%s;background:%s;padding:2px 8px;">强平线 · 亏损 %.1f%%</div>'
            '<div class="m" style="position:absolute;left:0;top:2px;font-size:10px;color:%s;">0%%</div>'
            '<div class="m" style="position:absolute;left:0;top:%dpx;font-size:10px;color:%s;">50%%</div>%s</div>'
            '<div class="m" style="position:relative;height:18px;margin-top:4px;font-size:10px;color:%s;">%s</div>'
            % (height, height, w, w, STENCIL, w, LINE2, w, LINE2, w, LINE2, pts, w, RED, pts, RED,
               liq_top - 4, max(liq_top - 28, 0), BG, ORANGE, 100 * liq_loss, MUTED, height // 2 + 2, MUTED, labels,
               MUTED, "".join(ticks)))


def score_block(overall: float, weakest_label: str, grade: str) -> str:
    c = grade_color(overall)
    return ('<div style="display:flex;align-items:center;gap:20px;margin:4px 0 8px;">'
            '<div class="m" style="font-size:96px;line-height:0.85;font-weight:700;color:%s;">%02d</div>'
            '<div style="display:flex;flex-direction:column;gap:8px;">'
            '<div class="rsd-tape" style="height:auto;padding:3px;align-self:flex-start;"><div style="background:%s;padding:5px 14px;font-size:18px;font-weight:700;color:%s;letter-spacing:0.1em;">%s</div></div>'
            '<div style="font-size:14px;color:%s;">短板: %s</div>'
            '<div style="font-size:12px;color:%s;">总分取各项最低分, 不取平均</div></div></div>'
            % (c, round(overall), BG, c, esc(grade), SOFT, esc(weakest_label), MUTED))


def compare_line(before: float, after: float) -> str:
    """追问 / 套用方案后: 修改前总分 → 现在总分。"""
    d = round(after) - round(before)
    c = GREEN if d > 0 else (RED if d < 0 else MUTED)
    return ('<div class="m" style="display:flex;align-items:center;gap:10px;font-size:13px;color:%s;margin:-2px 0 6px;">'
            '<span>修改前</span><span style="color:%s;font-weight:700;">%d</span><span>→ 现在</span>'
            '<span style="color:%s;font-weight:700;">%d</span>'
            '<span style="background:%s;color:%s;font-weight:700;padding:0 6px;">%+d</span></div>'
            % (MUTED, grade_color(before), round(before), grade_color(after), round(after), c, BG, d))


RULER_SHORT = {"single_day": "单日", "multi_day": "连续5日", "presets": "预设", "weekend": "周末"}


def ruler(items: list[dict]) -> str:
    """强平距离尺: 每个评分项用掉多少强平距离 (= 100 − 分数), 最后 10% 是警示胶带区。
    标签用短名 (09-16 截图发现长名在尺上互相重叠); 相邻标记距离 < 30% 时上下错开。"""
    placed, prev_p, prev_row = [], None, 1
    for it in sorted(items, key=lambda x: 100 - x["score"]):
        p = min(max(100 - it["score"], 0.0), 100.0)
        it = {**it, "label": RULER_SHORT.get(it.get("key"), it["label"])}
        row = (1 - prev_row) if (prev_p is not None and p - prev_p < 30) else 0
        placed.append((p, row, it))
        prev_p, prev_row = p, row
    marks = ""
    for p, row, it in placed:
        c = grade_color(it["score"])
        label_top = 0 if row == 0 else 28
        shift = -92 if p > 85 else (-8 if p < 12 else -50)
        marks += ('<div style="position:absolute;left:%.1f%%;top:%dpx;height:%dpx;border-left:2px solid %s;"></div>'
                  '<svg width="12" height="8" style="position:absolute;left:%.1f%%;top:62px;transform:translateX(-5px);"><path d="M0 0 H12 L6 8 Z" fill="%s"></path></svg>'
                  '<div class="m" style="position:absolute;left:%.1f%%;top:%dpx;transform:translateX(%d%%);font-size:12px;font-weight:700;color:%s;background:%s;padding:1px 7px;white-space:nowrap;">%s %d</div>'
                  % (p, label_top + 20, 70 - label_top - 20, c, p, c, p, label_top, shift, BG, c, esc(it["label"]), round(it["score"])))
    return ('<div style="position:relative;height:118px;margin:6px 0 4px;">%s'
            '<div style="position:absolute;left:0;right:10%%;top:70px;height:14px;background:%s;"></div>'
            '<div class="rsd-tape-thin" style="position:absolute;left:90%%;right:0;top:70px;height:14px;"></div>'
            '<div style="position:absolute;left:0;right:0;top:86px;height:8px;background:repeating-linear-gradient(90deg,%s 0 1px,transparent 1px 10%%);"></div>'
            '<div class="m" style="position:absolute;left:0;top:98px;font-size:10px;color:%s;">开仓</div>'
            '<div class="m" style="position:absolute;right:0;top:98px;font-size:10px;font-weight:700;color:%s;">强平</div></div>'
            % (marks, LINE2, STENCIL, MUTED, ORANGE))


def evidence_line(evidence: list[dict]) -> str:
    spans = "".join('<span><span style="color:%s;">%s</span> %s</span>'
                    % (GREEN if e["status"] == "ok" else ORANGE, "OK" if e["status"] == "ok" else "WARN", esc(e["text"]))
                    for e in evidence)
    return '<div class="m" style="display:flex;gap:8px 16px;flex-wrap:wrap;font-size:12px;color:%s;border-top:1px dashed #3a3831;padding-top:10px;">%s</div>' % (MUTED, spans)


def suggestion_card(s: dict) -> str:
    if s.get("score") is None:
        return ('<div style="background:%s;border:1px solid %s;border-left:2px dashed %s;padding:12px 14px;">'
                '<div style="font-size:15px;font-weight:600;">%s</div><div style="font-size:12px;color:%s;margin-top:4px;">%s</div></div>'
                % (BG, LINE, STENCIL, esc(s.get("title", "")), MUTED, esc(s.get("detail", ""))))
    before, after = s["score_before"], s["score"]
    c = grade_color(after)
    gain = max(after - before, 0)
    return ('<div style="background:%s;border:1px solid %s;border-left:2px dashed %s;padding:12px 14px;display:flex;flex-direction:column;gap:6px;">'
            '<div style="font-size:15px;font-weight:600;">%s</div><div class="m" style="font-size:12px;color:%s;">%s</div>'
            '<div style="display:flex;align-items:center;gap:8px;"><div style="flex-grow:1;height:8px;background:%s;position:relative;">'
            '<div style="position:absolute;left:0;width:%.0f%%;height:8px;background:%s;"></div>'
            '<div style="position:absolute;left:%.0f%%;width:%.0f%%;height:8px;background:%s;"></div></div>'
            '<span class="m" style="font-size:13px;font-weight:700;color:%s;">%d→%d</span></div></div>'
            % (BG, LINE, STENCIL, esc(s["title"]), MUTED, esc(s["detail"]), LINE2,
               before, grade_color(before), before, gain, c, c, round(before), round(after)))


# 结论分点: 按小标题关键词给标签上色 (None = 按安全分档位上色)
_TONES = [(("安全",), None), (("最差", "强平", "连续", "同步", "双重", "亏"), RED),
          (("注意", "周末", "若用", "BTC", "样本"), ORANGE), (("可以考虑", "建议", "调整", "方案"), GREEN),
          (("历史", "最终", "提醒"), MUTED)]
_TOKEN = re.compile(r"(\d{4}-\d{2}-\d{2}(?:\s?~\s?\d{4}-\d{2}-\d{2})?)"
                    r"|([-+−]?\d[\d,]*(?:\.\d+)?\s?(?:%|倍|分|天|年|次|个交易日|个周末|USDT|个)?)")


def _tone(title: str, overall: float | None) -> str:
    for keys, color in _TONES:
        if any(k in title for k in keys):
            return (grade_color(overall) if overall is not None else ORANGE) if color is None else color
    return MUTED


def _highlight(text: str) -> str:
    def rep(m):
        if m.group(1):
            return '<span class="m" style="color:%s;">%s</span>' % (SOFT, m.group(1))
        tok = m.group(2)
        color = RED if tok.startswith(("-", "−")) else TEXT
        return '<span class="m" style="color:%s;background:%s;padding:0 4px;font-weight:700;">%s</span>' % (color, LINE2, tok)
    return _TOKEN.sub(rep, esc(text))


def bullets(md: str, overall: float | None = None) -> str:
    """把 Markdown 分点结论 (模板或大模型写的) 渲染成: 彩色标签 + 数字高亮。解析不了的行照常显示。"""
    rows = ""
    for raw in md.splitlines():
        if not raw.strip():
            continue
        sub = raw.startswith(("  ", "\t"))
        line = re.sub(r"^[-*•]\s+", "", raw.strip())
        m = re.match(r"\*\*(.+?)\*\*\s*[:：]?\s*(.*)", line)
        title, body = (m.group(1).strip(), m.group(2)) if m else ("", line)
        body = body.replace("**", "")
        if sub:
            rows += ('<div style="display:flex;gap:10px;padding:4px 0 4px 106px;">'
                     '<svg width="8" height="8" style="flex:0 0 auto;margin-top:9px;"><rect width="8" height="8" fill="%s"></rect></svg>'
                     '<div style="font-size:14px;line-height:1.7;color:%s;">%s</div></div>' % (GREEN, SOFT, _highlight(line.replace("**", ""))))
            continue
        if not title:
            title = "提醒" if re.search(r"历史|最终|决定", body) else "要点"
        tone = _tone(title, overall)
        rows += ('<div style="display:flex;gap:12px;align-items:flex-start;padding:8px 0;border-bottom:1px dashed %s;">'
                 '<span style="flex:0 0 auto;min-width:94px;text-align:center;background:%s;color:%s;font-weight:700;font-size:12px;padding:3px 8px;letter-spacing:0.04em;">%s</span>'
                 '<div style="font-size:14px;line-height:1.75;color:%s;">%s</div></div>'
                 % (LINE, tone, BG, esc(title), SOFT, _highlight(body)))
    return '<div style="border-top:2px solid %s;margin:6px 0 4px;">%s</div>' % (LINE, rows)


def account_overview(items: list[tuple[str, float, str]], collateral: list[tuple[str, float, str]],
                     eff_lev: float | None, equity: float, pnl: float, gross: float) -> str:
    """持仓面板右侧的「账户全貌」: 仓位构成 / 保证金构成 / 有效杠杆 / 未实现盈亏。"""
    def bar(parts):
        total = sum(v for _, v, _ in parts) or 1.0
        segs = "".join('<div style="width:%.2f%%;background:%s;"></div>' % (100 * v / total, c) for _, v, c in parts)
        legend = " · ".join("%s %.0f%%" % (esc(n), 100 * v / total) for n, v, _ in parts)
        return ('<div style="display:flex;height:20px;margin-top:6px;">%s</div>'
                '<div class="m" style="font-size:11px;color:%s;margin-top:5px;line-height:1.7;">%s</div>' % (segs, MUTED, legend))

    lev_html = ""
    if eff_lev is not None:
        lev_html = ('<div><div style="font-size:11px;color:%s;">有效杠杆</div>'
                    '<div class="m" style="font-size:28px;font-weight:700;line-height:1.1;">%.2f×</div>'
                    '<div style="height:8px;background:%s;margin-top:6px;position:relative;">'
                    '<div style="width:%.0f%%;height:8px;background:%s;"></div>'
                    '<div style="position:absolute;left:50%%;top:-3px;height:14px;border-left:1px dashed %s;"></div></div>'
                    '<div style="font-size:11px;color:%s;margin-top:4px;">名义 ÷ 权益; 虚线 = 5 倍</div></div>'
                    % (MUTED, eff_lev, LINE2, min(100.0, 100 * eff_lev / 10), ORANGE, STENCIL, MUTED))
    pc = GREEN if pnl > 0 else (RED if pnl < 0 else MUTED)
    pnl_txt = "%s%s USDT" % ("+" if pnl > 0 else ("−" if pnl < 0 else ""), format(round(abs(pnl)), ","))
    return ('<div style="display:flex;flex-direction:column;gap:16px;">'
            '<div style="font-size:13px;font-weight:700;letter-spacing:0.06em;color:%s;">账户全貌</div>'
            '<div><div style="font-size:11px;color:%s;">仓位构成 (名义 %s)</div>%s</div>'
            '<div><div style="font-size:11px;color:%s;">保证金构成 (权益 %s)</div>%s</div>%s'
            '<div style="border-top:1px dashed #3a3831;padding-top:12px;">'
            '<div style="font-size:11px;color:%s;">未实现盈亏</div>'
            '<div class="m" style="font-size:22px;font-weight:700;color:%s;">%s</div>'
            '<div style="font-size:11px;color:%s;">填了买入价的仓位才算; 当前价来自 Yahoo</div></div></div>'
            % (SOFT, MUTED, format(round(gross), ","), bar(items) if items else "",
               MUTED, format(round(equity), ","), bar(collateral) if collateral else "", lev_html,
               MUTED, pc, pnl_txt, MUTED))


def metric_card(m: dict) -> str:
    """雷达里的一个指标卡: 状态点 + 标签 + 数值 (多来源时同时列出) + 说明。"""
    ok = m["status"] == "ok"
    color = GREEN if ok else ORANGE
    note = ('<div style="font-size:11px;color:%s;line-height:1.5;">%s</div>' % (STENCIL, esc(m["note"]))) if m.get("note") else ""
    return ('<div style="border:1px solid %s;background:%s;padding:12px 14px;display:flex;flex-direction:column;gap:6px;height:100%%;">'
            '<div style="display:flex;align-items:center;gap:8px;"><svg width="8" height="8"><rect width="8" height="8" fill="%s"></rect></svg>'
            '<span style="font-size:13px;color:%s;">%s</span></div>'
            '<div style="font-size:14px;line-height:1.6;color:%s;">%s</div>%s</div>'
            % (LINE, BG, color, MUTED, esc(m["label"]), TEXT, _highlight(m["display"]), note))


def earnings_table(rows: list[dict]) -> str:
    """下次财报日: 越近越红; 推算出来的明确标注。"""
    body = ""
    for r in rows:
        days = r.get("days")
        color = MUTED if days is None else (RED if days <= 7 else (ORANGE if days <= 21 else GREEN))
        when = "—" if not r.get("date") else "%s · %d 天后" % (r["date"], days)
        tag = ('<span style="background:%s;color:%s;font-size:11px;font-weight:700;padding:1px 6px;">估算</span>' % (ORANGE, BG)) \
            if r.get("estimated") and r.get("date") else ""
        body += ('<tr><td class="m">%s</td><td class="m" style="color:%s;">%s</td><td>%s %s</td></tr>'
                 % (esc(r["symbol"]), color, esc(when), esc(r["source"]), tag))
    return ('<table class="rsd-table"><tr><th>标的</th><th>下次财报</th><th>来源</th></tr>%s</table>' % body)


def worst_table(rows: list[dict], k: int = 5) -> str:
    has_eth = bool(rows) and "ETH 当日最低" in rows[0]
    body = ""
    for i, r in enumerate(rows[:k]):
        first = i == 0
        body += ('<tr%s><td class="m"%s>%s</td><td class="m" style="text-align:right;color:%s;%s">−%.1f%%</td>'
                 '<td class="m" style="text-align:right;">%s</td>%s<td class="m">%s</td><td>%s</td></tr>'
                 % (' style="background:#221a12;"' if first else "", ' style="border-left:3px solid %s;"' % RED if first else "",
                    esc(r["日期"]), RED, "font-weight:700;" if first else "", r["账户亏损 %"], esc(r["BTC 当日最低"]),
                    '<td class="m" style="text-align:right;">%s</td>' % esc(r["ETH 当日最低"]) if has_eth else "",
                    esc(str(r["最拖累"]).split(" ")[0]), "是" if r["强平"] else "否"))
    return ('<table class="rsd-table"><tr><th>日期</th><th style="text-align:right;">账户亏损</th>'
            '<th style="text-align:right;">BTC 当日最低</th>%s<th>最拖累</th><th>强平</th></tr>%s</table>'
            % ('<th style="text-align:right;">ETH 当日最低</th>' if has_eth else "", body))
