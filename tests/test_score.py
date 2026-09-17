"""打分模块离线测试 (合成行情 + 手算)。  python -X utf8 tests/test_score.py"""
import math
import os
import sys
from datetime import date

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from desk import portfolio as pf  # noqa: E402
from desk import score  # noqa: E402

fails = 0


def check(name, got, want, tol=1e-3):
    global fails
    ok = got == want if isinstance(want, (bool, str, type(None))) else math.isclose(got, want, abs_tol=tol)
    fails += not ok
    print("%s  %-40s got=%s want=%s" % ("PASS" if ok else "FAIL", name, got, want))


# 与 test_portfolio_engine.py 相同的合成行情
def stock(rows):
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
A = pf.Account([pf.Position("NVDA", "long", 3000), pf.Position("MSTR", "long", 2000), pf.Position("TSLA", "short", 1000)],
               usdt=1000, btc=0.01, maint_margin=0.01, btc_haircut=0.9)

# E0 = 1000 + 0.01*90000*0.9 = 1810 ; M0 = 60 ; 强平距离 = 1750 ; 抵押品价值 810
# 单日: 01-05 最差 E=898, 亏 912 -> 用掉 0.521143 -> 47.886
# 多日: 01-02 起第 1 天 E=1152.8, 亏 657.2 -> 0.375543 -> 62.446
# 预设 (多头随价格, TSLA 空头反向):
#   美股-10%/BTC-20%: -300-200+100 -162 = -562 -> 67.886
#   周末真空 -5%/-10%: -150-100+50 -81 = -281 -> 83.943
#   只有 BTC -30%: -243 -> 86.114
#   只有美股 -15%: -450-300+150 = -600 -> 65.714  <- 最差
# 总分 = min(47.886, 62.446, 65.714) = 47.886 (单日)
sc = score.portfolio_scorecard(A, P, horizon=5, today=date(2026, 1, 8))
s = {i.key: i for i in sc.items}
check("单日得分", s["single_day"].score, 47.886)
check("多日得分", s["multi_day"].score, 62.446)
check("预设得分", s["presets"].score, 65.714)
check("预设最差是「只有美股 -15%」", "只有美股" in s["presets"].detail, True)
check("总分 = 最低项", sc.overall, 47.886)
check("短板", sc.weakest, "single_day")
check("新鲜度 ok (2 天)", [e for e in sc.evidence if e.label == "数据新鲜度"][0].status, "ok")
check("样本 2 天 -> warn", [e for e in sc.evidence if e.label == "历史样本"][0].status, "warn")
sc_old = score.portfolio_scorecard(A, P, today=date(2026, 2, 1))
check("新鲜度 warn (26 天)", [e for e in sc_old.evidence if e.label == "数据新鲜度"][0].status, "warn")

# 已强平: 只有 100 USDT, 距离 = 40; 01-05 亏 750 -> 强平 -> 0 分
B = pf.Account(A.positions, usdt=100, maint_margin=0.01)
scb = score.portfolio_scorecard(B, P, today=date(2026, 1, 8))
check("强平 -> 单日 0 分", {i.key: i for i in scb.items}["single_day"].score, 0.0)
check("强平 -> 总分 0", scb.overall, 0.0)

# 单笔: 3 倍, 维持 1% -> 距离 0.323333; 5 年最差单日 18.17% -> 用掉 0.561959 -> 43.804
#       周末最差 3.52% -> 0.108866 -> 89.113; 总分 43.804 (单日)
t = score.trade_scorecard(3, 0.01, 0.1817, 0, 1253, "2026-09-14", 0.0352, 0, 12, today=date(2026, 9, 16))
ts = {i.key: i for i in t.items}
check("单笔 单日", ts["single_day"].score, 43.804)
check("单笔 周末", ts["weekend"].score, 89.113)
check("单笔 总分", t.overall, 43.804)
check("单笔 周末样本 warn", [e for e in t.evidence if e.label == "周末样本"][0].status, "warn")
# 5 倍 MSTR 5 年触及 12 次 -> 0 分
t2 = score.trade_scorecard(5, 0.01, 0.2928, 12, 1253, "2026-09-14", today=date(2026, 9, 16))
check("单笔 触及强平 -> 0", t2.overall, 0.0)

check("from_buffer_used 截断上限", score.from_buffer_used(-0.5), 100.0)
check("from_buffer_used 截断下限", score.from_buffer_used(1.7), 0.0)
check("grade", score.grade(29.9) + score.grade(30) + score.grade(60), "危险警惕稳健")

# 缺数据不能给满分: min(100.0, nan) 在 Python 里返回 100.0 (2026-09-18 对抗性审查实测)
check("NaN 记 0 分, 不是满分", score.from_buffer_used(float("nan")), 0.0)
check("NaN + 已强平也是 0", score.from_buffer_used(float("nan"), True), 0.0)
check("正常值不受影响", score.from_buffer_used(0.4), 60.0)

print("\n%s" % ("全部通过" if not fails else "%d 项失败" % fails))
sys.exit(1 if fails else 0)
