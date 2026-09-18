"""组合引擎离线测试: 合成行情 + 手算期望值 (不联网)。  python -X utf8 tests/test_portfolio_engine.py

期望值全部在本文件注释里手算, 不调用被测函数推导。
"""
import math
import os
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from desk import portfolio as pf  # noqa: E402

fails = 0


def check(name, got, want, tol=1e-3):
    global fails
    ok = (got is None and want is None) or (got is not None and want is not None and
                                            (got == want if isinstance(want, (bool, str)) else math.isclose(got, want, abs_tol=tol)))
    fails += not ok
    print("%s  %-48s got=%s want=%s" % ("PASS" if ok else "FAIL", name, got, want))


A = pf.Account([pf.Position("NVDA", "long", 3000), pf.Position("MSTR", "long", 2000), pf.Position("TSLA", "short", 1000)],
               usdt=1000, btc=0.01, maint_margin=0.01, btc_haircut=0.9)

# ---- 1. 纯情景 (BTC 价格 100000) ----
# E0 = 1000 + 0.01*100000*0.9 = 1900 ; M0 = 0.01*6000 = 60
# 变动 NVDA -10% MSTR -20% TSLA +5% BTC -20%:
#   仓位盈亏 = -300 -400 -50 = -750 ; 抵押品 = 900*-0.2 = -180 ; E = 970
#   维持 = 0.01*(2700+1600+1050) = 53.5 ; 亏损 930/1900 = 0.489474 ; 缓冲用掉 930/1840 = 0.505435
mv = {"NVDA": -0.10, "MSTR": -0.20, "TSLA": 0.05, "BTC": -0.20}
r = pf.shock(A, 100000, mv)
check("shock equity", r.equity, 970)
check("shock maint", r.maint, 53.5)
check("shock liquidated", r.liquidated, False)
check("shock loss_pct", r.loss_pct, 0.489474, 1e-5)
check("shock buffer_used", r.buffer_used, 0.505435, 1e-5)
check("shock collateral_pnl", r.collateral_pnl, -180)
# 放大 k 倍: E-M = 1900-930k - 0.01*(6000 - 650k) = 1840 - 923.5k -> k = 1.992420
check("scale_to_liquidation", pf.scale_to_liquidation(A, 100000, mv), 1.992420, 1e-4)
# 同步不利 x, BTC 跟跌: 1840 - 6860x -> 0.268222 ; BTC 不动: 1840 - 5960x -> 0.308725
check("uniform x (BTC follows)", pf.uniform_move_to_liquidation(A, 100000, True), 0.268222, 1e-4)
check("uniform x (BTC flat)", pf.uniform_move_to_liquidation(A, 100000, False), 0.308725, 1e-4)
# 已经强平的情景 k < 1: 只有 USDT 100 的账户, 名义 6000, 上面情景亏 750 -> 必然 k<1
B = pf.Account(A.positions, usdt=100, btc=0, maint_margin=0.01)
# E-M = 100 - 750k - 60 + 6.5k = 40 - 743.5k -> k = 0.053800
check("scale k<1 (already liquidated)", pf.scale_to_liquidation(B, 100000, mv), 0.053800, 1e-4)
# 纯多头 1 倍无杠杆且只有 USDT: 跌到 0 也不强平? E-M = 3000 - 3000x - 0.01*3000(1-x) = 2970(1-x) > 0 for x<1
C = pf.Account([pf.Position("NVDA", "long", 3000)], usdt=3000)
check("no liquidation at 1x", pf.uniform_move_to_liquidation(C, 100000, False), 1.0, 1e-4)


# ---- 2. 合成行情面板 ----
def stock(rows):  # rows: (NY 日期, open, high, low, close)
    return pd.DataFrame({"ts": [pd.Timestamp(d + " 14:30", tz="UTC") for d, *_ in rows],
                         "open": [x[1] for x in rows], "high": [x[2] for x in rows],
                         "low": [x[3] for x in rows], "close": [x[4] for x in rows], "volume": 0})


stocks = {
    "NVDA": stock([("2026-01-02", 100, 100, 100, 100), ("2026-01-05", 95, 101, 90, 92), ("2026-01-06", 93, 100, 91, 99)]),
    "MSTR": stock([("2026-01-02", 200, 200, 200, 200), ("2026-01-05", 190, 205, 160, 170), ("2026-01-06", 170, 175, 150, 150)]),
    "TSLA": stock([("2026-01-02", 50, 50, 50, 50), ("2026-01-05", 50, 52.5, 49, 51), ("2026-01-06", 51, 51, 49, 50)]),
}
btc = pd.DataFrame({"ts": [pd.Timestamp(d, tz="UTC") for d in ["2026-01-02", "2026-01-03", "2026-01-04", "2026-01-05", "2026-01-06"]],
                    "open": 0, "high": [100000, 101000, 99000, 90000, 92000], "low": [100000, 97000, 80000, 84000, 85000],
                    "close": [100000, 98000, 85000, 88000, 90000], "volume": 0})
P = pf.build_panel(stocks, btc)
check("panel rows", len(P.close_ret), 2)
check("panel level rows (含首日)", len(P.close_level), 3)
check("btc_price = 最新收盘", P.btc_price, 90000)
d1 = P.close_ret.index[0]
check("NVDA low_ret 01-05", P.low_ret.loc[d1, "NVDA"], -0.10, 1e-9)
check("TSLA high_ret 01-05", P.high_ret.loc[d1, "TSLA"], 0.05, 1e-9)
# BTC 周五收盘 100000 -> 周六/日/一 最低 80000 = -20%, 最高 101000 = +1%, 周一收盘 88000 = -12%
check("BTC low_ret 覆盖周末", P.low_ret.loc[d1, "BTC"], -0.20, 1e-9)
check("BTC high_ret 覆盖周末", P.high_ret.loc[d1, "BTC"], 0.01, 1e-9)
check("BTC close_ret", P.close_ret.loc[d1, "BTC"], -0.12, 1e-9)

# 面板上 BTC 价格 90000 -> E0 = 1000 + 810 = 1810
# extreme 01-05: 仓位 -750 (与第 1 部分相同变动), 抵押品 810*-0.2 = -162 -> E = 898
rep = pf.replay(A, P, "extreme")
check("replay worst date", rep.loc[0, "date"], "2026-01-05")
check("replay extreme equity", rep.loc[0, "equity"], 898)
check("replay worst_position", rep.loc[0, "worst_position"], "MSTR long")
check("replay == shock(day_moves)", pf.shock(A, P.btc_price, pf.day_moves(A, P, d1)).equity, 898)
# extreme 01-06: NVDA 91/92-1, MSTR 150/170-1, TSLA 空头看最高 51/51-1=0, BTC 85000/88000-1
#   = 3000*-0.0108696 + 2000*-0.117647 + 810*-0.0340909 = -32.6087 - 235.2941 - 27.6136 -> E = 1514.4836
check("replay extreme 01-06", rep.loc[1, "equity"], 1514.4836)
# close 01-05: -240 -300 -20 = -560; 810*-0.12 = -97.2 -> 1152.8
repc = pf.replay(A, P, "close")
check("replay close equity", float(repc[repc.date == "2026-01-05"]["equity"].iloc[0]), 1152.8)

# 多日 (收盘口径): 从 01-02 起, 第 1 天 E=1152.8, 第 2 天 NVDA -1% MSTR -25% TSLA 0 BTC -10% -> -30-500-81 -> 1199
md = pf.multi_day_worst(A, P, horizon=2)
row = md[md.start == "2026-01-02"].iloc[0]
check("multi_day worst equity from 01-02", row["equity"], 1152.8)
check("multi_day worst days", int(row["days"]), 1)
# 从 01-05 起只有 1 天: NVDA 99/92-1 -> +228.2609, MSTR -235.2941, TSLA 空 50/51-1 -> +19.6078, BTC 810*(90/88-1) +18.4091
row = md[md.start == "2026-01-05"].iloc[0]
check("multi_day from 01-05", row["equity"], 1840.9837)

# BTC 缺行 (Yahoo 2026-09-14 实测缺): 去掉 01-05 那行, 01-05 的股票日不能被丢
#   参考 = 01-02 收盘 100000; 窗口 (01-02, 01-05] 剩 01-03/01-04: 最低 80000 -> -20%, 收盘取最后一行 85000 -> -15%
#   01-06: 参考 = ≤01-05 最近一行 (01-04) 85000; 窗口只有 01-06: 最低 85000 -> 0, 收盘 90000 -> +5.8824%
P2 = pf.build_panel(stocks, btc[btc.ts != pd.Timestamp("2026-01-05", tz="UTC")].reset_index(drop=True))
check("缺行: 股票日不丢", len(P2.close_ret), 2)
check("缺行: BTC low_ret 01-05", P2.low_ret.iloc[0]["BTC"], -0.20, 1e-9)
check("缺行: BTC close_ret 01-05", P2.close_ret.iloc[0]["BTC"], -0.15, 1e-9)
check("缺行: BTC close_ret 01-06", P2.close_ret.iloc[1]["BTC"], 0.058824, 1e-6)
check("缺行: level 01-05 用 01-04 收盘", P2.close_level.iloc[1]["BTC"], 85000)

# ---- ETH 抵押品 (2026-09-16 加入) ----
# ETH 日线: 01-02 收 3000; 01-03 低 2800; 01-04 低 2250; 01-05 低 2600 收 2700; 01-06 低 2650 收 2850
eth = pd.DataFrame({"ts": [pd.Timestamp(d, tz="UTC") for d in ["2026-01-02", "2026-01-03", "2026-01-04", "2026-01-05", "2026-01-06"]],
                    "open": 0, "high": [3000, 3050, 2950, 2750, 2900], "low": [3000, 2800, 2250, 2600, 2650],
                    "close": [3000, 2900, 2400, 2700, 2850], "volume": 0})
PE = pf.build_panel(stocks, btc, eth=eth)
AE = pf.Account(A.positions, usdt=1000, btc=0.01, maint_margin=0.01, btc_haircut=0.9, eth=1, eth_haircut=0.8)
check("ETH 现价 = 最新收盘", PE.eth_price, 2850)
check("ETH low_ret 覆盖周末", PE.low_ret.iloc[0]["ETH"], -0.25, 1e-9)
# 纯情景 (BTC 100000, ETH 3000): E0 = 1000 + 900 + 1x3000x0.8 = 4300; 仓位 -750, BTC -180, ETH 2400x-0.2 = -480 -> 2890
r = pf.shock(AE, 100000, {**mv, "ETH": -0.20}, eth_price=3000)
check("ETH shock equity", r.equity, 2890)
check("ETH shock collateral_pnl", r.collateral_pnl, -660)
# 同步不利 x, 加密跟跌: E-M = 4300 - 6000x - 900x - 2400x - (60 - 40x) = 4240 - 9260x -> 0.457883; 不动: 4240 - 5960x -> 0.711409
check("ETH uniform (加密跟跌)", pf.uniform_move_to_liquidation(AE, 100000, True, 3000), 0.457883, 1e-4)
check("ETH uniform (加密不动)", pf.uniform_move_to_liquidation(AE, 100000, False, 3000), 0.711409, 1e-4)
# 面板 (BTC 90000, ETH 2850): E0 = 1000 + 810 + 2280 = 4090
# extreme 01-05: 仓位 -750, BTC 810x-0.2 = -162, ETH 2280x-0.25 = -570 -> 2608
repe = pf.replay(AE, PE, "extreme")
check("ETH replay 最差日", repe.loc[0, "date"], "2026-01-05")
check("ETH replay extreme equity", repe.loc[0, "equity"], 2608)
# close 01-05: -560, BTC -97.2, ETH 2280x-0.1 = -228 -> 3204.8
check("ETH replay close equity", float(pf.replay(AE, PE, "close").query("date == '2026-01-05'")["equity"].iloc[0]), 3204.8)
# 多日从 01-02: 第 1 天 3204.8; 第 2 天 -530, BTC -81, ETH 2280x-0.05 = -114 -> 3365 -> 取 3204.8
mde = pf.multi_day_worst(AE, PE, horizon=2)
check("ETH multi_day from 01-02", mde[mde.start == "2026-01-02"].iloc[0]["equity"], 3204.8)
check("只有 ETH 也算有抵押品", pf.validate_account(pf.Account(A.positions, eth=1)), None)
try:
    pf.replay(AE, P, "extreme")      # 面板没有 ETH 数据
    check("缺 ETH 数据要报错", False, True)
except ValueError:
    check("缺 ETH 数据要报错", True, True)
check("没有 ETH 的组合结果不变 (旧面板)", pf.replay(A, P, "extreme").loc[0, "equity"], 898)
check("没有 ETH 的组合结果不变 (新面板)", pf.replay(A, PE, "extreme").loc[0, "equity"], 898)
check("account_from_dict 往返", pf.account_from_dict(AE.to_dict()) == AE, True)

# ---- 现货 + 买入价 (2026-09-16 加入) ----
# NVDA 杠杆多 3000, 买价 90 现价 100 -> 浮盈 3000x(1-90/100) = +300
# MSTR 现货多 2000 (市值计入权益, 不占维持保证金)
# TSLA 杠杆空 1000, 买价 60 现价 50 -> 浮盈 -1x1000x(1-60/50) = +200
AS = pf.Account([pf.Position("NVDA", "long", 3000, entry=90, price=100),
                 pf.Position("MSTR", "long", 2000, kind="spot", entry=250, price=200),
                 pf.Position("TSLA", "short", 1000, entry=60, price=50)],
                usdt=1000, btc=0.01, maint_margin=0.01, btc_haircut=0.9)
check("现货市值", pf.spot_value(AS), 2000)
check("未实现盈亏 (不含现货)", pf.unrealized_pnl(AS), 500)
# 权益 = 1000 + 810 (BTC) + 2000 (现货) + 500 (浮盈) = 4310
check("权益含现货与浮盈", pf.equity0(AS, 90000), 4310)
check("维持保证金不含现货", pf.maint0(AS), 40)          # 0.01 x (3000 + 1000)
# 变动同前: 仓位 -750, 抵押品 810x-0.2 = -162 -> 3398; 维持 = 0.01x(2700 + 1050) = 37.5
r = pf.shock(AS, 90000, mv)
check("现货组合 shock equity", r.equity, 3398)
check("现货组合 shock maint", r.maint, 37.5)
check("现货组合 buffer_used", r.buffer_used, 912 / 4270, 1e-6)
check("面板重演一致", pf.replay(AS, P, "extreme").loc[0, "equity"], 3398)
check("只有现货也算有资产", pf.validate_account(pf.Account([pf.Position("NVDA", "long", 1000, kind="spot")])), None)
check("现货不能做空", pf.validate_account(pf.Account([pf.Position("NVDA", "short", 1000, kind="spot")], usdt=100)) is not None, True)
check("没填买价就当浮盈为 0", pf.Position("NVDA", "long", 3000).pnl, 0.0)
check("旧组合结果不变", pf.equity0(A, 90000), 1810)

# ---- 3. 校验 ----
check("validate ok", pf.validate_account(A), None)
check("validate unsupported", pf.validate_account(pf.Account([pf.Position("DOGE", "long", 1)], usdt=1)) is not None, True)
check("validate no collateral", pf.validate_account(pf.Account([pf.Position("NVDA", "long", 1)])) is not None, True)

# ---- 4. 多日强平必须看「路径上任意一天」, 不是只看亏得最多那天 ----
# 2026-09-18 对抗性审查发现: 旧写法取「亏损最大」那一行的 liquidated, 会漏掉更早就触线的日子。
# 空头上涨会同时压低权益并抬高维持保证金, 两者不同向 -> 最早触线的那天不一定是权益最低的那天。
#
# 手算 (维持保证金率 50%, 现金 1100, 空 S 名义 1000, 多 L 名义 1000):
#   E0 = 1100 ; M0 = 0.5 * 2000 = 1000 ; 离强平的距离 = 100
#   第 1 天 S +20% / L 0%   -> 盈亏 -200 -> 权益 900 ; 维持 = 0.5*(1200+1000) = 1100
#                              900 <= 1100 -> 已经强平 ; 亏损 200/1100 = 18.2%
#   第 2 天 S -50% / L -90% -> 盈亏 +500-900 = -400 -> 权益 700 ; 维持 = 0.5*(500+100) = 300
#                              700 <= 300 ? 否 -> 没触线 ; 亏损 400/1100 = 36.4% (更大)
#   旧写法取第 2 天 -> 报告「没强平」; 可账户在第 1 天就被平掉了, 根本活不到第 2 天。
LD = ["2026-01-02", "2026-01-05", "2026-01-06"]
liq_stocks = {"S": stock([(d, p, p, p, p) for d, p in zip(LD, [100.0, 120.0, 50.0])]),
              "L": stock([(d, p, p, p, p) for d, p in zip(LD, [100.0, 100.0, 10.0])])}
liq_btc = pd.DataFrame({"ts": [pd.Timestamp(d, tz="UTC") for d in LD],
                        "open": 0, "high": 100000, "low": 100000, "close": 100000, "volume": 0})
PL = pf.build_panel(liq_stocks, liq_btc)
AL = pf.Account([pf.Position("S", "short", 1000), pf.Position("L", "long", 1000)],
                usdt=1100, btc=0, maint_margin=0.5)
_md = pf.multi_day_worst(AL, PL, horizon=2)
_row = _md[_md["start"] == LD[0]].iloc[0]
check("多日: 报告的最差仍是亏损最大那天", float(_row["loss_pct"]), 400.0 / 1100.0, 1e-6)
check("多日: 持有期内任意一天触线即算强平", bool(_row["liquidated"]), True)


print("\n%s" % ("全部通过" if not fails else "%d 项失败" % fails))
sys.exit(1 if fails else 0)
