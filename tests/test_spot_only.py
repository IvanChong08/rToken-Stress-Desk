"""全现货、零保证金账户的离线回归测试 (不联网)。  python -X utf8 tests/test_spot_only.py

来历: 有人在持仓表里填了两笔现货、USDT/BTC/ETH 全填 0, 点运行 -> AttributeError 崩溃。
根因是 multi_day_worst 里 sum(generator) 在「一笔杠杆仓位都没有」时退化成 int 0。
顺带钉住这种账户的措辞: 现货不会被强平, 不能说「用掉多少强平距离」;
以及现货减仓要把钱记回 USDT (卖掉的股票变成现金留在账户里), 否则调整方案永远是 85 → 85。
"""
import math
import os
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from desk import links, portfolio_card as pcard, score, suggest      # noqa: E402
from desk import portfolio as pf                                     # noqa: E402

fails = 0


def check(name, got, want, tol=1e-3):
    global fails
    ok = (got is None and want is None) or (got is not None and want is not None and
                                            (got == want if isinstance(want, (bool, str)) else math.isclose(got, want, abs_tol=tol)))
    fails += not ok
    print("%s  %-44s got=%s want=%s" % ("PASS" if ok else "FAIL", name, got, want))


def stock(rows):
    return pd.DataFrame({"ts": [pd.Timestamp(d + " 14:30", tz="UTC") for d, *_ in rows],
                         "open": [x[1] for x in rows], "high": [x[2] for x in rows],
                         "low": [x[3] for x in rows], "close": [x[4] for x in rows], "volume": 0})


stocks = {
    "NVDA": stock([("2026-01-02", 100, 100, 100, 100), ("2026-01-05", 95, 101, 90, 92), ("2026-01-06", 93, 100, 91, 99)]),
    "AAPL": stock([("2026-01-02", 200, 200, 200, 200), ("2026-01-05", 190, 205, 160, 170), ("2026-01-06", 170, 175, 150, 150)]),
}
btc = pd.DataFrame({"ts": [pd.Timestamp(d, tz="UTC") for d in ["2026-01-02", "2026-01-05", "2026-01-06"]],
                    "open": 0, "high": [100000, 90000, 92000], "low": [100000, 84000, 85000],
                    "close": [100000, 88000, 90000], "volume": 0})
P = pf.build_panel(stocks, btc)

# 两笔现货各 3795 USDT, 没有任何保证金 (截图里那个账户)
S = pf.Account([pf.Position("NVDA", "long", 3795.0, kind="spot", qty=17.0, price=223.2),
                pf.Position("AAPL", "long", 3795.0, kind="spot", qty=11.0, price=345.0)],
               usdt=0.0, btc=0.0, maint_margin=0.01)

# ---- 1. 不再崩溃 ----
check("这种账户能建", pf.validate_account(S), None)
check("权益 = 现货市值", pf.equity0(S, P.btc_price), 7590)
check("维持保证金 = 0", pf.maint0(S), 0)
md = pf.multi_day_worst(S, P, horizon=2)          # 以前这里 AttributeError: int has no .values
check("multi_day_worst 出表", len(md) > 0, True)
check("多日结果没有强平", bool(md.liquidated.any()), False)
rep = pf.replay(S, P, "extreme")
check("逐日重演没有强平", bool(rep.liquidated.any()), False)
# 01-05: NVDA 90/100-1 = -10%, AAPL 160/200-1 = -20% -> -379.5 - 759 = -1138.5 -> 6451.5
check("最差单日权益", float(rep.iloc[0].equity), 6451.5)

# ---- 2. 措辞: 现货不会被强平 ----
sc = score.portfolio_scorecard(S, P, horizon=2)
detail = " ".join(i.detail for i in sc.items)
check("不提强平距离", "强平距离" in detail, False)
check("说的是亏掉本金", "本金" in detail, True)
check("规则换成现货口径", sc.rule, score.SCORE_RULE_SPOT)
# 最差单日亏 1138.5/7590 = 15% -> 85 分
check("单日得分", next(i.score for i in sc.items if i.key == "single_day"), 85.0)

rp = pcard.build_report(S, P, log=None, use_llm=False)
check("结论写明不会被强平", "不会被强平" in rp.narrative, True)
check("结论不提强平距离", "强平距离" in rp.narrative, False)
check("没有加密抵押品就不写抵押品缩水", "抵押品缩水" in rp.narrative, False)

# ---- 3. 现货减仓要把钱记回 USDT ----
h = suggest.halve_position(S, "AAPL", "long")
check("卖一半后 AAPL 名义减半", next(p.notional for p in h.positions if p.symbol == "AAPL"), 1897.5)
check("卖一半后数量也减半", next(p.qty for p in h.positions if p.symbol == "AAPL"), 5.5)
check("卖出的钱进了 USDT", h.usdt, 1897.5)
check("权益不变 (换成现金而已)", pf.equity0(h, P.btc_price), 7590)
check("分数确实变好", suggest.overall(h, P, 2) > suggest.overall(S, P, 2), True)
# 杠杆仓位减仓不该凭空多出 USDT
L = pf.Account([pf.Position("NVDA", "long", 4000.0)], usdt=2000.0, maint_margin=0.01)
check("杠杆减仓不加 USDT", suggest.halve_position(L, "NVDA", "long").usdt, 2000)

# ---- 4. 去 Bitget 的步骤: 现货不该给永续链接 ----
steps = links.adjustment_steps(S, h)
pos = [s for s in steps if s["kind"] == "position"][0]
check("步骤写现货不写做多", "现货" in pos["text"] and "做多" not in pos["text"], True)
check("现货给现货链接", bool(pos["spot_url"]), True)
check("现货不给永续链接", pos["futures_url"], "")
usdt_step = [s for s in steps if s["symbol"] == "USDT"][0]
check("USDT 不叫保证金", "保证金" in usdt_step["text"], False)
# 杠杆账户照旧
lsteps = links.adjustment_steps(L, suggest.halve_position(L, "NVDA", "long"))
check("杠杆仓位仍给永续链接", bool(lsteps[0]["futures_url"]), True)
check("杠杆仓位仍写做多", "做多" in lsteps[0]["text"], True)

print("\n%s" % ("全部通过" if not fails else "%d 项失败" % fails))
sys.exit(1 if fails else 0)
