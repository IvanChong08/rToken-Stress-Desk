"""
历史相似情景检索的离线测试 (合成行情 + 手算期望值, 不联网)。
  python -X utf8 tests/test_analog.py

期望值全部在本文件注释里手算, 不调用被测函数推导。
第 7-10 组是「方法论性质」测试 —— 验的不是「代码有没有按注释执行」, 是「规则本身对不对」:
  7  防前视: 把面板截断到 t 重算, 第 t 行必须一字不差
  8  仓位特征与抵押品无关 (换一个账户必须完全相同), 但抵押品必须影响后续结果
  9  同榜可比: 核心版每个候选日用完全相同的特征集合; 删掉 DVOL 不改变核心版排名
 10  排序看不见未来收益; 压力反例必须与「最相似」分开
"""
import math
import os
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from desk import analog as an  # noqa: E402
from desk import ui  # noqa: E402
from desk import portfolio as pf  # noqa: E402

fails = 0
checks = 0


def check(name, got, want, tol=1e-6):
    global fails, checks
    checks += 1
    if want is None or got is None:
        ok = got is None and want is None
    elif isinstance(want, (bool, str, list, tuple)):
        ok = got == want
    else:
        ok = math.isclose(got, want, abs_tol=tol)
    fails += not ok
    print("%s  %-54s got=%s want=%s" % ("PASS" if ok else "FAIL", name, got, want))


def stock(rows):  # rows: (NY 日期, open, high, low, close)
    return pd.DataFrame({"ts": [pd.Timestamp(d + " 14:30", tz="UTC") for d, *_ in rows],
                         "open": [x[1] for x in rows], "high": [x[2] for x in rows],
                         "low": [x[3] for x in rows], "close": [x[4] for x in rows], "volume": 0})


# ================================================================ 1. trailing_pct_rank
# s = [5, 1, 3, 2, 4], window=3, min_periods=2
#   i0: 只有 1 个点 < min_periods        -> NaN
#   i1: 窗口 [5,1]   末值 1, <=1 的有 1 个 -> 50%
#   i2: 窗口 [5,1,3] 末值 3, <=3 的有 2 个 -> 66.667%
#   i4: 窗口 [3,2,4] 末值 4, <=4 的有 3 个 -> 100%
pr = an.trailing_pct_rank(pd.Series([5.0, 1, 3, 2, 4]), window=3, min_periods=2)
check("pct_rank i0 是 NaN", bool(pd.isna(pr.iloc[0])), True)
check("pct_rank i1", float(pr.iloc[1]), 50.0)
check("pct_rank i2", float(pr.iloc[2]), 100.0 * 2 / 3, 1e-4)
check("pct_rank i4", float(pr.iloc[4]), 100.0)

# ================================================================ 2. robust_scale
# x = [1,2,3,4,5]: 中位数 3, Q1 2, Q3 4 -> IQR 2 -> 缩放后 [-1,-0.5,0,0.5,1]
# c 是常数列: IQR = 0 -> 无法缩放 -> 整列丢掉 (不能当成「完全一样」凑进距离)
sc, scaler = an.robust_scale(pd.DataFrame({"x": [1.0, 2, 3, 4, 5], "c": [7.0, 7, 7, 7, 7]}))
check("scale 丢掉常数列", list(sc.columns), ["x"])
check("scale x[0]", float(sc["x"].iloc[0]), -1.0)
check("scale x[3]", float(sc["x"].iloc[3]), 0.5)
check("scaler 记下中位数", scaler["x"]["median"], 3.0)
check("常数列的 IQR 记为 None", scaler["c"]["iqr"], None)

# ================================================================ 3. row_distance (加权曼哈顿)
# 组权重 vol .30 / mkt .20 / coupling .25 / pos .25
# 用到的特征: vix->vol, btc_ret1->coupling, pos_ret1->pos, pos_rv5->pos  (没有 mkt 组的特征)
# 出现的组 vol+coupling+pos -> total_w = .30+.25+.25 = .80, 在这三组之间重新归一
# query 全 0; row: vix=1, btc_ret1=2, pos_ret1=3, pos_rv5=5
#   vol      : 1 个特征 -> 权重 .30/.80/1 = .375    -> .375 * 1 = .375
#   coupling : 1 个特征 -> 权重 .25/.80/1 = .3125   -> .3125 * 2 = .625
#   pos      : 2 个特征 -> 权重 .25/.80/2 = .15625  -> .15625*3 + .15625*5 = 1.25
#   合计 = .375 + .625 + 1.25 = 2.25 ; 有效特征数 4
KEYS4 = ["vix", "btc_ret1", "pos_ret1", "pos_rv5"]
q = pd.Series({"vix": 0.0, "btc_ret1": 0.0, "pos_ret1": 0.0, "pos_rv5": 0.0})
row = pd.Series({"vix": 1.0, "btc_ret1": 2.0, "pos_ret1": 3.0, "pos_rv5": 5.0})
d, n, detail = an.row_distance(q, row, keys=KEYS4)
check("distance 四特征", d, 2.25)
check("有效特征数 4", n, 4)
check("detail 记录裸差值", detail["pos_rv5"]["gap"], 5.0)
# 贡献 = 权重 x 裸差值。reasons 必须按贡献排 —— 按裸差值排会把权重很小的特征说成「最像」
check("detail 记录加权贡献", detail["pos_rv5"]["contrib"], 0.15625 * 5)
check("裸差值最大的项贡献未必最大: btc_ret1 差 2 贡献 .625 > pos_ret1 差 3 贡献 .469",
      detail["btc_ret1"]["contrib"] > detail["pos_ret1"]["contrib"], True)

# 只剩一个特征时必须在「可用的组之间」重新归一, 否则缺数据的日子会显得更近:
#   只有 vol 组 -> total_w = .30 -> 权重 .30/.30/1 = 1.0 -> 距离 = 1.0 (不是 .30)
row2 = pd.Series({"vix": 1.0, "btc_ret1": float("nan"), "pos_ret1": float("nan"), "pos_rv5": float("nan")})
d2, n2, _ = an.row_distance(q, row2, keys=KEYS4)
check("缺特征时重新归一", d2, 1.0)
check("有效特征数 1", n2, 1)

# 一个共同特征都没有 -> NaN, 不能当成距离 0
d3, n3, _ = an.row_distance(q, pd.Series({k: float("nan") for k in KEYS4}), keys=KEYS4)
check("无共同特征 -> NaN", bool(d3 != d3), True)

# ================================================================ 4. dedup (去重窗口)
# 位置: a=0 b=3 c=12 d=20 e=22 ; 按距离升序 a,b,c,d,e ; min_gap=10
#   a 收; b 与 a 相隔 3 < 10 -> 跳过; c 与 a 相隔 12 >= 10 -> 收;
#   d 与 c 相隔 8 < 10 -> 跳过; e 与 c 相隔 10 >= 10 -> 收
POS = {"a": 0, "b": 3, "c": 12, "d": 20, "e": 22}
ranked = [{"date": k, "distance": i * 0.1} for i, k in enumerate(["a", "b", "c", "d", "e"])]
check("dedup 过滤相邻日期", [r["date"] for r in an.dedup(ranked, POS, top_n=5, min_gap=10)], ["a", "c", "e"])
check("dedup 尊重 top_n", [r["date"] for r in an.dedup(ranked, POS, top_n=2, min_gap=10)], ["a", "c"])

# ================================================================ 5. forward_outcomes
DAYS = ["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"]
NV = [100.0, 110.0, 121.0, 100.0, 90.0]
sm = stock([(d, p, p, p, p) for d, p in zip(DAYS, NV)])
bt = pd.DataFrame({"ts": [pd.Timestamp(d, tz="UTC") for d in DAYS],
                   "open": 0, "high": 100000, "low": 100000, "close": 100000, "volume": 0})
P5 = pf.build_panel({"NVDA": sm}, bt)

# 账户: 1000U 现金 + NVDA 多单名义 1000, 维持保证金率 1%, 无 BTC 抵押
#   E0 = 1000 ; M0 = 10 ; BTC 价格不动 -> 抵押品盈亏恒为 0
A1 = pf.Account([pf.Position("NVDA", "long", 1000)], usdt=1000, btc=0, maint_margin=0.01)
# 从 2026-01-02 起:
#   h=1: 110/100-1 = +10% -> 权益 1100 ; 维持 = 0.01*1000*1.1 = 11
#   h=3: j1 权益 1100 (维持 11) / j2 (+21%) 权益 1210 (维持 12.1) / j3 (0%) 权益 1000 (维持 10)
#        期末 1000 -> 收益 0% ; 期间最小缓冲 = 1000 - 10 = 990
oc = an.forward_outcomes(A1, P5, pd.Timestamp("2026-01-02").date(), horizons=(1, 3))
check("h=1 收益", oc[0]["ret_pct"], 0.10)
check("h=1 期间最差", oc[0]["worst_pct"], 0.10)
check("h=1 未强平", oc[0]["liquidated"], False)
check("h=3 期末收益", oc[1]["ret_pct"], 0.0)
check("h=3 期间最小缓冲 (权益 - 维持保证金)", oc[1]["worst_buffer"], 990.0)

# 样本末尾不够 h 天: 必须如实说不够, 不能当成 0 或者悄悄跳过
oc2 = an.forward_outcomes(A1, P5, pd.Timestamp("2026-01-07").date(), horizons=(1, 5))
check("末尾 h=1 仍可算", oc2[0]["ret_pct"], 90.0 / 100.0 - 1)
check("末尾 h=5 收益为 None", oc2[1]["ret_pct"], None)
check("末尾 h=5 强平判定为 None", oc2[1]["liquidated"], None)

# ---- 强平必须看路径上任意一天 (与 test_portfolio_engine.py 同一个反例) ----
# 维持保证金率 50%, 现金 1100, 空 S 名义 1000, 多 L 名义 1000:
#   第 1 天 S +20%          -> 权益 900, 维持 1100 -> 已经强平 (亏 18.2%)
#   第 2 天 S -50% / L -90% -> 权益 700, 维持  300 -> 没触线, 但亏得更多 (36.4%)
# 只看「权益最低那天」会报告「没强平」—— 可账户在第 1 天就被平掉了, 活不到第 2 天。
LD = ["2026-01-02", "2026-01-05", "2026-01-06"]
PL = pf.build_panel(
    {"S": stock([(d, p, p, p, p) for d, p in zip(LD, [100.0, 120.0, 50.0])]),
     "L": stock([(d, p, p, p, p) for d, p in zip(LD, [100.0, 100.0, 10.0])])},
    pd.DataFrame({"ts": [pd.Timestamp(d, tz="UTC") for d in LD],
                  "open": 0, "high": 100000, "low": 100000, "close": 100000, "volume": 0}))
AL = pf.Account([pf.Position("S", "short", 1000), pf.Position("L", "long", 1000)],
                usdt=1100, btc=0, maint_margin=0.5)
ocl = an.forward_outcomes(AL, PL, pd.Timestamp(LD[0]).date(), horizons=(2,))
check("路径上任意一天触线即算强平", ocl[0]["liquidated"], True)
check("期间最差仍来自权益最低那天", ocl[0]["worst_pct"], -400.0 / 1100.0, 1e-9)

# ================================================================ 6. 周期行情: 相似日应当落在同相位
# 构造 120 个交易日, 日收益只取决于 t mod 20 -> 第 t 天与第 t-20 天的所有特征完全相同。
# 这样「最相似的日子」有唯一正确答案, 不依赖任何随机性。
PERIOD, N = 20, 120


def wave(t):
    return 0.02 * math.sin(2 * math.pi * t / PERIOD)


def series(start, phase):
    px, out = start, []
    for t in range(N):
        px *= (1 + wave(t + phase))
        out.append(px)
    return out


ny = [d.strftime("%Y-%m-%d") for d in pd.date_range("2024-01-01", periods=N, freq="D")]
SP, BP = series(100.0, 0), series(50000.0, 7)
sm2 = stock([(d, p, p, p, p) for d, p in zip(ny, SP)])
bt2 = pd.DataFrame({"ts": [pd.Timestamp(d, tz="UTC") for d in ny], "open": 0,
                    "high": BP, "low": BP, "close": BP, "volume": 0})
PN = pf.build_panel({"NVDA": sm2}, bt2)
A2 = pf.Account([pf.Position("NVDA", "long", 5000)], usdt=2000, btc=0.05, maint_margin=0.01, btc_haircut=0.95)

res = an.find_analogs(A2, PN, top_n=4, min_gap=10, exclude_recent=10, horizons=(1, 3, 5))
feat_dates = list(an.build_features(A2, PN).index)
pos = {d: i for i, d in enumerate(feat_dates)}
today_i = len(feat_dates) - 1
phases = [pos[pd.Timestamp(r["date"]).date()] % PERIOD for r in res["analogs"]]
check("返回 4 个相似日", len(res["analogs"]), 4)
check("全部落在今天的相位上", phases, [today_i % PERIOD] * 4)
check("最近的那个距离约等于 0", res["analogs"][0]["distance"], 0.0, 1e-6)
sel = sorted(pos[pd.Timestamp(r["date"]).date()] for r in res["analogs"])
check("去重窗口生效 (两两相隔 >= 10)", all(b - a >= 10 for a, b in zip(sel, sel[1:])), True)
check("排除最近 10 个交易日", max(sel) <= today_i - 10, True)
check("每个相似日给两条「最像」", [len(r["reasons"]) for r in res["analogs"]], [2, 2, 2, 2])
check("每个相似日还要给一条「最不像」", all(bool(r["unlike"]) for r in res["analogs"]), True)
check("每个相似日都带后续 1/3/5 日", [len(r["outcomes"]) for r in res["analogs"]], [3, 3, 3, 3])
check("可复现: 两次结果完全一致",
      [r["date"] for r in an.find_analogs(A2, PN, top_n=4, min_gap=10, exclude_recent=10,
                                          horizons=(1, 3, 5))["analogs"]],
      [r["date"] for r in res["analogs"]])

# ================================================================ 7. 防前视: 截断重算不变
MARKET = ["btc_ret1", "btc_rv5"]
POSF = ["pos_ret1", "pos_rv5", "corr20"]
full = an.build_features(A2, PN)


def diffs(cols, k_list=(40, 70, 100)):
    bad = []
    for k in k_list:
        pk = pf.build_panel({"NVDA": sm2.iloc[:k]}, bt2.iloc[:k])
        fk = an.build_features(A2, pk)
        day = fk.index[-1]
        for col in cols:
            a_, b_ = full.loc[day, col], fk.loc[day, col]
            if not ((pd.isna(a_) and pd.isna(b_)) or math.isclose(float(a_), float(b_), abs_tol=1e-12)):
                bad.append("%s@%s: 全量=%s 截断=%s" % (col, day, a_, b_))
    return bad


check("防前视: 市场特征原样截断不变", diffs(MARKET), [])
# 仓位特征除以名义总额 (不是账户权益), 与「今天有多少抵押品」无关 -> 截断也必须一字不差。
# (旧版除以 e0 时这一条会失败, 因为 e0 含今天的 BTC 价格。)
check("防前视: 仓位特征原样截断不变", diffs(POSF), [])

# ================================================================ 8. 仓位特征必须与抵押品无关
# 同一套股票仓位, 一个账户没有 BTC 抵押、另一个有大量 BTC 抵押。
# 相似度只描述「这套股票仓位当时遇到了什么市场」, 不该被账户里装了多少 BTC 改变 ——
# 否则「股票仓位与 BTC 的相关性」里有一部分只是账户公式自带的, 不是真实的市场耦合。
A_nobtc = pf.Account([pf.Position("NVDA", "long", 5000)], usdt=2000, btc=0.0, maint_margin=0.01)
A_bigbtc = pf.Account([pf.Position("NVDA", "long", 5000)], usdt=2000, btc=5.0, maint_margin=0.01)
f_no, f_big = an.build_features(A_nobtc, PN), an.build_features(A_bigbtc, PN)
check("抵押品不影响仓位特征 (最大差异)",
      max(float(abs(f_no[c] - f_big[c]).max()) for c in POSF), 0.0, 1e-15)
# 反向确认: 抵押品必须影响后续账户结果, 否则上一条说明抵押品被整个忽略了
o_no = an.forward_outcomes(A_nobtc, PN, feat_dates[30], horizons=(5,))[0]["ret_pct"]
o_big = an.forward_outcomes(A_bigbtc, PN, feat_dates[30], horizons=(5,))[0]["ret_pct"]
check("但抵押品必须影响后续结果 (证明没被忽略)", abs(o_no - o_big) > 1e-9, True)

# ================================================================ 9. 同榜可比 + 双榜隔离
# 造一列只覆盖后半段的假 DVOL: 核心版不能用它, DVOL 版只能在它有值的日子里检索。
dv_dates = feat_dates[N // 2:]
fake_dvol = pd.Series([40.0 + 5 * math.sin(i / 3.0) for i in range(len(dv_dates))], index=dv_dates)
res2 = an.find_analogs(A2, PN, dvol=fake_dvol, top_n=4, min_gap=10, exclude_recent=10, horizons=(1, 3, 5))
check("核心版: 所有候选用完全相同的特征数", len({r["n_features"] for r in res2["analogs"]}), 1)
check("核心版: 不含 DVOL", "BTC DVOL" not in res2["features_used"], True)
check("DVOL 版: 含 DVOL", "BTC DVOL" in res2["dvol_tier"]["features_used"], True)
check("DVOL 版: 候选数少于核心版 (只在有 DVOL 的日子里找)",
      res2["dvol_tier"]["n_candidates"] < res2["n_candidates"], True)
check("加不加 DVOL, 核心版排名完全不变",
      [r["date"] for r in res2["analogs"]], [r["date"] for r in res["analogs"]])
check("DVOL 取不到时如实说明, 不是空白", an.find_analogs(A2, PN, top_n=2)["dvol_tier"]["note"] != "", True)

# ================================================================ 10. 排序看不见未来 + 反例要分开
scaled, _ = an.robust_scale(full)
cands = list(scaled.index[:80])
kk = [k for k in an.CORE_KEYS if k in scaled.columns]
base = [str(r["date"]) for r in an.rank_candidates(scaled, scaled.iloc[-1], cands, keys=kk)]
poisoned = scaled.copy()
poisoned["future_ret"] = [999.0 * (i % 7) for i in range(len(poisoned))]
after = [str(r["date"]) for r in an.rank_candidates(poisoned, poisoned.iloc[-1], cands, keys=kk)]
check("排序无视未来收益列 (%d 个候选全序相同)" % len(base), after == base, True)
moved = scaled.copy()
moved["btc_ret1"] = moved["btc_ret1"] + 5.0
check("改真特征会改变排序 (证明上一条不是空测)",
      [str(r["date"]) for r in an.rank_candidates(moved, scaled.iloc[-1], cands, keys=kk)] != base, True)

nb = res["neighborhood"]
check("邻域统计存在", bool(nb), True)
check("邻域里挑出的反例数量", len(nb.get("adverse", [])), an.ADVERSE_N)
check("反例写明是按事后损失挑的", "按事后 5 日损失挑出来的压力反例" in nb.get("note", ""), True)
check("邻域给了全历史基准做对照", "base_neg_share" in nb, True)
check("反例与「最相似」不是同一批 (否则这一层没起作用)",
      [x["date"] for x in nb["adverse"]] != [r["date"] for r in res["analogs"][:an.ADVERSE_N]], True)
check("免责声明写明不是概率预测", "不构成概率预测" in res["disclaimer"], True)

# ================================================================ 11. 第二轮复核修的五处
# --- 11.1 中位数: 偶数样本不能取「偏大的那一个」---
# 旧写法 vals[len(vals)//2] 在 [1,3] 上返回 3, 正确答案是 2。
def _fake_nb(vals_by_date, min_gap=5, n=30):
    """直接喂给 _neighborhood 一组构造好的日期与未来收益。"""
    ds = list(vals_by_date)
    ranked_ = [{"date": d, "distance": 0.01 * k, "n_features": 5, "detail": {}}
               for k, d in enumerate(ds)]
    fwd = pd.Series(vals_by_date, dtype=float)
    return an._neighborhood(A2, PN, ranked_, fwd, pos, n=n, min_gap=min_gap,
                            horizons=(1, 3, 5), raw=full, today=feat_dates[-1])


# 四个日期, 两两相隔 20 个交易日 (远大于 min_gap=5, 所以去重不会淘汰任何一个)
FOUR = [feat_dates[20], feat_dates[40], feat_dates[60], feat_dates[80]]
nb4 = _fake_nb(dict(zip(FOUR, [-0.10, -0.02, 0.03, 0.08])))
check("11.1 偶数样本中位数 = 中间两个的平均", nb4["median"], (-0.02 + 0.03) / 2, 1e-12)
check("11.1 旧写法会给出的错误值应被避免", abs(nb4["median"] - 0.03) > 1e-9, True)
nb3 = _fake_nb(dict(zip(FOUR[:3], [-0.10, -0.02, 0.03])))
check("11.1 奇数样本中位数 = 正中间那个", nb3["median"], -0.02, 1e-12)
check("11.1 为负比例", nb4["neg_share"], 0.5, 1e-12)
check("11.1 最差值", nb4["worst"], -0.10, 1e-12)

# --- 11.2 邻域必须按事件去重 ---
# 手算 (min_gap=5 表示「相隔不足 5 个交易日才淘汰」, 正好相隔 5 的保留):
#   位置 30,31,32,33 连续四天 + 位置 80。按距离升序处理:
#   30 收; 31(差1) 32(差2) 33(差3) 全部不足 5 -> 淘汰; 80(差50) 收。
#   -> 4 个连续日只算 1 个事件, 总共 2 个。不去重的话这一次崩盘会占掉 4 票。
RUN = [feat_dates[k] for k in (30, 31, 32, 33)] + [feat_dates[80]]
nb_run = _fake_nb({d: -0.05 for d in RUN}, min_gap=5)
check("11.2 连续 4 天 + 1 个远日 -> 去重后只剩 2 个事件", nb_run["n"], 2)
check("11.2 去重前的原始个数如实记录", nb_run["n_raw"], 5)
# 边界: 正好相隔 min_gap 的必须保留 (不是「不足」)
EDGE = [feat_dates[30], feat_dates[35], feat_dates[80]]
check("11.2 正好相隔 min_gap 的保留", _fake_nb({d: -0.05 for d in EDGE}, min_gap=5)["n"], 3)
check("11.2 去重窗口写进结果", nb_run["min_gap"], 5)
check("11.2 去重不会超过 top_n", _fake_nb({d: -0.05 for d in FOUR}, min_gap=5, n=2)["n"], 2)

# --- 11.3 邻域与基准必须来自同一个候选宇宙 ---
# 基准不能是「整条 fwd5 序列」(里面混着预热期和最近 N 天), 必须是这张榜的全部合格候选日。
res3 = an.find_analogs(A2, PN, top_n=4, min_gap=10, exclude_recent=10, horizons=(1, 3, 5))
fwd5_all = an.forward_return_series(A2, PN, 5)
check("11.3 基准样本数 = 候选日数, 不是整条序列长度",
      res3["neighborhood"]["base_n"], res3["n_candidates"])
check("11.3 基准确实小于整条序列 (证明这条不是空测)",
      res3["neighborhood"]["base_n"] < int(fwd5_all.notna().sum()), True)

# --- 11.4 date_range 必须是时间范围, 不是距离排序的首尾 ---
dr = res3["date_range"]
check("11.4 date_range 左边早于右边", dr.split(" ~ ")[0] < dr.split(" ~ ")[1], True)

# --- 11.5 「一年内百分位」就必须满 252 个观测才给值 ---
long_s = pd.Series(range(400), dtype=float)
pct252 = an.trailing_pct_rank(long_s)
check("11.5 前 251 个观测不产生百分位", int(pct252.notna().sum()), 400 - 251)
check("11.5 第 252 个开始有值", bool(pd.notna(pct252.iloc[251])), True)
check("11.5 PCT_MIN 与窗口一致 (名字才对得上)", an.PCT_MIN, an.PCT_WINDOW)

# --- 11.6 路径中途强平: 后面几天的收益是假设值, 必须标出来 ---
# 复用第 5 节的反例: 第 1 天就触线, 第 2 天没触线但亏更多。
ocl3 = an.forward_outcomes(AL, PL, pd.Timestamp(LD[0]).date(), horizons=(1, 2))
check("11.6 记录第几天首次触线", ocl3[0]["liq_day"], 1)
check("11.6 只看 1 天时不算「假设值」(触线当天就是终点)", ocl3[0]["counterfactual"], False)
check("11.6 看 2 天时第 2 天是假设值", ocl3[1]["counterfactual"], True)
check("11.6 没触线时 liq_day 为 None", an.forward_outcomes(A1, P5, pd.Timestamp("2026-01-02").date(),
                                                          horizons=(1,))[0]["liq_day"], None)

# --- 11.7 核心特征整列缺失时: 显式降级, 不能对外仍宣称固定 8 项 ---
res_no_vix = an.find_analogs(A2, PN, vix=None, spy=None, top_n=3, horizons=(1, 3, 5))
used_n = len(res_no_vix["features_used"])
check("11.7 缺 VIX/SPY 时特征数确实变少", used_n < len(an.CORE_KEYS), True)
check("11.7 对外报告的特征数 = 实际用的项数",
      used_n, ({a["n_features"] for a in res_no_vix["analogs"]}.pop() if res_no_vix["analogs"] else used_n))
check("11.7 notes 里如实列出没用上的特征",

      any("没用上的特征" in n for n in res_no_vix["notes"]), True)

# ================================================================ 12. 时间戳映射 (钉住审计结论)
# 2026-09-19 逐条审计真实 Yahoo 时间戳的发现:
#   VIX 07:00 UTC / SPY 13:30 UTC  -> 转纽约后日期不变
#   BTC 00:00 UTC                  -> 转纽约后掉到前一天 (整体偏移一天)
# 所以美股类才能转纽约, 加密类必须留在 UTC。这条不钉住的话, 以后谁把 BTC 也塞进
# _daily_from_yahoo(..., "America/New_York") 就会悄悄把全部加密数据错位一天, 而且不会报错。
def _one_bar(ts_utc, close=100.0):
    return pd.DataFrame({"ts": [pd.Timestamp(ts_utc, tz="UTC")], "open": close, "high": close,
                         "low": close, "close": close, "volume": 0})


_vix_like = an._daily_from_yahoo(_one_bar("2026-09-18 07:00"), "America/New_York")
_spy_like = an._daily_from_yahoo(_one_bar("2026-09-18 13:30"), "America/New_York")
_btc_like = an._daily_from_yahoo(_one_bar("2026-09-18 00:00"), "America/New_York")
check("12 VIX 时间戳转纽约后日期不变", str(_vix_like.index[0]), "2026-09-18")
check("12 SPY 时间戳转纽约后日期不变", str(_spy_like.index[0]), "2026-09-18")
check("12 BTC 时间戳转纽约会偏移到前一天 (所以不能这么用)", str(_btc_like.index[0]), "2026-09-17")
# 引擎里 BTC 走的是 portfolio._btc_daily (UTC), 不是上面那条路 —— 验证它不偏移
_btc_engine = pf._btc_daily(_one_bar("2026-09-18 00:00"))
check("12 引擎的 BTC 按 UTC 处理, 日期不偏移", str(_btc_engine.index[0]), "2026-09-18")

# ================================================================ 13. 逐个数据源缺失时的降级
# GPT 的验收要求: 「数据源缺失时 UI 文案与真实降级状态一致」, 且不得内部降级却对外仍宣称固定 8 项。
# 这里逐个把外部序列打掉, 检查三件事: 特征数对不对、notes 有没有如实说、结果还能不能出。
_VIXF, _SPYF = an.FEATURES["vix"][0], an.FEATURES["spy_ret5"][0]
_PCTF = an.FEATURES["vix_pct"][0]
_fake_vix = pd.Series([15.0 + (i % 7) for i in range(400)],
                      index=pd.date_range("2023-06-01", periods=400, freq="D").date, dtype=float)
_fake_spy = pd.Series([400.0 + i * 0.1 for i in range(400)],
                      index=pd.date_range("2023-06-01", periods=400, freq="D").date, dtype=float)

_full = an.find_analogs(A2, PN, vix=_fake_vix, spy=_fake_spy, top_n=3, horizons=(1, 3, 5))
_no_vix = an.find_analogs(A2, PN, vix=None, spy=_fake_spy, top_n=3, horizons=(1, 3, 5))
_no_spy = an.find_analogs(A2, PN, vix=_fake_vix, spy=None, top_n=3, horizons=(1, 3, 5))
_none = an.find_analogs(A2, PN, vix=None, spy=None, top_n=3, horizons=(1, 3, 5))

check("13 全部数据源在位时特征最多",
      len(_full["features_used"]) > len(_no_vix["features_used"]), True)
check("13 缺 VIX -> features_used 里没有 VIX", _VIXF not in _no_vix["features_used"], True)
check("13 缺 VIX -> 连带没有 VIX 百分位", _PCTF not in _no_vix["features_used"], True)
check("13 缺 SPY -> features_used 里没有 SPY", _SPYF not in _no_spy["features_used"], True)
check("13 缺 VIX 时 notes 点名 VIX", any(_VIXF in n for n in _no_vix["notes"]), True)
check("13 缺 SPY 时 notes 点名 SPY", any(_SPYF in n for n in _no_spy["notes"]), True)
for _lab, _r in (("全在位", _full), ("缺VIX", _no_vix), ("缺SPY", _no_spy), ("全缺", _none)):
    check("13 %s: 对外报告的特征数 = 候选实际用的项数" % _lab,
          len(_r["features_used"]),
          ({a["n_features"] for a in _r["analogs"]}.pop() if _r["analogs"] else len(_r["features_used"])))
    check("13 %s: 降级后仍能给出结果" % _lab, len(_r["analogs"]) > 0, True)
check("13 全缺时只剩不依赖外部源的特征", sorted(_none["features_used"]),
      sorted([an.FEATURES[k][0] for k in ("btc_rv5", "btc_ret1", "corr20", "pos_ret1", "pos_rv5")]))

# ================================================================ 14. 多空近中性组合的分母
# GPT 问: gross notional 做分母, 市场中性组合 gross 大而净敞口小 -> pos_ret1 会偏小, 算不算问题?
# 这里把行为钉死: 多空对冲掉的部分**本来就不该**产生方向暴露, 分母仍是 gross。
_TWO = ["2026-01-02", "2026-01-05", "2026-01-06"]
_P2 = pf.build_panel(
    {"X": stock([(d, p, p, p, p) for d, p in zip(_TWO, [100.0, 110.0, 121.0])]),
     "Y": stock([(d, p, p, p, p) for d, p in zip(_TWO, [50.0, 55.0, 60.5])])},   # 与 X 同步 +10%
    pd.DataFrame({"ts": [pd.Timestamp(d, tz="UTC") for d in _TWO],
                  "open": 0, "high": 100000, "low": 100000, "close": 100000, "volume": 0}))
# 完全对冲: 多 X 5000 + 空 Y 5000, 两者同步 +10% -> 净盈亏 0, gross = 10000 -> pos_ret1 = 0
_NEUTRAL = pf.Account([pf.Position("X", "long", 5000), pf.Position("Y", "short", 5000)],
                      usdt=5000, maint_margin=0.01)
check("14 完全对冲时 gross = 多空名义之和", pf.gross_notional(_NEUTRAL), 10000.0)
check("14 完全对冲时仓位收益为 0 (不是除零, 不是 NaN)",
      float(an.position_return_series(_NEUTRAL, _P2).iloc[0]), 0.0, 1e-12)
# 同样是 +10% 的行情, 纯多头组合的 pos_ret1 = 10%
_LONG = pf.Account([pf.Position("X", "long", 5000)], usdt=5000, maint_margin=0.01)
check("14 纯多头同一天是 +10%",
      float(an.position_return_series(_LONG, _P2).iloc[0]), 0.10, 1e-12)
check("14 无仓位时返回 NaN 而不是崩掉",
      bool(pd.isna(an.position_return_series(pf.Account([], usdt=1000), _P2).iloc[0])), True)


# ================================================================ 15. 第三轮复核修的几处
# --- 15.1 截止时点必须同时支持夏令时与冬令时 ---
# UTC 次日 00:00 在 EDT 下是纽约 20:00, 在 EST 下是 19:00 —— 不能把它写成固定的 20:00。
# 这里不验证注释, 验证行为: 用注入的 now 检查两类数据各自的「最后一根走完的日线」。
def _cut(ts):
    return an.as_of_cutoff(pd.Timestamp(ts, tz="UTC"))


# 夏令时 (EDT, UTC-4): 纽约 09-18 13:55 还在盘中 -> 美股当天没走完
_c, _u, _r = _cut("2026-09-18 17:55")
check("15.1 EDT 盘中: 美股完整到前一天", str(_u), "2026-09-17")
check("15.1 EDT 盘中: 加密完整到前一天", str(_r), "2026-09-17")
# 夏令时收盘后 (纽约 17:30): 美股当天走完了, 但加密的 UTC 日还没
_c2, _u2, _r2 = _cut("2026-09-18 21:30")
check("15.1 EDT 收盘后: 美股完整到当天", str(_u2), "2026-09-18")
check("15.1 EDT 收盘后: 加密还差一天", str(_r2), "2026-09-17")
check("15.1 共同截止日取两者较早的", str(_c2), "2026-09-17")
# 冬令时 (EST, UTC-5): 同样是 21:30 UTC, 纽约是 16:30 EST, 美股已收盘
_c3, _u3, _r3 = _cut("2026-01-15 21:30")
check("15.1 EST 同一 UTC 时刻: 美股已收盘", str(_u3), "2026-01-15")
check("15.1 EST: 加密仍差一天", str(_r3), "2026-01-14")
# 冬令时 20:30 UTC = 纽约 15:30 EST, 还没收盘 —— 换成夏令时这个时刻已经是 16:30 了
check("15.1 EST 20:30 UTC (纽约 15:30) 美股未收盘", str(_cut("2026-01-15 20:30")[1]), "2026-01-14")
check("15.1 EDT 20:30 UTC (纽约 16:30) 美股已收盘", str(_cut("2026-07-15 20:30")[1]), "2026-07-15")

# --- 15.2 还在形成中的当日日线必须被丢掉 ---
# 面板最后一天 = 合成数据的最后一天; 把 now 设在那一天的盘中, 那一根就不算走完。
_last = feat_dates[-1]
_intraday = pd.Timestamp("%s 17:00" % _last, tz="UTC")          # 纽约 13:00, 盘中
_res_intraday = an.find_analogs(A2, PN, top_n=3, horizons=(1, 3, 5), now=_intraday)
check("15.2 盘中: 查询日不等于面板最后一天",
      _res_intraday["query_date"] != str(_last), True)
check("15.2 盘中: 如实报告丢掉了几根没走完的日线",
      _res_intraday["as_of"]["dropped_incomplete"] > 0, True)
check("15.2 盘中: notes 里说明了只用完整交易日",
      any("只用完整交易日" in n for n in _res_intraday["notes"]), True)
# 把 now 推到很晚 -> 面板里全部日线都走完了, 查询日就是最后一天
_res_late = an.find_analogs(A2, PN, top_n=3, horizons=(1, 3, 5),
                            now=pd.Timestamp("2030-01-01", tz="UTC"))
check("15.2 全部走完后: 查询日就是面板最后一天", _res_late["query_date"], str(_last))
check("15.2 全部走完后: 没有丢任何日线", _res_late["as_of"]["dropped_incomplete"], 0)

# --- 15.3 查询行缺值时: 退回到最近一个凑齐的日子, 并点名缺了什么 ---
# 造一列只到倒数第 4 天的假 VIX -> 最后 3 天缺 VIX
_vix_short = pd.Series([15.0 + (i % 5) for i in range(len(feat_dates) - 3)],
                       index=feat_dates[:-3], dtype=float)
_res_gap = an.find_analogs(A2, PN, vix=_vix_short, top_n=3, horizons=(1, 3, 5),
                           now=pd.Timestamp("2030-01-01", tz="UTC"))
check("15.3 查询日退回到最近一个凑齐的交易日", _res_gap["query_date"], str(feat_dates[-4]))
check("15.3 榜单不是空的", len(_res_gap["analogs"]) > 0, True)
check("15.3 notes 点名了查询日缺的特征",
      any("查询日那天缺了" in n for n in _res_gap["notes"]), True)

# --- 15.4 降级后必须报告「本次实际权重」, 不是配置权重 ---
# 缺 SPY -> mkt 组 (20%) 整组消失 -> 剩下三组重新归一为 30/25/25 ÷ 0.80 = 37.5 / 31.25 / 31.25
_eff = an.effective_weights(["vix", "btc_rv5", "btc_ret1", "corr20", "pos_ret1", "pos_rv5"])
check("15.4 缺 mkt 组后 vol 的实际权重", _eff["市场波动状态"], 0.375, 1e-12)
check("15.4 缺 mkt 组后 coupling 的实际权重", _eff["股币耦合"], 0.3125, 1e-12)
check("15.4 缺 mkt 组后 pos 的实际权重", _eff["股票仓位自身"], 0.3125, 1e-12)
check("15.4 实际权重之和为 1", sum(_eff.values()), 1.0, 1e-12)
check("15.4 全部组都在时实际权重 = 配置权重",
      an.effective_weights(list(an.CORE_KEYS))["股市方向"], 0.20, 1e-12)
_res_nospy = an.find_analogs(A2, PN, vix=_vix_short, spy=None, top_n=3, horizons=(1, 3, 5),
                             now=pd.Timestamp("2030-01-01", tz="UTC"))
check("15.4 结果里同时给出配置权重和实际权重",
      bool(_res_nospy["configured_weights"]) and bool(_res_nospy["effective_weights"]), True)
check("15.4 缺 SPY 时实际权重里没有股市方向组",
      "股市方向" not in _res_nospy["effective_weights"], True)

# --- 15.5 完全对冲的组合: 整个 pos 组和 corr20 都会退化, 必须端到端验证 ---
# 多 X 5000 + 空 Y 5000, 两者每天同步涨跌 -> 仓位收益恒为 0
#   -> pos_ret1 恒 0 (常数列, IQR=0 被丢) / pos_rv5 恒 0 (同理) / corr20 = 常数与 BTC 的相关 = NaN
#   -> 8 项应当只剩 5 项 (不是 6 项)
_YP = [p * 0.5 for p in SP]          # 与 X 同步的价格路径 (比例不同不影响收益率)
_hedged_panel = pf.build_panel(
    {"X": stock([(d, p, p, p, p) for d, p in zip(ny, SP)]),
     "Y": stock([(d, p, p, p, p) for d, p in zip(ny, _YP)])}, bt2)
_HEDGED = pf.Account([pf.Position("X", "long", 5000), pf.Position("Y", "short", 5000)],
                     usdt=5000, btc=0.05, maint_margin=0.01)
_fh = an.build_features(_HEDGED, _hedged_panel)
check("15.5 完全对冲: 仓位日收益恒为 0", float(_fh["pos_ret1"].abs().max()), 0.0, 1e-12)
_res_h = an.find_analogs(_HEDGED, _hedged_panel, top_n=3, horizons=(1, 3, 5),
                         now=pd.Timestamp("2030-01-01", tz="UTC"))
for _f in ("pos_ret1", "pos_rv5", "corr20"):
    check("15.5 %s 退化后不进 features_used" % _f,
          an.FEATURES[_f][0] not in _res_h["features_used"], True)
check("15.5 实际权重里没有「股票仓位自身」组",
      "股票仓位自身" not in _res_h["effective_weights"], True)
check("15.5 退化后仍能出结果", len(_res_h["analogs"]) > 0, True)
check("15.5 notes 如实列出退化掉的特征",
      any(an.FEATURES["pos_ret1"][0] in n for n in _res_h["notes"]), True)

# --- 15.6 全现货账户没有强平线, 不该算出缓冲百分比 ---
_SPOT = pf.Account([pf.Position("NVDA", "long", 1000, kind="spot")], usdt=0, maint_margin=0.01)
_osp = an.forward_outcomes(_SPOT, P5, pd.Timestamp("2026-01-02").date(), horizons=(1,))
check("15.6 全现货: 不给缓冲百分比", _osp[0]["worst_buffer_pct"], None)
check("15.6 全现货: 也不会被判强平", _osp[0]["liquidated"], False)
check("15.6 有杠杆仓时才给缓冲百分比",
      an.forward_outcomes(A1, P5, pd.Timestamp("2026-01-02").date(),
                          horizons=(1,))[0]["worst_buffer_pct"] is not None, True)

# --- 15.7 「独立事件」这个说法已经撤掉 ---
check("15.7 诊断字段改名, 不再叫 cluster",
      "n_retained_from_raw_top_n" in (res["neighborhood"] or {}), True)
check("15.7 反例文案不再写「独立事件」",
      "独立事件" not in an.ADVERSE_NOTE, True)

# ================================================================ 16. 第四轮复核修的三处
# --- 16.1 「为什么像」不能写「今天」: 查询日可能比今天早一到两天 ---
_r16 = an.find_analogs(A2, PN, top_n=3, horizons=(1, 3, 5),
                       now=pd.Timestamp("2030-01-01", tz="UTC"))
_reason = _r16["analogs"][0]["reasons"][0]
check("16.1 理由里不出现「今天」", "今天" not in _reason, True)
check("16.1 理由里带上真实查询日", _r16["query_date"] in _reason, True)
check("16.1 最不像那条也不出现「今天」", "今天" not in _r16["analogs"][0]["unlike"], True)

# --- 16.2 日历边界不能冒充美股交易日 ---
# 周日运行时, 按时间判断的「日历边界」会落在周末, 但那天根本没有 bar。
# 必须把「日历边界」和「最后一个真实完整交易日」分成两个字段, 界面只能显示后者。
_sun = pd.Timestamp("2026-09-20 18:00", tz="UTC")       # 周日
_c_sun, _u_sun, _r_sun = an.as_of_cutoff(_sun)
check("16.2 周日的日历边界确实落在周末 (所以不能直接显示)",
      pd.Timestamp(_u_sun).strftime("%A") in ("Saturday", "Sunday"), True)
_r_wk = an.find_analogs(A2, PN, top_n=3, horizons=(1, 3, 5), now=_sun)
_ao = _r_wk["as_of"]
check("16.2 结果里同时给出日历边界和真实交易日",
      all(k in _ao for k in ("calendar_cutoff", "us_calendar_limit", "us_last_complete_bar")), True)
check("16.2 真实交易日来自实际数据, 不等于周末的日历边界",
      _ao["us_last_complete_bar"] != str(_u_sun), True)
check("16.2 真实交易日确实是面板里存在的一天",
      _ao["us_last_complete_bar"] in [str(d) for d in feat_dates], True)
check("16.2 各榜的查询日也单独给出",
      all(k in _ao for k in ("core_query_date", "dvol_query_date")), True)
# last_bar_on_or_before 本身: 周末边界必须回退到之前真实存在的日期
check("16.2 last_bar_on_or_before 会回退到真实日期",
      str(an.last_bar_on_or_before(feat_dates, pd.Timestamp("2030-01-01").date())), str(feat_dates[-1]))
check("16.2 边界早于全部数据时返回 None",
      an.last_bar_on_or_before(feat_dates, pd.Timestamp("2000-01-01").date()), None)

# --- 16.3 收盘后要留发布缓冲 ---
# 16:00 ET 整不能立刻当成「数据已就绪」: 供应商还要几分钟发布/稳定最后一根 bar。
check("16.3 16:05 ET 还不算就绪 (EDT: 20:05 UTC)",
      str(an.last_complete_us_date(pd.Timestamp("2026-07-15 20:05", tz="UTC"))), "2026-07-14")
check("16.3 16:20 ET 才算就绪 (EDT: 20:20 UTC)",
      str(an.last_complete_us_date(pd.Timestamp("2026-07-15 20:20", tz="UTC"))), "2026-07-15")
check("16.3 冬令时同理 (EST: 21:05 / 21:20 UTC)",
      [str(an.last_complete_us_date(pd.Timestamp("2026-01-15 %s" % t, tz="UTC")))
       for t in ("21:05", "21:20")], ["2026-01-14", "2026-01-15"])

# --- 16.4 邻域结论不能写成确定性判断 ---
_txt = ui.analog_neighborhood(_r16["neighborhood"])
check("16.4 不再出现「并不比随便挑一天更危险」", "并不比随便挑一天更危险" not in _txt, True)
check("16.4 不再用「随便挑一天」指代基准", "随便挑一天" not in _txt, True)
check("16.4 写明是描述性对照", "描述性对照" in _txt, True)
check("16.4 写明不能据此判断", "不能据此判断" in _txt or "不构成显著性结论" in _txt, True)
check("16.4 写明换个查询日结果可能不同", "结果都可能不同" in _txt, True)
check("16.4 基准称呼改成「同一合格历史日期池」", "同一合格历史日期池" in _txt, True)

print("")
print("%s  (%d 处断言)" % ("全部通过" if not fails else "%d 处失败" % fails, checks))
sys.exit(1 if fails else 0)
