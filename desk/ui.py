"""
网页样式与 HTML 片段 —— 白底模板 (2026-09-17 定稿, 取代原来的橙黑警示风)。

只负责展示: 这里不计算任何数字, 所有数值都来自 portfolio_card / card / score 已经算好的结果。
注意: st.markdown 会把行首缩进 4 格的内容当代码块, 所以所有 HTML 都拼成单行。

配色只做三件事: 中性灰做结构 / 语义色 (红黄绿) 只给分数和涨跌 / 黑色只给主按钮。
原生组件 (表格、输入框、滑块) 的底色来自 .streamlit/config.toml 的 base="light", CSS 盖不住那一层。
"""
from __future__ import annotations

import re

BG, CARD, LINE, LINE2 = "#f6f7f8", "#ffffff", "#e6e7ea", "#f0f1f3"
INK, TEXT, SOFT, MUTED = "#16181d", "#2c2f36", "#5b606b", "#8b8f98"
RED, AMBER, GREEN, BLUE = "#d64027", "#e08a1e", "#1a9e5f", "#2f6fd0"
ORANGE = AMBER              # 旧名字, 保留给还没改的调用点

CSS = """<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=Noto+Sans+SC:wght@400;500;700&display=swap');

html, body, [class*="st-"], button, input, textarea, select {
  font-family: "Noto Sans SC", "PingFang SC", system-ui, sans-serif; }
.m, .m * { font-family: "JetBrains Mono", ui-monospace, Menlo, monospace !important; font-variant-numeric: tabular-nums; }
[data-testid="stIconMaterial"] { font-family: "Material Symbols Rounded" !important; }

.stApp { background: #f6f7f8; }
/* 顶部留白必须给 Streamlit Cloud 的工具条 (Share / ☆ / ✎) 让位 ——
   那条是盖在页面上的, 压到 1.1rem 会把第一行 (标签栏) 整个藏到它下面。
   登录的人看得见工具条, 匿名访客看不见, 所以本地和匿名截图都正常, 只有 app 主人看不到标签。 */
.block-container { padding-top: 3.6rem !important; padding-bottom: 3rem !important; max-width: 1240px; }
[data-testid="stToolbar"] { z-index: 5; }
#MainMenu, footer, [data-testid="stDecoration"] { display: none; }

/* 标签栏: 文字 + 下划线, 不用色块 */
[data-testid="stTabs"] [role="tablist"] { gap: 26px !important; border-bottom: 1px solid #e6e7ea; margin-bottom: 4px; }
[data-testid="stTabs"] [role="tab"] { padding: 6px 0 !important; height: auto !important; min-width: 0 !important; flex: none !important; }
[data-testid="stTabs"] [role="tab"], [data-testid="stTabs"] [role="tab"] * { color: #8b8f98 !important; font-size: 15px !important; }
[data-testid="stTabs"] [role="tab"][aria-selected="true"], [data-testid="stTabs"] [role="tab"][aria-selected="true"] * {
  color: #16181d !important; font-weight: 700 !important; }
[data-baseweb="tab-highlight"] { background: #16181d !important; height: 2px !important; }
[data-baseweb="tab-border"], .react-aria-SelectionIndicator { display: none !important; }

/* 卡片: 每个 st.container(key=...) 都是一张卡 (panel_ 是单笔 / 雷达两页沿用的旧键名)。
   用精确 class: [class*="st-key-card_"] 这种前缀写法会连带命中 key 同前缀的控件,
   09-17 实测按钮 key="guide_dismiss" 就被套了第二层边框。 */
div.st-key-card_hold, div.st-key-card_score, div.st-key-card_plans, div.st-key-card_quake, div.st-key-card_empty,
div.st-key-panel_01, div.st-key-panel_02, div.st-key-panel_03, div.st-key-panel_04, div.st-key-panel_05, div.st-key-panel_06 {
  background: #ffffff; border: 1px solid #e6e7ea; border-radius: 10px; padding: 16px 20px 18px; margin-bottom: 14px; }
div.st-key-guide {
  background: #ffffff; border: 1px solid #e6e7ea; border-radius: 8px; padding: 12px 14px 14px; margin-bottom: 12px; }
div.st-key-ecard_1, div.st-key-ecard_2, div.st-key-ecard_3 {
  background: #ffffff; border: 1px solid #e6e7ea; border-radius: 10px; padding: 14px 16px 16px; height: 100%; }
/* 卡片里最后一个元素的下外边距会顶到边框上, 抹掉 */
div[class*="st-key-card_"] > div > div > div:last-child, div.st-key-guide > div > div > div:last-child { margin-bottom: 0; }

/* 按钮 */
.stButton button, [data-testid="stPopover"] button {
  border-radius: 7px !important; border: 1px solid #e6e7ea !important; background: #ffffff !important; color: #16181d !important;
  font-weight: 500 !important; }
.stButton button:hover, [data-testid="stPopover"] button:hover { border-color: #16181d !important; color: #16181d !important; }
.stButton button[kind="primary"], [data-testid^="stBaseButton-primary"] {
  background: #16181d !important; color: #ffffff !important; border: 1px solid #16181d !important; font-weight: 500 !important; }
[data-testid="stLinkButton"] a {
  border-radius: 7px !important; border: 1px solid #e6e7ea !important; color: #16181d !important; background: #ffffff !important; }

/* 示例 chip: 圆角小按钮 */
div[class*="st-key-pf_ex_"] button, div[class*="st-key-pf_fu_"] button, div[class*="st-key-tr_ex_"] button {
  min-height: 28px !important; padding: 1px 12px !important; border-radius: 20px !important; }
div[class*="st-key-pf_ex_"] button p, div[class*="st-key-pf_fu_"] button p, div[class*="st-key-tr_ex_"] button p {
  font-size: 12.5px !important; color: #5b606b !important; }

/* 输入框 / 表格 */
[data-baseweb="input"], [data-baseweb="select"] > div {
  border-radius: 7px !important; background: #ffffff !important; border: 1px solid #e6e7ea !important; }
[data-baseweb="input"]:focus-within { border-color: #16181d !important; }
[data-baseweb="base-input"] { background: transparent !important; }
[data-testid="stNumberInputContainer"] { border-radius: 7px !important; border: 1px solid #e6e7ea !important; }
div[class*="st-key-pf_query"] input, div[class*="st-key-query"] input { height: 36px; font-size: 14px; }
[data-testid="stDataFrameResizable"] { border: 1px solid #e6e7ea !important; border-radius: 8px; }
[data-testid="stExpander"] details { border-radius: 8px; border-color: #e6e7ea; background: #ffffff; }
[data-testid="stExpander"] summary { font-size: 13px; color: #5b606b; }

/* 侧栏 */
[data-testid="stSidebar"] { background: #ffffff; border-right: 1px solid #e6e7ea; }
[data-testid="stSidebar"] label p, [data-testid="stSidebar"] [data-testid="stWidgetLabel"] p { color: #2c2f36; font-size: 13px; }
[data-testid="stSidebar"] .block-container { padding-top: 1.2rem; }
[data-testid="stSidebar"] [data-testid="stSliderTickBarMin"], [data-testid="stSidebar"] [data-testid="stSliderTickBarMax"] { display: none; }

/* 分数四格: 窄屏折成两列 */
.rsd-grid4 { display: grid; grid-template-columns: 1.25fr 1fr 1fr 1fr; gap: 0; }
@media (max-width: 820px) {
  .rsd-grid4 { grid-template-columns: 1fr 1fr; row-gap: 14px; }
  .rsd-grid4 > div { border-right: 0 !important; padding-left: 0 !important; }
}

/* 通用小件 */
.rsd-table { border-collapse: collapse; width: 100%; }
.rsd-table th { text-align: left; font-weight: 500; color: #8b8f98; font-size: 11.5px; padding: 7px 10px; border-bottom: 1px solid #e6e7ea; }
.rsd-table td { padding: 9px 10px; font-size: 13px; border-bottom: 1px solid #f0f1f3; color: #2c2f36; }
@keyframes rsdblink { 0%, 100% { opacity: 1; } 50% { opacity: 0.2; } }
.rsd-blink { animation: rsdblink 1s ease-in-out infinite; }
</style>"""


def esc(x) -> str:
    return str(x).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def grade_color(s: float) -> str:
    return RED if s < 30 else (AMBER if s < 60 else GREEN)


# ---------------------------------------------------------------- 侧栏
def brand() -> str:
    return ('<div style="font-weight:700;font-size:19px;color:%s;line-height:1.3;letter-spacing:-0.01em;">rToken Stress Desk'
            '<div style="font-weight:400;font-size:12px;color:%s;margin-top:3px;">开仓前的压力测试台</div></div>'
            % (INK, MUTED))


def side_head(text: str) -> str:
    return '<div style="font-size:11.5px;color:%s;letter-spacing:0.04em;margin:14px 0 2px;">%s</div>' % (MUTED, esc(text))


def status_rows(items: list[tuple]) -> str:
    """侧栏的运行状态: ok=True 绿 / False 红 / None 灰 / "pending" 黄色闪烁。"""
    rows = ""
    for ok, label, right in items:
        color = {True: GREEN, False: RED, None: "#c9ccd2"}.get(ok, AMBER)
        cls = ' class="rsd-blink"' if ok == "pending" else ""
        rows += ('<div style="display:flex;align-items:center;gap:8px;font-size:12.5px;padding:2.5px 0;">'
                 '<span%s style="width:6px;height:6px;border-radius:50%%;background:%s;flex:0 0 auto;"></span>'
                 '<span style="color:%s;">%s</span>'
                 '<span class="m" style="margin-left:auto;font-size:11px;color:%s;">%s</span></div>'
                 % (cls, color, TEXT, esc(label), MUTED, esc(right)))
    return rows


def assume_note(text: str) -> str:
    return '<div style="font-size:11px;color:%s;line-height:1.6;margin-top:6px;">%s</div>' % (MUTED, esc(text))


# ---------------------------------------------------------------- 卡片骨架
def card_head(title: str, hint: str = "") -> str:
    return ('<div style="display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin-bottom:2px;">'
            '<span style="font-size:14.5px;font-weight:700;color:%s;">%s</span>'
            '<span style="margin-left:auto;font-size:11.5px;color:%s;text-align:right;">%s</span></div>'
            % (INK, esc(title), MUTED, esc(hint)))


# 旧调用点还在用 panel_title(num, title, sub): 编号在白底模板里不再显示
def panel_title(num: str, title: str, sub: str = "") -> str:
    return card_head(title, sub)


def guide_strip() -> str:
    steps = [("1", "说出你的持仓", "一句话, 或在表里填"),
             ("2", "点「运行压力测试」", "约 10 秒"),
             ("3", "看分数和调整方案", "分数越低越危险")]
    cells = ""
    for i, (n, t, s) in enumerate(steps):
        border = "" if i == len(steps) - 1 else "border-right:1px solid %s;" % LINE2
        cells += ('<div style="flex:1 1 190px;display:flex;gap:10px;padding:4px 15px 4px 0;%s">'
                  '<span class="m" style="font-weight:700;color:%s;font-size:13px;background:%s;width:21px;height:21px;'
                  'display:flex;align-items:center;justify-content:center;flex:0 0 auto;border-radius:4px;">%s</span>'
                  '<span><b style="display:block;font-size:13px;color:%s;font-weight:500;">%s</b>'
                  '<span style="color:%s;font-size:11.5px;">%s</span></span></div>'
                  % (border, INK, LINE2, n, INK, esc(t), MUTED, esc(s)))
    return '<div style="display:flex;flex-wrap:wrap;align-items:center;row-gap:8px;">%s</div>' % cells


def empty_card(icon: str, title: str, body: str) -> str:
    return ('<div style="display:flex;flex-direction:column;gap:6px;">'
            '<div style="width:26px;height:26px;border-radius:6px;background:%s;display:flex;align-items:center;'
            'justify-content:center;font-size:14px;color:%s;">%s</div>'
            '<div style="font-size:14px;color:%s;font-weight:500;">%s</div>'
            '<div style="color:%s;font-size:12.5px;min-height:40px;">%s</div></div>'
            % (LINE2, SOFT, esc(icon), INK, esc(title), MUTED, esc(body)))


# ---------------------------------------------------------------- 地震图
def seismograph(dates: list[str], losses: list[float], liq_loss: float, height: int = 190) -> str:
    """5 年地震图: 每个交易日的账户亏损 (向下), 强平线是一条红色虚线。"""
    n = len(losses)
    if n < 2:
        return '<div style="color:%s;">样本不足, 无法绘制</div>' % MUTED
    w = n - 1
    ys = [100 * min(max(x, 0.0), 1.0) for x in losses]
    pts = " ".join("%d,%.1f" % (i, y) for i, y in enumerate(ys))
    liq_top = height * min(max(liq_loss, 0.0), 1.0)
    ticks, seen = [], set()
    for i, d in enumerate(dates):
        y = d[:4]
        if y not in seen:
            seen.add(y)
            if i > 0:
                ticks.append('<span style="position:absolute;left:%.2f%%;">%s</span>' % (100 * i / w, y))
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
        top = height * min(max(losses[i], 0), 1) + 4
        shift = -92 if x > 85 else (-8 if x < 12 else -50)
        labels += ('<div style="position:absolute;left:%.2f%%;top:%.0fpx;height:14px;border-left:1px solid %s;"></div>'
                   '<div class="m" style="position:absolute;left:%.2f%%;top:%.0fpx;transform:translateX(%d%%);font-size:11px;'
                   'font-weight:500;color:%s;background:%s;padding:1px 6px;white-space:nowrap;border:1px solid %s;border-radius:4px;">%s −%.1f%%</div>'
                   % (x, top, MUTED, x, top + 16, shift, TEXT, CARD, LINE, esc(dates[i]), 100 * losses[i]))
    return ('<div style="position:relative;height:%dpx;">'
            '<svg width="100%%" height="%d" viewBox="0 0 %d 100" preserveAspectRatio="none" style="position:absolute;left:0;top:0;">'
            '<line x1="0" y1="0.5" x2="%d" y2="0.5" stroke="%s" stroke-width="1" vector-effect="non-scaling-stroke"></line>'
            '<line x1="0" y1="50" x2="%d" y2="50" stroke="%s" stroke-dasharray="3 5" vector-effect="non-scaling-stroke"></line>'
            '<polyline points="%s" fill="none" stroke="%s" stroke-width="1" vector-effect="non-scaling-stroke" opacity="0.9"></polyline></svg>'
            '<div style="position:absolute;left:0;right:0;top:%.0fpx;border-top:1.5px dashed %s;"></div>'
            '<div class="m" style="position:absolute;right:0;top:%.0fpx;font-size:11px;color:%s;background:%s;padding:0 6px;">强平线 · 亏损 %.1f%%</div>'
            '<div class="m" style="position:absolute;left:0;top:2px;font-size:10px;color:%s;">0%%</div>'
            '<div class="m" style="position:absolute;left:0;top:%dpx;font-size:10px;color:%s;">50%%</div>%s</div>'
            '<div class="m" style="position:relative;height:16px;margin-top:2px;font-size:10px;color:%s;">%s</div>'
            % (height, height, w, w, LINE, w, LINE2, pts, RED,
               liq_top, RED, max(liq_top - 20, 0), RED, CARD, 100 * liq_loss, MUTED, height // 2 + 2, MUTED, labels,
               MUTED, "".join(ticks)))


# ---------------------------------------------------------------- 分数
def score_grid(overall: float, grade: str, weakest_label: str, items: list[dict], verdict: str, rule: str) -> str:
    """四格: 总分 + 三个评分项 (细进度条 = 用掉多少强平距离)。"""
    c = grade_color(overall)
    cells = ('<div style="padding:2px 20px 2px 0;border-right:1px solid %s;">'
             '<div style="font-size:12px;color:%s;">安全总分</div>'
             '<div class="m" style="font-size:40px;font-weight:700;line-height:1.15;color:%s;">%d'
             '<span style="font-size:12px;font-weight:700;margin-left:7px;">%s</span></div>'
             '<div style="font-size:11.5px;color:%s;">短板: %s</div></div>'
             % (LINE2, MUTED, c, round(overall), esc(grade), MUTED, esc(weakest_label)))
    for k, it in enumerate(items):
        col = grade_color(it["score"])
        used = min(max(100 - it["score"], 0), 100)
        border = "" if k == len(items) - 1 else "border-right:1px solid %s;" % LINE2
        cells += ('<div style="padding:2px 20px;%s">'
                  '<div style="font-size:12px;color:%s;">%s</div>'
                  '<div class="m" style="font-size:40px;font-weight:700;line-height:1.15;color:%s;">%d</div>'
                  '<div style="height:3px;background:%s;margin:8px 0 7px;position:relative;">'
                  '<i style="position:absolute;left:0;top:0;bottom:0;width:%.0f%%;background:%s;"></i></div>'
                  '<div style="font-size:11.5px;color:%s;">%s</div></div>'
                  % (border, MUTED, esc(it["label"]), col, round(it["score"]), LINE2, used, col, MUTED, esc(it["detail"])))
    return ('<div class="rsd-grid4">%s</div>'
            '<div style="margin-top:14px;padding-top:13px;border-top:1px solid %s;font-size:14px;color:%s;">%s'
            '<div style="color:%s;font-size:12.5px;margin-top:3px;">%s</div></div>'
            % (cells, LINE2, INK, verdict, MUTED, esc(rule)))


def score_block(overall: float, weakest_label: str, grade: str) -> str:
    """单笔页面还在用的小号分数块。"""
    c = grade_color(overall)
    return ('<div style="display:flex;align-items:center;gap:16px;margin:2px 0 8px;">'
            '<div class="m" style="font-size:52px;line-height:0.9;font-weight:700;color:%s;">%d</div>'
            '<div><div style="font-size:15px;font-weight:700;color:%s;">%s</div>'
            '<div style="font-size:12.5px;color:%s;">短板: %s</div>'
            '<div style="font-size:11.5px;color:%s;">总分取各项最低分, 不取平均</div></div></div>'
            % (c, round(overall), c, esc(grade), SOFT, esc(weakest_label), MUTED))


def compare_line(before: float, after: float) -> str:
    d = round(after) - round(before)
    c = GREEN if d > 0 else (RED if d < 0 else MUTED)
    return ('<div class="m" style="display:flex;align-items:center;gap:9px;font-size:13px;color:%s;margin:-2px 0 8px;">'
            '<span>修改前</span><span style="color:%s;font-weight:700;">%d</span><span>→ 现在</span>'
            '<span style="color:%s;font-weight:700;">%d</span>'
            '<span style="background:%s;color:%s;font-weight:700;padding:0 7px;border-radius:5px;">%+d</span></div>'
            % (MUTED, grade_color(before), round(before), grade_color(after), round(after),
               "#e7f6ee" if d > 0 else LINE2, c, d))


RULER_SHORT = {"single_day": "单日", "multi_day": "连续5日", "presets": "预设", "weekend": "周末"}


def ruler(items: list[dict], end_label: str = "强平") -> str:
    """强平距离尺: 每个评分项用掉多少强平距离 (= 100 − 分数)。相邻标记靠太近时上下错开。"""
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
        label_top = 0 if row == 0 else 26
        shift = -92 if p > 85 else (-8 if p < 12 else -50)
        marks += ('<div style="position:absolute;left:%.1f%%;top:%dpx;height:%dpx;border-left:1px solid %s;"></div>'
                  '<div class="m" style="position:absolute;left:%.1f%%;top:%dpx;transform:translateX(%d%%);font-size:11.5px;'
                  'font-weight:700;color:%s;background:%s;padding:1px 7px;white-space:nowrap;border:1px solid %s;border-radius:4px;">%s %d</div>'
                  % (p, label_top + 18, 62 - label_top - 18, c, p, label_top, shift, c, CARD, LINE,
                     esc(it["label"]), round(it["score"])))
    return ('<div style="position:relative;height:100px;margin:4px 0;">%s'
            '<div style="position:absolute;left:0;right:0;top:62px;height:8px;background:%s;border-radius:4px;"></div>'
            '<div style="position:absolute;left:90%%;right:0;top:62px;height:8px;background:%s;border-radius:0 4px 4px 0;opacity:.85;"></div>'
            '<div class="m" style="position:absolute;left:0;top:76px;font-size:10px;color:%s;">开仓</div>'
            '<div class="m" style="position:absolute;right:0;top:76px;font-size:10px;font-weight:700;color:%s;">%s</div></div>'
            % (marks, LINE2, RED, MUTED, RED, esc(end_label)))


def evidence_bar(evidence: list[dict]) -> str:
    """底部证据条: ✔ 绿 / ! 黄。"""
    spans = ""
    for e in evidence:
        ok = e["status"] == "ok"
        spans += ('<span style="display:inline-flex;align-items:flex-start;gap:6px;">'
                  '<span style="color:%s;font-weight:700;">%s</span><span>%s</span></span>'
                  % (GREEN if ok else AMBER, "✔" if ok else "!", esc(e["text"])))
    return ('<div style="display:flex;gap:24px;flex-wrap:wrap;font-size:12.5px;color:%s;background:%s;'
            'border:1px solid %s;border-radius:10px;padding:11px 18px;">%s</div>' % (SOFT, CARD, LINE, spans))


def evidence_line(evidence: list[dict]) -> str:
    return evidence_bar(evidence)


def suggestion_card(s: dict) -> str:
    if s.get("score") is None:
        return ('<div><div style="font-size:13.5px;font-weight:500;color:%s;">%s</div>'
                '<div style="font-size:12px;color:%s;margin-top:4px;">%s</div></div>'
                % (INK, esc(s.get("title", "")), MUTED, esc(s.get("detail", ""))))
    before, after = s["score_before"], s["score"]
    gain = round(after) - round(before)
    return ('<div><div style="font-size:13.5px;font-weight:500;color:%s;">%s</div>'
            '<div style="font-size:12px;color:%s;margin:2px 0 9px;">%s</div>'
            '<div class="m" style="display:flex;align-items:baseline;gap:8px;font-weight:700;">'
            '<span style="color:%s;font-size:17px;">%d</span><span style="color:%s;">→</span>'
            '<span style="color:%s;font-size:23px;">%d</span>'
            '<span style="background:%s;color:%s;font-size:11px;padding:1px 7px;border-radius:5px;">%+d</span></div></div>'
            % (INK, esc(s.get("title", "")), MUTED, esc(s.get("detail", "")),
               grade_color(before), round(before), MUTED, grade_color(after), round(after),
               "#e7f6ee" if gain > 0 else LINE2, GREEN if gain > 0 else MUTED, gain))


# ---------------------------------------------------------------- 分点结论
# 小标题关键词 -> 色调 (None = 按安全分档位上色)
_TONES = [(("安全",), None), (("最差", "强平", "连续", "同步", "双重", "亏"), RED),
          (("注意", "周末", "若用", "BTC", "样本"), AMBER), (("可以考虑", "建议", "调整", "方案"), GREEN),
          (("历史", "最终", "提醒"), MUTED)]
_TINT = {RED: "#fdecea", AMBER: "#fdf3e4", GREEN: "#e7f6ee", MUTED: LINE2}
_TOKEN = re.compile(r"(\d{4}-\d{2}-\d{2}(?:\s?~\s?\d{4}-\d{2}-\d{2})?)"
                    r"|([-+−]?\d[\d,]*(?:\.\d+)?\s?(?:%|倍|分|天|年|次|个交易日|个周末|USDT|个)?)")


def _tone(title: str, overall: float | None) -> str:
    for keys, color in _TONES:
        if any(k in title for k in keys):
            return (grade_color(overall) if overall is not None else AMBER) if color is None else color
    return MUTED


def _highlight(text: str) -> str:
    def rep(m):
        if m.group(1):
            return '<span class="m" style="color:%s;">%s</span>' % (SOFT, m.group(1))
        tok = m.group(2)
        color = RED if tok.startswith(("-", "−")) else INK
        return '<span class="m" style="color:%s;font-weight:700;">%s</span>' % (color, tok)
    return _TOKEN.sub(rep, esc(text))


def bullets(md: str, overall: float | None = None) -> str:
    """
    把 Markdown 分点结论 (模板或大模型写的) 渲染成: 彩色标签 + 数字高亮。解析不了的行照常显示。
    子条目 (缩进行) 必须画进它所属那条的正文格里 —— 早先是单独一行、左边距写死,
    结果「可以考虑」那行正文空着, 建议悬在它和「提醒」之间, 看起来像掉到下一条去了。
    """
    blocks: list[dict] = []
    for raw in md.splitlines():
        if not raw.strip():
            continue
        line = re.sub(r"^[-*•]\s+", "", raw.strip())
        if raw.startswith(("  ", "\t", "    ")) and blocks:
            blocks[-1]["subs"].append(line.replace("**", ""))
            continue
        m = re.match(r"\*\*(.+?)\*\*\s*[:：]?\s*(.*)", line)
        title, body = (m.group(1).strip(), m.group(2)) if m else ("", line)
        blocks.append({"title": title, "body": body.replace("**", ""), "subs": []})

    rows = ""
    for b in blocks:
        title = b["title"] or ("提醒" if re.search(r"历史|最终|决定", b["body"]) else "要点")
        tone = _tone(title, overall)
        body = '<div style="font-size:13.5px;line-height:1.75;color:%s;">%s</div>' % (SOFT, _highlight(b["body"])) \
            if b["body"].strip() else ""
        for sub in b["subs"]:
            body += ('<div style="display:flex;gap:9px;padding:3px 0;">'
                     '<span style="flex:0 0 auto;margin-top:8px;width:6px;height:6px;border-radius:50%%;background:%s;"></span>'
                     '<div style="font-size:13.5px;line-height:1.7;color:%s;">%s</div></div>' % (GREEN, SOFT, _highlight(sub)))
        rows += ('<div style="display:flex;gap:12px;align-items:flex-start;padding:7px 0;border-bottom:1px solid %s;">'
                 '<span style="flex:0 0 auto;min-width:78px;text-align:center;background:%s;color:%s;font-weight:500;'
                 'font-size:11px;padding:2px 8px;border-radius:5px;">%s</span>'
                 '<div style="flex:1 1 auto;min-width:0;">%s</div></div>'
                 % (LINE2, _TINT.get(tone, LINE2), tone, esc(title), body))
    return '<div style="margin:6px 0 2px;">%s</div>' % rows


# ---------------------------------------------------------------- 持仓汇总
def stale_note(text: str) -> str:
    """结果过期的提示条 (表格改了但还没重新运行)。"""
    return ('<div style="display:flex;align-items:center;gap:8px;background:#fdf7ec;border:1px solid #f2e2c4;'
            'border-radius:7px;padding:8px 12px;margin:2px 0 8px;font-size:12.5px;color:#8a6417;">'
            '<span style="font-weight:700;">!</span><span>%s</span></div>' % esc(text))


def summary_line(pairs: list[tuple[str, str]]) -> str:
    """表格下面那一行: 保证金 / 权益 / 总仓位 / 有效杠杆 / 未实现盈亏。"""
    cells = "".join('<span style="color:%s;">%s <b class="m" style="color:%s;font-size:13.5px;font-weight:700;">%s</b></span>'
                    % (MUTED, esc(k), INK, esc(v)) for k, v in pairs)
    return ('<div style="display:flex;gap:10px 22px;flex-wrap:wrap;margin:10px 0 20px;padding-top:11px;'
            'border-top:1px solid %s;font-size:12.5px;line-height:1.9;">%s</div>' % (LINE2, cells))


def mix_bar(items: list[tuple[str, float, str]], total: float) -> str:
    """仓位构成 / 保证金构成的细条 (侧栏与持仓卡用)。"""
    if total <= 0:
        return ""
    seg = "".join('<span style="width:%.1f%%;background:%s;"></span>' % (100 * v / total, c) for _, v, c in items)
    txt = " · ".join('<span style="color:%s;">%s %.0f%%</span>' % (MUTED, esc(n), 100 * v / total) for n, v, c in items)
    return ('<div style="display:flex;height:7px;border-radius:4px;overflow:hidden;background:%s;">%s</div>'
            '<div class="m" style="font-size:11px;margin-top:5px;">%s</div>' % (LINE2, seg, txt))


def metric_card(m: dict) -> str:
    """雷达里的一个指标卡: 状态点 + 标签 + 数值 (多来源时同时列出) + 说明。"""
    ok = m.get("ok", True)
    vals = m.get("values") or ([m.get("display")] if m.get("display") else [])
    body = "".join('<div class="m" style="font-size:%dpx;font-weight:700;color:%s;line-height:1.45;">%s</div>'
                   % ((22 if len(str(v)) <= 22 else 15) if i == 0 else 13, INK if i == 0 else SOFT, esc(v))
                   for i, v in enumerate(vals))
    return ('<div style="display:flex;flex-direction:column;gap:3px;">'
            '<div style="display:flex;align-items:center;gap:7px;font-size:12px;color:%s;">'
            '<span style="width:6px;height:6px;border-radius:50%%;background:%s;"></span>%s</div>%s'
            '<div style="font-size:11.5px;color:%s;">%s</div></div>'
            % (MUTED, GREEN if ok else AMBER, esc(m.get("label", "")), body, MUTED, esc(m.get("note", ""))))


def earnings_table(rows: list[dict]) -> str:
    body = ""
    for r in rows:
        days = r.get("days")
        when = "—" if not r.get("date") else "%s · %d 天后" % (r["date"], days)
        color = RED if (days is not None and days <= 7) else (AMBER if (days is not None and days <= 21) else TEXT)
        tag = ('<span style="background:%s;color:%s;font-size:11px;padding:1px 6px;border-radius:4px;">估算</span>' % ("#fdf3e4", "#a2650f")) \
            if r.get("estimated") else ""
        body += ('<tr><td class="m">%s</td><td class="m" style="color:%s;">%s</td><td>%s %s</td></tr>'
                 % (esc(r.get("symbol", "")), color, esc(when), esc(r.get("source", "")), tag))
    return '<table class="rsd-table"><tr><th>标的</th><th>下次财报</th><th>来源</th></tr>%s</table>' % body


def worst_table(rows: list[dict], k: int = 5) -> str:
    has_eth = bool(rows) and "ETH 当日最低" in rows[0]
    body = ""
    for i, r in enumerate(rows[:k]):
        first = i == 0
        body += ('<tr%s><td class="m">%s</td><td class="m" style="text-align:right;color:%s;%s">−%.1f%%</td>'
                 '<td class="m" style="text-align:right;">%s</td>%s<td class="m">%s</td><td>%s</td></tr>'
                 % (' style="background:#fdf7f5;"' if first else "",
                    esc(r["日期"]), RED, "font-weight:700;" if first else "", r["账户亏损 %"], esc(r["BTC 当日最低"]),
                    '<td class="m" style="text-align:right;">%s</td>' % esc(r["ETH 当日最低"]) if has_eth else "",
                    esc(str(r["最拖累"]).split(" ")[0]), "是" if r["强平"] else "否"))
    return ('<table class="rsd-table"><tr><th>日期</th><th style="text-align:right;">账户亏损</th>'
            '<th style="text-align:right;">BTC 当日最低</th>%s<th>最拖累</th><th>强平</th></tr>%s</table>'
            % ('<th style="text-align:right;">ETH 当日最低</th>' if has_eth else "", body))
