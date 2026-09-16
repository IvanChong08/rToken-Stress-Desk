"""标的清单的离线测试 (不联网)。  python -X utf8 tests/test_universe.py

钉住扩容后最容易出事的三件事:
  1. 三个集合的包含关系 (CACHED ⊆ LEVERAGE? 不一定; CACHED ⊆ SUPPORTED 必须成立)
  2. 提示语和报错不能把上千个代码全铺出来 (会把 prompt 撑爆, 也没人看得完)
  3. 上千个代码里有大量两三个字母的票 (ALL / NOW / OPEN / APP...), 小写英文词不能被当成代码
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from desk import idea, portfolio_idea as pidea, universe   # noqa: E402
from desk import portfolio as pf                           # noqa: E402

fails = 0


def check(name, got, want):
    global fails
    ok = got == want
    fails += not ok
    print("%s  %-46s got=%s want=%s" % ("PASS" if ok else "FAIL", name, got, want))


# ---- 1. 集合关系 ----
check("组合支持的至少有 11 只", len(universe.SUPPORTED) >= 11, True)
check("能加杠杆的是子集", universe.LEVERAGE <= universe.SUPPORTED, True)
check("单笔缓存的是子集", universe.CACHED <= universe.SUPPORTED, True)
check("单笔集合非空", len(universe.CACHED) > 0, True)
check("NVDA 三个集合都在", all("NVDA" in s for s in (universe.SUPPORTED, universe.LEVERAGE, universe.CACHED)), True)
check("BTC 不是可开仓标的", "BTC" in universe.SUPPORTED, False)

# ---- 2. 提示语 / 报错不铺全量清单 ----
for name, txt in [("单笔提示语", idea.SYSTEM_PROMPT), ("组合提示语", pidea.SYSTEM_PROMPT),
                  ("追问提示语", pidea.EDIT_PROMPT if hasattr(pidea, "EDIT_PROMPT") else pidea.SYSTEM_PROMPT)]:
    check("%s 够短 (<1500 字)" % name, len(txt) < 1500, True)
check("不支持标的的报错不铺清单", len(pf.validate_account(
    pf.Account([pf.Position("ZZZZ", "long", 1000)], usdt=1000)) or "") < 200, True)

# ---- 3. 小写英文词不能被当成股票代码 ----
# 这些词在扩容后都是真实的美股代码: ALL(Allstate) NOW(ServiceNow) OPEN(Opendoor) APP(AppLovin) KEY(KeyCorp)
for sentence in ["all of them", "what should i do now", "open the app", "the key point",
                 "把仓位 all 平掉吗"]:
    got = [s for _, s in pidea._symbols_in(sentence)]
    check("小写英文词不当代码: %r" % sentence[:22], got, [])
# 大写才认 (用户写代码本来就是大写)
check("大写 ALL 认得出", [s for _, s in pidea._symbols_in("做多 ALL 5000U")] == ["ALL"] if "ALL" in universe.SUPPORTED else True, True)
check("小写 nvda 照旧认得出", [s for _, s in pidea._symbols_in("做多 nvda 5000U")], ["NVDA"])

# ---- 4. 杠杆可用性 ----
lev_only_spot = sorted(universe.SUPPORTED - universe.LEVERAGE)
if lev_only_spot:
    sym = lev_only_spot[0]
    err = pf.validate_account(pf.Account([pf.Position(sym, "long", 1000)], usdt=1000))
    check("没有永续的标的不能开杠杆 (%s)" % sym, bool(err) and "现货" in err, True)
    ok = pf.validate_account(pf.Account([pf.Position(sym, "long", 1000, kind="spot")], usdt=1000))
    check("同一只可以当现货持有 (%s)" % sym, ok, None)
else:
    print("SKIP  清单里每只都有永续合约, 跳过杠杆限制检查")

# ---- 5. 单笔模式只认有缓存的 ----
not_cached = sorted(universe.SUPPORTED - universe.CACHED)
if not_cached:
    err = idea.validate({"symbol": not_cached[0], "side": "long", "leverage": 2.0, "scenario": "weekend"})
    check("单笔拒绝没缓存的标的", bool(err) and "组合" in err, True)
check("单笔接受 NVDA", idea.validate({"symbol": "NVDA", "side": "long", "leverage": 2.0, "scenario": "weekend"}), None)

print("\n%s" % ("全部通过" if not fails else "%d 项失败" % fails))
sys.exit(1 if fails else 0)
