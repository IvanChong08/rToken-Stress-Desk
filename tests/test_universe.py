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


# ---- 6. 英文自然语言走规则 (不经过大模型) ----
# 评分维度里有「LUI fluency」, 英文句子若退回大模型要 9–40 秒、还吃额度, 所以规则层必须认
from desk.idea import parse_rules as parse_trade                       # noqa: E402

base_acc = pf.Account([pf.Position("NVDA", "long", 15000), pf.Position("MSTR", "long", 8000),
                       pf.Position("COIN", "long", 5000)], usdt=5000, btc=0.1, eth=0.5)

acc_en = pidea.parse_rules("long NVDA 15000, short SPY 5000, 0.1 BTC as margin")
check("英文建仓: 标的方向", None if acc_en is None else [(p.symbol, p.side) for p in acc_en.positions],
      [("NVDA", "long"), ("SPY", "short")])
check("英文建仓: BTC 当保证金", None if acc_en is None else acc_en.btc, 0.1)

for text, want in [("halve MSTR", "4000"), ("close COIN", "COIN"), ("remove MSTR", "MSTR"),
                   ("swap all BTC to USDT", "8000"), ("convert ETH to cash", "1250"),
                   ("add 3000 USDT margin", "8000"), ("reduce 2000 margin", "3000"),
                   ("set NVDA to 20000", "20000"), ("swap COIN into AAPL", "AAPL")]:
    a, how = pidea.edit(text, base_acc, 80000.0, 2500.0)
    check("英文追问: %s" % text, a is not None and want in how, True)
check("英文闲聊不当修改", pidea.edit("what is the news on nvidia", base_acc, 80000.0, 2500.0)[0], None)

for text, want in [("3x long NVDA over the weekend", ("NVDA", "long", 3.0, "weekend")),
                   ("short TSLA 5x overnight before earnings", ("TSLA", "short", 5.0, "overnight")),
                   ("buy NVDA 3 times leverage weekend", ("NVDA", "long", 3.0, "weekend"))]:
    i = parse_trade(text)
    check("英文单笔: %s" % text[:28], None if i is None else (i.symbol, i.side, i.leverage, i.scenario), want)

# ---- 7. 零碎份额 (代币化美股的意义就在这) ----
from desk import holdings                                              # noqa: E402

frac = holdings.rows_to_positions(
    [{"标的": "NVDA", "类型": "现货", "方向": "做多", "买入价": "", "数量": 0.005, "名义 USDT": None}],
    {"NVDA": 200.0}, True, universe.SUPPORTED)
check("0.005 股能建仓", len(frac), 1)
check("0.005 股的名义价值", frac[0].notional, 1.0)

print("\n%s" % ("全部通过" if not fails else "%d 项失败" % fails))
sys.exit(1 if fails else 0)
