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
    scores = re.findall(r"font-size:40px;font-weight:700;line-height:1.15;color:[^\"]+;\">(\d+)", " ".join(md_all()))
    print("[%s] %.1fs  异常: %s" % (tag, time.time() - t, exc or "无"))
    print("  安全总分:", scores or "无", "| 地震图:", any("<polyline" in v for v in md_all()),
          "| 顶栏:", any("rToken Stress Desk" in v for v in md_all()))
    print("  解析/方式:", [c.value[:80] for c in at.caption if c.value.startswith(("解析", "结论生成方式"))][:3])
    print("  错误/警告:", [e.value[:80] for e in at.error] + [w.value[:80] for w in at.warning] or "无")


def click(label):
    [b for b in at.button if b.label == label][0].click()


report("首次加载", t0)

t = time.time()
click("运行压力测试")
at.run()
report("组合: 默认示例", t)

# 历史相似情景: 这张卡是「决策压力测试」子题的字面问题 (retrieve historically similar scenarios),
# 没有测试守着的话, 接口一改就会静默消失。
try:                       # AppTest 的 session_state 没有 .get()
    _ana = at.session_state["pf_analog"] or {}
except Exception:
    _ana = {}
_page0 = " ".join(md_all() + [c.value for c in at.caption])   # 免责声明走 st.caption, 不在 markdown 里
_checks = [
    ("相似情景卡片出现", "历史上最像当前状态的日子" in _page0),
    # 第四轮复核: 查询日可能比今天早一到两天, 页面上不能再把它叫「今天」
    ("卡片标题不再写「最像今天」", "最像今天" not in _page0),
    ("理由里带查询日而不是「今天」",
     all(("今天" not in r) and ((_ana.get("query_date") or "") in r)
         for a in _ana.get("analogs", []) for r in a.get("reasons", [])) and bool(_ana.get("analogs"))),
    ("状态条显示真实完整交易日, 不是日历边界",
     (_ana.get("as_of") or {}).get("us_last_complete_bar", "") in _page0),
    ("邻域结论不写成确定性判断", "并不比随便挑一天更危险" not in _page0 and "随便挑一天" not in _page0),
    ("邻域写明是描述性对照", "描述性对照" in _page0),
    ("算出了相似日", len(_ana.get("analogs", [])) > 0),
    ("每个相似日都带后续 1/3/5 日",
     all(len(a["outcomes"]) == 3 for a in _ana.get("analogs", [])) and bool(_ana.get("analogs"))),
    ("每个相似日给两条最像", all(len(a["reasons"]) == 2 for a in _ana.get("analogs", [])) and bool(_ana.get("analogs"))),
    ("每个相似日还给一条最不像", all(bool(a.get("unlike")) for a in _ana.get("analogs", [])) and bool(_ana.get("analogs"))),
    ("核心版每个候选用相同特征数", len({a["n_features"] for a in _ana.get("analogs", [])}) == 1),
    ("核心版不含 DVOL", "BTC DVOL" not in _ana.get("features_used", [])),
    ("DVOL 版单独成榜", isinstance(_ana.get("dvol_tier"), dict)),
    ("给了邻域统计与全历史基准", "base_neg_share" in (_ana.get("neighborhood") or {})),
    ("压力反例与最相似分开且标明挑选方式",
     "按事后" in ((_ana.get("neighborhood") or {}).get("note") or "")),
    ("有效特征数 > 0", all(a["n_features"] > 0 for a in _ana.get("analogs", [])) and bool(_ana.get("analogs"))),
    ("相似日按距离升序", [a["distance"] for a in _ana.get("analogs", [])]
     == sorted(a["distance"] for a in _ana.get("analogs", []))),
    ("排除了最近的日子 (最像的不是昨天)",
     all(a["date"] < (_ana.get("query_date") or "9999") for a in _ana.get("analogs", []))),
    ("写明不是概率预测", "不构成概率预测" in _page0),
    ("写明距离只是相对排序", "相对排序" in _page0),
]
for _name, _ok in _checks:
    problems += not _ok
    print("  [相似情景] %s: %s" % (_name, "OK" if _ok else "FAIL"))
if _ana.get("analogs"):
    print("  [相似情景] 最像的是 %s (距离 %.3f, %d 项特征) | 特征: %s"
          % (_ana["analogs"][0]["date"], _ana["analogs"][0]["distance"],
             _ana["analogs"][0]["n_features"], ", ".join(_ana.get("features_used", []))))

# 点示例 chip: 它要把句子写进输入框, 而输入框的 key 已经被 text_input 占用 ->
# 只能走 on_click 回调, 否则 StreamlitAPIException (09-17 云端实测炸过)
t = time.time()
[b for b in at.button if b.key == "pf_ex_1"][0].click()
at.run()
report("组合: 点示例 chip", t)
_q = at.text_input(key="pf_query").value
problems += not _q.startswith("保证金")
print("  输入框已填上示例:", "OK" if _q.startswith("保证金") else "FAIL (%s)" % _q[:30])
t = time.time()
[b for b in at.button if b.key == "pf_fu_0"][0].click()
at.run()
report("组合: 点追问 chip", t)
try:
    urls = sorted({b.proto.url for b in at.get("link_button")})
except Exception as e:  # AppTest 对 link_button 的支持随版本变化, 取不到不算失败
    urls = "取不到 (%s)" % type(e).__name__
print("  方案卡:", len([v for v in md_all() if "border-left:2px dashed" in v]), "| 去 Bitget 链接:", urls)

for q in ["把 MSTR 砍半呢", "把 COIN 换成 AAPL 呢", "一半 NVDA 换成 SPY 空单", "ETH 全部换成 USDT 呢", "英伟达最新新闻是什么",
          "BTC 全部换成 USDT 呢"]:
    t = time.time()
    at.text_input(key="pf_query").input(q)
    click("运行压力测试")
    at.run()
    report("组合追问: " + q, t)

t = time.time()
[b for b in at.button if b.label == "套用"][0].click()
at.run()
report("组合: 套用第一个方案", t)
t = time.time()
click("运行压力测试")
at.run()
report("组合: 套用后重新运行", t)

t = time.time()
at.text_input(key="pf_query").input("保证金 2万U, 多英伟达 1.5万, 多特斯拉 1万, 空纳指 1万")
click("运行压力测试")
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
click("运行压力测试")
at.run()
report("组合: 全现货零保证金", t)
page = " ".join(md_all())
import re as _re
# 侧栏那句产品标语是**全局定位**, 不是对这个账户的判断 —— 它在每一页都出现, 与账户类型无关。
# 豁免它, 但下面立刻断言它确实在页面上: 否则这条豁免会在标语改动后悄悄变成「什么都不检查」。
TAGLINE = "共享同一条强平线"
_liq_hits = [m for m in _re.findall(r"[^<>]{0,14}强平[^<>]{0,14}", page)
             if "不会被强平" not in m and "本金亏光" not in m and TAGLINE not in m]
for name, ok in [("写明不会被强平", "不会被强平" in page),
                 ("被豁免的产品标语确实还在 (防止豁免变成放水)", TAGLINE in page),
                 ("除全局标语外, 整页不出现任何「强平」字样", not _liq_hits)]:
    problems += not ok
    print("  %s: %s%s" % (name, "OK" if ok else "FAIL",
                          "" if ok else " <- " + str(_liq_hits[:3])))

for q in ["周五收盘前我想 3 倍做多 NVDA rToken 过周末", "如果降到 2 倍呢", "我想买点狗狗币"]:
    t = time.time()
    at.text_input(key="query").input(q)
    click("单笔压力测试")
    at.run()
    report("单笔: " + q, t)

print("\n%s" % ("无异常" if not problems else "%d 个步骤有异常" % problems))
sys.exit(1 if problems else 0)
