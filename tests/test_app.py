"""Streamlit AppTest: 真正执行 app.py (无浏览器, 看不到样式, 只验证流程与内容)。  python -X utf8 tests/test_app.py"""
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
from streamlit.testing.v1 import AppTest  # noqa: E402
import streamlit  # noqa: E402

print("streamlit", streamlit.__version__)
t0 = time.time()
at = AppTest.from_file("app.py", default_timeout=600)
at.run()
problems = 0


def md_all():
    return [m.value for m in at.markdown]


def report(tag, t):
    global problems
    exc = [e.value for e in at.exception]
    problems += bool(exc)
    scores = [re.search(r">(\d\d)</div>", v).group(1) for v in md_all() if "font-size:96px" in v and re.search(r">(\d\d)</div>", v)]
    print("[%s] %.1fs  异常: %s" % (tag, time.time() - t, exc or "无"))
    print("  安全总分:", scores or "无", "| 地震图:", any("<polyline" in v for v in md_all()),
          "| 顶栏:", any("RTOKEN STRESS DESK" in v for v in md_all()))
    print("  解析/方式:", [c.value[:80] for c in at.caption if c.value.startswith(("解析", "结论生成方式"))][:3])
    print("  错误/警告:", [e.value[:80] for e in at.error] + [w.value[:80] for w in at.warning] or "无")


def click(label):
    [b for b in at.button if b.label == label][0].click()


report("首次加载", t0)

t = time.time()
click("运行组合压力测试")
at.run()
report("组合: 默认示例", t)
try:
    urls = sorted({b.proto.url for b in at.get("link_button")})
except Exception as e:  # AppTest 对 link_button 的支持随版本变化, 取不到不算失败
    urls = "取不到 (%s)" % type(e).__name__
print("  方案卡:", len([v for v in md_all() if "border-left:2px dashed" in v]), "| 去 Bitget 链接:", urls)

for q in ["把 MSTR 砍半呢", "把 COIN 换成 AAPL 呢", "一半 NVDA 换成 SPY 空单", "ETH 全部换成 USDT 呢", "英伟达最新新闻是什么",
          "BTC 全部换成 USDT 呢"]:
    t = time.time()
    at.text_input(key="pf_query").input(q)
    click("解析 / 修改")
    at.run()
    report("组合追问: " + q, t)

t = time.time()
[b for b in at.button if b.label == "套用"][0].click()
at.run()
report("组合: 套用第一个方案", t)
t = time.time()
click("运行组合压力测试")
at.run()
report("组合: 套用后重新运行", t)

t = time.time()
at.text_input(key="pf_query").input("保证金 2万U, 多英伟达 1.5万, 多特斯拉 1万, 空纳指 1万")
click("解析 / 修改")
at.run()
report("组合: 新组合", t)

# 全现货、零保证金 (表格里可以直接填出来): 曾经在 multi_day_worst 里崩掉, 且不能说「强平距离」
t = time.time()
from desk import portfolio as pf                                     # noqa: E402
at.session_state["pf_account"] = pf.Account(
    [pf.Position("NVDA", "long", 3795.0, kind="spot", qty=17.0, price=223.2),
     pf.Position("AAPL", "long", 3795.0, kind="spot", qty=11.0, price=345.0)],
    usdt=0.0, btc=0.0, maint_margin=0.01)
at.session_state["pf_ver"] = 99
at.session_state["pf_report"] = None
at.run()
click("运行组合压力测试")
at.run()
report("组合: 全现货零保证金", t)
page = " ".join(md_all())
for name, ok in [("写明不会被强平", "不会被强平" in page), ("不提强平距离", "强平距离" not in page)]:
    problems += not ok
    print("  %s: %s" % (name, "OK" if ok else "FAIL"))

for q in ["周五收盘前我想 3 倍做多 NVDA rToken 过周末", "如果降到 2 倍呢", "我想买点狗狗币"]:
    t = time.time()
    at.text_input(key="query").input(q)
    click("单笔压力测试")
    at.run()
    report("单笔: " + q, t)

print("\n%s" % ("无异常" if not problems else "%d 个步骤有异常" % problems))
sys.exit(1 if problems else 0)
