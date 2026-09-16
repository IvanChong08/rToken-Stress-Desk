"""组合压力测试真实数据跑批 (Yahoo 5 年)。  python -X utf8 tests/run_portfolio.py"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from desk import portfolio as pf  # noqa: E402
from desk.provenance import CallLog, verify_log  # noqa: E402

LOG = os.path.join(ROOT, "data", "calls_portfolio.jsonl")

# 示例: 0.1 BTC + 5000 USDT 作保证金, 多 NVDA 15000 / MSTR 8000 / COIN 5000, 空 SPY 5000 对冲
A = pf.Account([pf.Position("NVDA", "long", 15000), pf.Position("MSTR", "long", 8000),
                pf.Position("COIN", "long", 5000), pf.Position("SPY", "short", 5000)],
               usdt=5000, btc=0.1, maint_margin=0.01, btc_haircut=0.95)
assert pf.validate_account(A) is None

t0 = time.time()
P = pf.fetch_panel([p.symbol for p in A.positions], CallLog(LOG))
print("取数 %.1fs · %d 个交易日 %s ~ %s · BTC %.0f (%s)" % (time.time() - t0, len(P.close_ret), P.dates[0], P.dates[-1],
                                                        P.btc_price, P.btc_price_date))
e0, m0 = pf.equity0(A, P.btc_price), pf.maint0(A)
print("E0 %.0f · M0 %.0f · 名义 %.0f · 有效杠杆 %.2fx\n" % (e0, m0, pf.gross_notional(A), pf.gross_notional(A) / e0))

for mode in ("extreme", "close"):
    rep = pf.replay(A, P, mode)
    print("[%s] 强平日数 %d / %d" % (mode, int(rep.liquidated.sum()), len(rep)))
    print(rep.head(8)[["date", "positions_pnl", "collateral_pnl", "loss_pct", "buffer_used", "liquidated", "btc_move",
                       "worst_position"]].to_string(float_format=lambda x: "%.3f" % x))
    print()

md = pf.multi_day_worst(A, P, 5)
print("[5 日收盘口径] 强平起点数 %d" % int(md.liquidated.sum()))
print(md.head(5).to_string(float_format=lambda x: "%.3f" % x))

worst_date = pf.replay(A, P, "extreme").loc[0, "date"]
mv = pf.day_moves(A, P, next(d for d in P.close_ret.index if str(d) == worst_date))
print("\n最差日 %s 变动 %s" % (worst_date, {k: round(v, 4) for k, v in mv.items()}))
print("放大到强平 k = %s" % pf.scale_to_liquidation(A, P.btc_price, mv))
print("同步不利 x (BTC 跟跌) = %s · (BTC 不动) = %s" % (pf.uniform_move_to_liquidation(A, P.btc_price, True),
                                                   pf.uniform_move_to_liquidation(A, P.btc_price, False)))
for ps in pf.PRESETS:
    r = pf.shock(A, P.btc_price, pf.preset_moves(A, ps))
    print("预设 %-30s 亏损 %.1f%% 缓冲用掉 %.1f%% 强平 %s" % (ps["name"], 100 * r.loss_pct, 100 * r.buffer_used, r.liquidated))
print("\n调用日志:", verify_log(LOG))
