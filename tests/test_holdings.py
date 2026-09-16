"""持仓表格解析离线测试 (不联网)。  python -X utf8 tests/test_holdings.py

st.data_editor 是 canvas 组件, AppTest 和浏览器自动化都点不进去,
所以「表里填的买入价 / 数量到底有没有变成正确的仓位」只能在这一层钉住。
期望值在注释里手算。
"""
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from desk import holdings                                              # noqa: E402
from desk import portfolio as pf                                       # noqa: E402

fails = 0


def check(name, got, want, tol=1e-3):
    global fails
    ok = (got is None and want is None) or (got is not None and want is not None and
                                            (got == want if isinstance(want, (bool, str)) else math.isclose(got, want, abs_tol=tol)))
    fails += not ok
    print("%s  %-46s got=%s want=%s" % ("PASS" if ok else "FAIL", name, got, want))


SUP = {"NVDA", "MSTR", "COIN", "SPY", "BTC"}
PX = {"NVDA": 200.0, "MSTR": 100.0, "SPY": 500.0, "BTC": 60000.0}


def row(**kw):
    r = {"标的": "NVDA", "类型": "杠杆", "方向": "做多", "买入价": "", "数量": 70.0, "名义 USDT": 15000.0}
    r.update(kw)
    return [r]


def one(auto=True, **kw):
    return holdings.rows_to_positions(row(**kw), PX, auto, SUP)[0]

# ---- 1. 名义价值: 自动 vs 手填 ----
check("自动: 数量 x 当前价", one().notional, 14000)          # 70 x 200, 表里的 15000 被忽略
check("自动: 记下当前价", one().price, 200)
check("关掉自动: 用表里的名义", one(auto=False).notional, 15000)
check("自动但没数量: 退回名义", holdings.rows_to_positions(row(数量=None), PX, True, SUP)[0].notional, 15000)

# ---- 2. 买入价 -> 浮盈浮亏 ----
# 多头 14000 名义, 150 -> 200: 14000 x (1 - 150/200) = 3500
check("买入价进了仓位", one(买入价="150").entry, 150)
check("多头浮盈", one(买入价="150").pnl, 3500)
check("空头 250 -> 200 是赚", one(方向="做空", 买入价="250").pnl, 14000 * (1 - 250 / 200) * -1)
check("账户层浮盈", pf.unrealized_pnl(pf.Account(holdings.rows_to_positions(row(买入价="150"), PX, True, SUP), 1000)), 3500)
for v in ("", "   ", None, "None", "nan", "abc", "0"):
    check("空/脏买入价当没填 (%r)" % v, one(买入价=v).entry is None, True)

# ---- 3. 类型 / 方向 / 跳过的行 ----
got = holdings.rows_to_positions(
    [{"标的": "btc", "类型": "现货", "方向": "做多", "买入价": "50000", "数量": 0.1, "名义 USDT": None},
     {"标的": "", "类型": "杠杆", "方向": "做多", "买入价": "", "数量": 1, "名义 USDT": 1},            # 空行 (表尾新增行)
     {"标的": "TSLA", "类型": "杠杆", "方向": "做多", "买入价": "", "数量": 1, "名义 USDT": 1},        # 不支持的标的
     {"标的": "SPY", "类型": "杠杆", "方向": "做空", "买入价": "", "数量": None, "名义 USDT": None}],   # 没数量也没名义
    PX, True, SUP)
check("只留下合法的一行", len(got), 1)
check("小写标的照样认", got[0].symbol, "BTC")
check("现货类型", got[0].kind, "spot")
check("现货名义 = 0.1 x 60000", got[0].notional, 6000)

# ---- 4. 往返: 仓位 -> 表格 -> 仓位 ----
pos = [pf.Position("NVDA", "long", 14000.0, "leverage", 150.0, 200.0, 70.0),
       pf.Position("BTC", "long", 6000.0, "spot", None, 60000.0, 0.1)]
r = holdings.position_rows(pos, PX)
check("有买入价就显示两位小数", r[0]["买入价"], "150.00")
check("没买入价显示空串 (不是 None)", r[1]["买入价"], "")
check("类型回中文", r[1]["类型"], "现货")
check("方向回中文", r[0]["方向"], "做多")
back = holdings.rows_to_positions(r, PX, True, SUP)
check("往返后标的不变", "%s,%s" % (back[0].symbol, back[1].symbol), "NVDA,BTC")
check("往返后买入价不变", back[0].entry, 150)
check("往返后名义不变", back[0].notional, 14000)
check("没数量时按当前价补出来", holdings.position_rows([pf.Position("SPY", "short", 5000.0)], PX)[0]["数量"], 10)


# ---- 手动加的行: 取不到当前价时不能静默丢掉 (09-17 Ivan 实测) ----
manual = [{"标的": "NVDA", "类型": "杠杆", "方向": "做多", "买入价": "", "数量": 70.0, "名义 USDT": 15000.0},
          {"标的": "ORCL", "类型": "杠杆", "方向": "做空", "买入价": "143.22", "数量": 50.0, "名义 USDT": None},
          {"标的": "", "类型": None, "方向": None, "买入价": None, "数量": None, "名义 USDT": None},
          {"标的": "ZZZZ", "类型": "杠杆", "方向": "做多", "买入价": "", "数量": 1.0, "名义 USDT": 100.0}]

pos, skipped = holdings.parse_rows(manual, dict(PX, ORCL=143.0), True, SUP | {"ORCL"})
check("有价格时新行能算出来", ",".join(p.symbol for p in pos), "NVDA,ORCL")
check("新行的名义 = 数量 x 现价", next(p.notional for p in pos if p.symbol == "ORCL"), 7150)
check("新行的买入价也留住了", next(p.entry for p in pos if p.symbol == "ORCL"), 143.22)
check("空行不报警告, 只报不支持的那行", ";".join(skipped), "ZZZZ 不在支持清单里")

pos2, skipped2 = holdings.parse_rows(manual, PX, True, SUP | {"ORCL"})      # 没有 ORCL 的价
check("取不到价时不算进仓位", ",".join(p.symbol for p in pos2), "NVDA")
check("取不到价要报出来", any("ORCL" in s and "当前价" in s for s in skipped2), True)
check("不支持的标的也要报出来", any("ZZZZ" in s for s in skipped2), True)

print("\n%s" % ("全部通过" if not fails else "%d 项失败" % fails))
sys.exit(1 if fails else 0)
