"""分点结论的渲染 (纯字符串, 不联网不起服务)。  python -X utf8 tests/test_ui_bullets.py

来历: 09-16 Ivan 截图发现「可以考虑」那行正文是空的, 两条建议悬在它和「提醒」之间 ——
子条目当时是单独一行、左边距写死 106px, 不属于任何一条。这里钉住「子条目必须在父条目的格子里」。
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from desk import ui                                                    # noqa: E402

fails = 0


def check(name, got, want):
    global fails
    ok = got == want
    fails += not ok
    print("%s  %-44s got=%s want=%s" % ("PASS" if ok else "FAIL", name, got, want))


MD = """- **安全总分**: 54 分 (警惕) · 短板: 连续 5 日下跌
- **最差单日**: 2024-08-05 · 账户亏损 34.1%
- **可以考虑**:
    - 所有仓位同比例缩到 87% (名义 35,000 → 30,352), 总分 54 → 60
    - 把最拖累的 NVDA 做多 砍半 (最差 10 天里 9 天是它亏最多), 总分 54 → 64
- 历史不代表未来, 最终由你决定"""

html = ui.bullets(MD, 54)
# 每个顶层条目一行。按行首标记切, 不能用非贪婪正则配 </div></div> —— 子条目自己也有嵌套 div, 会被截断
ROW = '<div style="display:flex;gap:12px;align-items:flex-start;'
rows = [ROW + seg for seg in html.split(ROW)[1:]]
check("顶层条目数", len(rows), 4)                      # 总分 / 最差单日 / 可以考虑 / 提醒

consider = [r for r in rows if "可以考虑" in r]
check("可以考虑 只出现在一行里", len(consider), 1)
check("建议 1 在可以考虑这一行内", "同比例缩到" in consider[0], True)
check("建议 2 在可以考虑这一行内", "最拖累" in consider[0], True)

remind = [r for r in rows if "历史不代表未来" in r]
check("提醒自成一行", len(remind), 1)
check("建议没掉进提醒那行", "同比例缩到" in remind[0], False)
check("提醒行的标签是「提醒」", ">提醒<" in remind[0], True)

# 数字仍然高亮 (标签 + 等宽 + 背景), 日期用柔色
check("百分比被高亮", "34.1%</span>" in html, True)
check("日期被高亮", "2024-08-05</span>" in html, True)

# 大模型写的结论通常没有子条目, 不能因此出错
flat = ui.bullets("- **要点**: 仓位过重\n- 历史不代表未来", 30)
check("没有子条目也能渲染", flat.count('align-items:flex-start') , 2)

# 没有 ** 标题的行: 含「历史/最终/决定」归「提醒」, 其余归「要点」
check("无标题行默认归要点", ">要点<" in ui.bullets("- 仓位偏重单一标的", 50), True)

print("\n%s" % ("全部通过" if not fails else "%d 项失败" % fails))
sys.exit(1 if fails else 0)
