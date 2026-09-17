"""
用户路径巡检 (AppTest, 无浏览器): 把新手和老手会走的路都点一遍, 看有没有异常或哑掉的地方。
python -X utf8 tests/test_paths.py

和 test_app.py 的分工: test_app 走「典型流程跑得通」, 这里专挑边角 ——
空表格、没有保证金、乱填标的、极端杠杆、把每个按钮都点一次、每个折叠区都展开一次。
"""
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
from streamlit.testing.v1 import AppTest                               # noqa: E402

from desk import portfolio as pf                                       # noqa: E402

problems = []


def new_app(timeout=600):
    at = AppTest.from_file("app.py", default_timeout=timeout)
    at.run()
    return at


def check(at, tag, extra=""):
    exc = [str(e.value)[:160] for e in at.exception]
    if exc:
        problems.append("%s -> %s" % (tag, exc))
    print("%-34s %s %s" % (tag, "异常: " + str(exc) if exc else "OK", extra))
    return not exc


def run_btn(at, label):
    hits = [b for b in at.button if b.label == label]
    if not hits:
        problems.append("找不到按钮: " + label)
        return False
    hits[0].click()
    at.run()
    return True


t0 = time.time()
at = new_app()
check(at, "首次加载")

# ---- 1. 新手: 什么都不改, 直接点运行 ----
run_btn(at, "运行压力测试")
check(at, "新手: 直接点运行", "总分=%s" % re.findall(r"font-size:40px[^>]*>(\d+)", " ".join(m.value for m in at.markdown))[:1])

# ---- 2. 每个按钮都点一次 (不能有点了报错的) ----
seen = set()
for b in list(at.button):
    if b.label in seen or b.label in ("运行压力测试",):
        continue
    seen.add(b.label)
    try:
        b.click()
        at.run()
        check(at, "点按钮: %s" % b.label[:18])
    except Exception as e:        # noqa: BLE001
        problems.append("点按钮 %s -> %s" % (b.label, type(e).__name__))
        print("点按钮 %-24s 抛异常 %s" % (b.label[:24], type(e).__name__))
        at = new_app()
        run_btn(at, "运行压力测试")

# ---- 3. 折叠区: AppTest 里内容本来就会渲染, 这里只确认数量和标题不为空 ----
at2 = new_app()
run_btn(at2, "运行压力测试")
labels = [e.label for e in at2.expander]
if not labels or any(not l.strip() for l in labels):
    problems.append("折叠区标题有空的: %s" % labels)
check(at2, "折叠区", "%d 个: %s" % (len(labels), " / ".join(l[:12] for l in labels)))

# ---- 4. 边角账户 ----
cases = {
    "只有一笔现货, 没有保证金": pf.Account([pf.Position("NVDA", "long", 5000.0, kind="spot")], usdt=0.0),
    "极小仓位 (10 USDT)": pf.Account([pf.Position("NVDA", "long", 10.0)], usdt=100.0),
    "极高杠杆 (名义 50 倍权益)": pf.Account([pf.Position("NVDA", "long", 50000.0)], usdt=1000.0),
    "十笔仓位": pf.Account([pf.Position(s, "long", 2000.0) for s in
                        ("NVDA", "TSLA", "AAPL", "MSFT", "AMZN", "GOOGL", "META", "SPY", "QQQ", "COIN")], usdt=5000.0),
    "多空混合 + 现货 + ETH 抵押": pf.Account(
        [pf.Position("NVDA", "long", 8000.0), pf.Position("SPY", "short", 4000.0),
         pf.Position("TSM", "long", 6000.0, kind="spot")], usdt=3000.0, btc=0.05, eth=1.0),
}
for tag, acc in cases.items():
    a = new_app()
    a.session_state["pf_account"] = acc
    a.session_state["pf_ver"] = a.session_state["pf_ver"] + 1
    a.session_state["pf_report"] = None
    a.run()
    run_btn(a, "运行压力测试")
    warns = [w.value[:60] for w in a.warning]
    check(a, "组合: " + tag, ("警告: %s" % warns) if warns else "")

# ---- 5. 乱填的句子 (不能崩, 要给出说明) ----
at3 = new_app()
for text in ["", "你好", "做多 不存在的股票 1万", "保证金 0, 做多 NVDA 100万", "!!!???", "做多 NVDA"]:
    at3.text_input(key="pf_query").set_value(text)
    run_btn(at3, "运行压力测试")
    msg = ([e.value[:70] for e in at3.error] + [c.value[:70] for c in at3.caption if c.value.startswith("解析")])[:1]
    check(at3, "乱填: %r" % (text[:16] or "(空)"), "回应: %s" % (msg or "无"))

# ---- 6. 单笔页的边角 ----
at4 = new_app()
for text in ["3 倍做多 AMD 过周末", "100 倍做多 NVDA 过周末", "做多"]:
    at4.text_input(key="query").set_value(text)
    run_btn(at4, "单笔压力测试")
    msg = ([e.value[:80] for e in at4.error] + [w.value[:80] for w in at4.warning])[:1]
    check(at4, "单笔: %r" % text[:16], "回应: %s" % (msg or "出了结果"))

print("\n用时 %.0f 秒" % (time.time() - t0))

# ---- 7. 改了持仓: 不能自动重跑 (会把正在输入的内容冲掉), 要提示结果过期 + 给一个就近的按钮 ----
a5 = new_app()
run_btn(a5, "运行压力测试")
before = a5.session_state["pf_report"].scorecard["overall"]
a5.session_state["pf_account"] = pf.Account(
    [pf.Position("NVDA", "long", 3000.0)], usdt=5000.0, btc=0.1)      # 仓位大幅缩小
a5.session_state["pf_ver"] = a5.session_state["pf_ver"] + 1
a5.run()                                                              # 不点任何按钮
kept = a5.session_state["pf_report"].scorecard["overall"] == before
if not kept:
    problems.append("改持仓后自动重跑了 —— 用户还没打完就会被冲掉")
page = " ".join(m.value for m in a5.markdown)
has_note = "表格改过了" in page
has_btn = any(b.label == "用改过的持仓重新运行" for b in a5.button)
if not (has_note and has_btn):
    problems.append("结果过期时没有提示或没有就近的按钮 (提示=%s 按钮=%s)" % (has_note, has_btn))
check(a5, "改持仓: 不自动重跑", "分数仍是 %.0f · 过期提示=%s · 就近按钮=%s" % (before, has_note, has_btn))

run_btn(a5, "用改过的持仓重新运行")
after = a5.session_state["pf_report"].scorecard["overall"]
if after == before:
    problems.append("点了「用改过的持仓重新运行」分数没变")
check(a5, "点就近按钮才重算", "总分 %.0f -> %.0f" % (before, after))
page2 = " ".join(m.value for m in a5.markdown)
if "表格改过了" in page2:
    problems.append("重算完了过期提示还在")

print("%s" % ("全部通过" if not problems else "发现 %d 个问题:\n  - %s" % (len(problems), "\n  - ".join(problems))))
sys.exit(1 if problems else 0)
