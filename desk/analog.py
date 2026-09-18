"""
历史相似情景检索 (Historical Analog Finder)。

子题「决策压力测试」的原文第一句是:
  "Before opening a position, how does AI retrieve historically similar scenarios?"

引擎原本只回答「历史上最差的日子」, 那不等于「历史上最像当前状态的日子」——
最差的那天可能发生在波动率、股币相关性、抵押品结构都完全不同的环境里。
这个模块回答的是: **以查询日的市场状态和你这个组合的结构来看, 过去五年哪些交易日最像?
那些日子之后的 1 / 3 / 5 个交易日, 这个组合发生了什么?**

信息截止时点 (2026-09-19 逐条审计 Yahoo / Deribit 原始时间戳 + 实测盘中行为后写定):
  **只用已经走完的日线。** 一根日线什么时候算走完:
    美股类 (VIX / SPY / 正股)  该纽约交易日 16:00 ET 收盘之后
    加密类 (BTC / ETH / DVOL)  该 UTC 日的次日 00:00 UTC 之后
  查询日 = 两类都已走完的**最新共同日期**。盘中打开页面时, 今天那根还在形成的日线会被丢掉 ——
  否则「今天」是半天、历史日期全是整天, 两者的收益和波动根本不可比
  (2026-09-18 周五 13:55 ET 实测: Yahoo 的 SPY 当日 bar 的 close 是现价不是收盘价,
   BTC 的 UTC 日也还没走完; Deribit 的 86400 resolution 同样会返回正在形成的当日 candle)。

  ⚠️ 不要把「UTC 次日 00:00」写成固定的纽约 20:00: 那只在夏令时 EDT 成立,
  冬令时 EST 下等于纽约 19:00 (实测 2026-01-15 -> 19:00 EST / 2026-07-15 -> 20:00 EDT)。
  时区换算一律交给 tz_convert, 不手写小时数。
  ⚠️ 已知近似: 同一个日期标签下, 加密的 UTC 日终比美股 16:00 ET 收盘晚 3~5 小时 (随夏令时变),
  两边截止点不同步。要消掉它得用 16:00 ET 截断的加密小时线, 本版本没做, 如实披露。

四条硬规则:
1. **不预测**。相似度只用来找反例和压力证据, 不代表未来, 也不代表这些情景等概率发生。
2. **不许用未来数据排序**。每个历史日期的特征只能由该日及之前的数据算出
   (tests/test_analog.py 的 no_lookahead: 把面板截断到 t 重算, 必须与全量面板的 t 行完全相同)。
   后续 1/3/5 日的结果只在排完序之后计算, 只用于展示。
3. **同一张排行榜里每个候选日必须用完全相同的特征集合**。用 7 个特征算出来的距离和用 8 个算出来的
   不在同一个度量空间里, 把它们排在一起比较是假可比。所以分成两张榜 (见下)。
4. **大模型不参与**。特征、缩放、距离、排序、后续收益、强平判定全部由这里的代码算。

两张排行榜:
  **五年核心版** (默认)   不含 DVOL, 覆盖完整的五年; 每个候选日都具备全部核心特征。
  **近期 DVOL 增强版**    只在 DVOL 有数据的日子里检索 (Deribit 历史上限约 1000 天)。
  两张榜的距离**不可互相比较**, 界面上分开显示, 不混排。

相似度里为什么**不含加密抵押品**:
  组合特征只算「今天的股票 / rToken 仓位」在历史各日的收益, 不含 BTC/ETH 抵押品盈亏。
  否则 BTC 的风险会被机械性地重复表达四次 (BTC 自身收益、含 BTC 的账户收益、含 BTC 的账户波动、
  含 BTC 的账户与 BTC 的相关性), 而「组合与 BTC 的相关性」这一项会有一部分只是账户公式自带的,
  不是真实的市场耦合。抵押品继续参与后续路径重演、强平判定、安全分和调整方案 —— 那才是它该起作用的地方。

权重说明 (产品设定, 不是经验最优, 所以放在 GROUP_WEIGHTS 里可改):
  原始方案里还有一组「组合结构特征」(有效杠杆 / 抵押品占比 / 集中度) 占 20%。
  那组量对每个历史日期都是同一个值 —— 距离公式里 |x(今天) - x(历史日)| 恒等于 0,
  完全不参与排序。所以只保留真正随日期变化的四组。组合结构改为「展示上下文」:
  它决定同样的价格变动会不会打爆你, 由后续 1/3/5 日的重演结果体现, 而不是塞进相似度里凑权重。
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from . import portfolio as pf
from .provenance import CallLog, Provenance
from .radar import DERIBIT, _get
from .sources import yahoo

# ---------------------------------------------------------------- 配置 (不 hard-code 进算法)

# 取整数百分比, 不用 37.5 / 31.25 这种小数 —— 它们是重新归一的副产物, 不是估出来的精度。
GROUP_WEIGHTS = {"vol": 0.30, "mkt": 0.20, "coupling": 0.25, "pos": 0.25}

GROUP_LABELS = {"vol": "市场波动状态", "mkt": "股市方向", "coupling": "股币耦合", "pos": "股票仓位自身"}

# key -> (界面标签, 所属组, 显示格式)
FEATURES: dict[str, tuple[str, str, str]] = {
    "vix":      ("VIX",                      "vol",      "%.1f"),
    "vix_pct":  ("VIX 一年内百分位",          "vol",      "%.0f%%"),
    "btc_rv5":  ("BTC 5 日实现波动",          "vol",      "%.2f%%"),
    "dvol_btc": ("BTC DVOL",                 "vol",      "%.1f"),
    "spy_ret5": ("美股 5 日变动 (SPY)",       "mkt",      "%.2f%%"),
    "btc_ret1": ("BTC 单日变动",              "coupling", "%.2f%%"),
    "corr20":   ("股票仓位与 BTC 20 日相关性", "coupling", "%.2f"),
    "pos_ret1": ("股票仓位单日变动",           "pos",      "%.2f%%"),
    "pos_rv5":  ("股票仓位 5 日实现波动",      "pos",      "%.2f%%"),
}
PCT_FEATURES = {"btc_rv5", "btc_ret1", "pos_ret1", "pos_rv5", "spy_ret5"}   # 展示时乘 100

# 五年核心版用的特征: 除 DVOL 外的全部 (DVOL 的历史只有约 1000 天 < 5 年)
CORE_KEYS = [k for k in FEATURES if k != "dvol_btc"]
DVOL_KEYS = list(FEATURES)

TOP_N = 5                # 每张榜返回几个相似日
MIN_GAP = 10             # 去重窗口 (交易日): 避免返回同一次危机的连续五天
EXCLUDE_RECENT = 10      # 排除最近 N 个交易日:「昨天很像今天」是废话, 不是证据
HORIZONS = (1, 3, 5)     # 后续观察期 (交易日)
NEIGHBORHOOD = 30        # 「相似邻域」取多少个最相似的日期 (经时间间隔过滤后)
NB_MIN_GAP = 5           # 邻域去重窗口 (比主榜的 10 小: 邻域要样本量, 主榜要代表性)
ADVERSE_N = 3            # 邻域里挑几个最差的当压力反例
RV_WINDOW = 5            # 实现波动窗口
CORR_WINDOW = 20         # 滚动相关性窗口
MKT_WINDOW = 5           # 美股方向窗口
PCT_WINDOW = 252         # 百分位回看窗口 (约一年); 只回看, 不前看
PCT_MIN = 252            # 「一年内百分位」就必须满 252 个观测才给值 ——
                         # 只要 60 个就出数的话, 这个名字是假的 (2026-09-19 复核指出)。
                         # VIX / SPY 因此取 10 年历史, 让预热期落在 5 年面板开始之前。

SPY = "SPY"
NY = ZoneInfo("America/New_York")
US_CLOSE = (16, 0)       # 美股 16:00 ET 收盘; 不写死 UTC 小时数 (夏令时会变)
# 收盘时间 != 数据可用时间: 供应商发布和稳定最后一根 bar 要几分钟, 刚收盘那一瞬读到的
# 可能还在刷新。留 15 分钟缓冲, 宁可晚一点也不要读到半成品 (第四轮复核建议)。
US_DATA_READY = (16, 15)

DISCLAIMER = ("历史相似性只用于检索案例与反例, 不构成概率预测。"
              "距离仅用于当前特征与权重配置下的相对排序, 换一套配置排序就会变。")
ADVERSE_NOTE = ("下面这几个案例是在最相似的 %d 个历史日期里, **按事后 5 日损失挑出来的压力反例** —— "
                "它们不是最相似的日子, 也不是最可能发生的结果。")


# ---------------------------------------------------------------- 外部数据 (各自留痕, 失败即降级)

def _daily_from_yahoo(df: pd.DataFrame, tz: str) -> pd.Series:
    """
    ⚠️ **只能用在美股类标的 (VIX / SPY), 不能用在 BTC-USD。**

    2026-09-19 逐条审计 Yahoo 原始时间戳的结果:
        VIX  2026-09-18 07:00 UTC -> UTC 日期 09-18 -> 纽约 09-18   一致
        SPY  2026-09-18 13:30 UTC -> UTC 日期 09-18 -> 纽约 09-18   一致
        BTC  2026-09-18 00:00 UTC -> UTC 日期 09-18 -> 纽约 09-17   **整体偏移一天**

    加密日线的时间戳是 00:00 UTC, 转成纽约时区会掉到前一个自然日。所以 BTC/ETH 必须留在 UTC
    (由 portfolio._btc_daily 处理), 美股类才转纽约。tests/test_analog.py 有回归测试钉住这条。
    """
    d = df.copy()
    d["date"] = d["ts"].dt.tz_convert(tz).dt.date
    return d.drop_duplicates("date", keep="last").set_index("date")["close"].astype(float).sort_index()


def vix_series(range_: str = "10y", log: CallLog | None = None) -> tuple[pd.Series | None, Provenance]:
    """VIX 日线 (Cboe 官方指数, 经 Yahoo), 索引是纽约交易日。默认取 10 年: 见 PCT_MIN。"""
    df, prov = yahoo.ohlcv("^VIX", range_, "1d")
    if log is not None:
        log.append(prov)
    if df is None or df.empty:
        return None, prov
    return _daily_from_yahoo(df, "America/New_York"), prov


def spy_series(range_: str = "10y", log: CallLog | None = None) -> tuple[pd.Series | None, Provenance]:
    """
    SPY 日线, 用来表达「美股整体方向」。
    为什么要它: VIX 只说市场有多紧张, 用户组合的收益又取决于他自己的多空结构 ——
    两者都代表不了大盘往哪边走。没有这一项时, 一个满仓做空的组合和满仓做多的组合
    会把同一个大盘环境读成两种状态。
    """
    df, prov = yahoo.ohlcv(SPY, range_, "1d")
    if log is not None:
        log.append(prov)
    if df is None or df.empty:
        return None, prov
    return _daily_from_yahoo(df, "America/New_York"), prov


def dvol_series(currency: str = "BTC", days: int = 1825,
                log: CallLog | None = None) -> tuple[pd.Series | None, Provenance]:
    """
    Deribit 官方波动率指数 DVOL 日线, 索引是 UTC 日期。
    边界: Deribit 实际返回的历史有上限 (实测约 1000 天 < 5 年)。覆盖不到的日期这一项会缺失 ——
    所以它只进「近期 DVOL 增强版」那张榜, 不混进五年核心版。不补值、不外推。
    """
    now = int(time.time() * 1000)
    params = {"currency": currency, "start_timestamp": now - days * 86400000,
              "end_timestamp": now, "resolution": "86400"}
    data, prov = _get("deribit", DERIBIT + "/get_volatility_index_data", params,
                      lambda j: j["result"]["data"])
    if log is not None:
        log.append(prov)
    if not data:
        return None, prov
    idx = [pd.Timestamp(int(row[0]), unit="ms", tz="UTC").date() for row in data]
    s = pd.Series([float(row[4]) for row in data], index=idx).sort_index()
    return s[~s.index.duplicated(keep="last")], prov


def fetch_context(log: CallLog | None = None):
    """一次取齐相似度要用的外部序列。返回 (vix, spy, dvol, [来源记录])。任何一个失败都只是少一项。"""
    # 取 10 年而不是 5 年: 百分位要满 252 个观测才给值, 拉长历史能让预热期
    # 落在 5 年主面板开始之前, 否则面板头一年会整段缺这一项。
    vix, p1 = vix_series(log=log)
    spy, p2 = spy_series(log=log)
    dv, p3 = dvol_series("BTC", log=log)
    return vix, spy, dv, [p1, p2, p3]


# ---------------------------------------------------------------- 哪些日线已经走完

def now_utc() -> pd.Timestamp:
    return pd.Timestamp.now("UTC")


def last_complete_us_date(now: pd.Timestamp | None = None):
    """
    **日历边界**: 按时间判断, 最多允许用到哪个日历日期。
    ⚠️ 这不是「最后一个美股交易日」—— 周日运行时它会返回周六/周日, 而那天根本没有 bar。
    真正的交易日要拿这个边界去实际数据里取 (见 last_bar_on_or_before), 界面不能把它
    当成行情日期显示 (2026-09-19 第四轮复核指出)。
    用 tz_convert 换算, 不手写 UTC 小时 (夏令时会变)。
    """
    n = (now or now_utc()).tz_convert(NY)
    return n.date() if (n.hour, n.minute) >= US_DATA_READY else n.date() - timedelta(days=1)


def last_complete_utc_date(now: pd.Timestamp | None = None):
    """
    **日历边界**: 加密的 UTC 日 D 要到 D+1 日 00:00 UTC 之后才算走完 -> 最后一个完整的是「昨天」。
    同样只是边界, 不代表那天一定有数据。
    """
    return ((now or now_utc()).tz_convert("UTC") - timedelta(days=1)).date()


def last_bar_on_or_before(index, limit):
    """在实际数据的日期里取 <= limit 的最后一个 —— 这才是真正的「最后一根完整日线」。"""
    ok = [d for d in index if d <= limit]
    return max(ok) if ok else None


def as_of_cutoff(now: pd.Timestamp | None = None):
    """
    两类数据都已走完的最新**日历**边界。返回 (共同边界, 美股边界, 加密边界)。
    盘中运行时这个值通常比「今天」早一到两天 —— 这是刻意的: 宁可用昨天的整天,
    也不拿今天的半天去和历史上的整天比。界面必须把这个日期显示出来。
    """
    n = now or now_utc()
    us, cr = last_complete_us_date(n), last_complete_utc_date(n)
    return min(us, cr), us, cr


# ---------------------------------------------------------------- 特征 (只用当日及之前的数据)

def position_return_series(a: pf.Account, panel: pf.Panel) -> pd.Series:
    """
    **只算股票 / rToken 仓位**在各历史日的收益 (按今天的开仓权益折算), 不含 BTC/ETH 抵押品盈亏。

    为什么要把抵押品摘出去: 见模块开头。相似度要问的是「股票仓位和 BTC 这两类风险当时怎么互动」,
    而不是「我的账户公式里装了多少 BTC」。抵押品在 forward_outcomes / 强平判定 / 安全分里全额参与。

    分母用**名义总额**而不是账户权益: 除以权益的话, 同一套股票仓位在「有大量 BTC 抵押品」和
    「没有抵押品」两个账户下会算出不同的数, 那这一项就又沾上抵押品了, 和标签说的对不上。
    除以名义总额得到的是这套仓位本身的加权收益, 换个账户完全一样 (tests/test_analog.py 守着)。
    杠杆高低不影响这一项 —— 杠杆决定的是后果, 后果由 forward_outcomes 和安全分表达。
    """
    frame = panel.close_ret
    gross = pf.gross_notional(a)
    # 用 Series 起头, 不能用 sum(generator): 没有仓位时会退化成 int 0
    pnl = pd.Series(0.0, index=frame.index)
    for p in a.positions:
        pnl = pnl + p.sign * p.notional * frame[p.symbol]
    if gross <= 0:
        return pd.Series(float("nan"), index=frame.index)
    return pnl / gross


def trailing_pct_rank(s: pd.Series, window: int = PCT_WINDOW, min_periods: int = PCT_MIN) -> pd.Series:
    """
    每个点在它**之前** window 个点里的百分位 (含自身)。
    不用全样本百分位: 那会让 2021 年的某一天知道 2025 年的行情, 是典型的前视偏差。
    """
    return s.rolling(window, min_periods=min_periods).apply(
        lambda w: 100.0 * (w <= w[-1]).sum() / len(w), raw=True)


def build_features(a: pf.Account, panel: pf.Panel, vix: pd.Series | None = None,
                   spy: pd.Series | None = None, dvol: pd.Series | None = None) -> pd.DataFrame:
    """按面板交易日产出特征表。每一行只由该日及之前的数据算出 (有 no_lookahead 测试守着)。"""
    idx = panel.close_ret.index
    pos = position_return_series(a, panel)
    btc = (panel.close_ret[pf.BTC] if pf.BTC in panel.close_ret.columns
           else pd.Series(float("nan"), index=idx))
    out = pd.DataFrame(index=idx)
    out["btc_ret1"] = btc
    out["pos_ret1"] = pos
    out["btc_rv5"] = btc.rolling(RV_WINDOW).std()
    out["pos_rv5"] = pos.rolling(RV_WINDOW).std()
    out["corr20"] = pos.rolling(CORR_WINDOW).corr(btc)
    if vix is not None and len(vix):
        out["vix"] = vix.reindex(idx)
        # 百分位在完整的 VIX 序列上算, 再对齐到面板 —— 对齐产生的空洞不该污染回看窗口
        out["vix_pct"] = trailing_pct_rank(vix).reindex(idx)
    if spy is not None and len(spy):
        out["spy_ret5"] = (spy / spy.shift(MKT_WINDOW) - 1).reindex(idx)
    if dvol is not None and len(dvol):
        out["dvol_btc"] = dvol.reindex(idx)
    return out[[k for k in FEATURES if k in out.columns]]


def _f(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if v != v else v


def robust_scale(feats: pd.DataFrame):
    """
    中位数 / IQR 缩放: 对厚尾和离群日比 z-score 稳健。IQR 为 0 的常数列直接丢掉。

    关于前视: scaler 用**截至查询时点已经可得的全部历史**拟合。本工具只对「今天」发问,
    而今天就是样本末端, 所以「全样本」就等于「截至查询日的扩张窗口」, 没有用到查询日之后的数据。
    ⚠️ 不要把理由说成「缩放只是归一化常数、不影响排序」—— 那是错的: median/IQR 会改变各特征的
    相对尺度, 从而改变排序。真正的理由是上面那句「查询日就是样本末端」。
    如果以后要支持「回到历史某天以当时视角检索」, 必须把数据截断到那天再重新拟合 scaler,
    并且 query 与全部候选日共用同一个 scaler。
    """
    med, q1, q3 = feats.median(), feats.quantile(0.25), feats.quantile(0.75)
    iqr = (q3 - q1).replace(0.0, float("nan"))
    scaled = (feats - med) / iqr
    scaler = {k: {"median": _f(med.get(k)), "iqr": _f(iqr.get(k))} for k in feats.columns}
    return scaled.dropna(axis=1, how="all"), scaler


# ---------------------------------------------------------------- 距离与排序 (纯函数, 拿不到未来数据)

def row_distance(query: pd.Series, row: pd.Series, weights: dict[str, float] | None = None,
                 keys: list[str] | None = None):
    """
    加权曼哈顿距离 (不用欧氏: 样本只有约 1250 天、特征厚尾, 平方会让单个离群差异主导排序,
    而且曼哈顿更容易手工复算)。

    只在两边都有值的特征上计算; 组权重按该组实际可用的特征数平分, 再在可用的组之间重新归一。
    ⚠️ 这个归一只解决「少一个特征天然更近」这一种错误, **解决不了跨度量空间的可比性** ——
    所以调用方 (rank_tier) 会要求同一张榜里每个候选日都具备完全相同的特征集合。
    返回 (距离, 有效特征数, {特征: {gap: 缩放后的绝对差, contrib: 该特征对距离的实际贡献}})。
    """
    gw = weights or GROUP_WEIGHTS
    want = keys if keys is not None else list(FEATURES)
    avail = [k for k in want if k in row.index and pd.notna(row[k]) and pd.notna(query.get(k))]
    if not avail:
        return float("nan"), 0, {}
    by_group: dict[str, list[str]] = {}
    for k in avail:
        by_group.setdefault(FEATURES[k][1], []).append(k)
    total_w = sum(gw.get(g, 0.0) for g in by_group)
    if total_w <= 0:
        return float("nan"), 0, {}
    dist = 0.0
    detail = {}
    for g, ks in by_group.items():
        w = gw.get(g, 0.0) / total_w / len(ks)
        for k in ks:
            gap = abs(float(row[k]) - float(query[k]))
            detail[k] = {"gap": gap, "contrib": w * gap}
            dist += w * gap
    return dist, len(avail), detail


def rank_candidates(scaled: pd.DataFrame, query: pd.Series, candidates: list,
                    weights: dict[str, float] | None = None,
                    keys: list[str] | None = None, require_complete: bool = False) -> list[dict]:
    """
    对候选日期按距离升序排序 (并列按日期升序, 保证可复现)。
    require_complete=True 时, 缺任何一个 keys 里的特征的日期直接不进这张榜 —— 这是「同一张榜里
    距离可比」的前提。

    这个函数只看 keys 声明过的列 —— 表里多出来的任何列 (包括未来收益) 都不会影响排序。
    tests/test_analog.py 的 ignores_extra_columns 守着这条。
    """
    want = keys if keys is not None else list(FEATURES)
    rows = []
    for d in candidates:
        if d not in scaled.index:
            continue
        row = scaled.loc[d]
        if require_complete and any(k not in row.index or pd.isna(row[k]) for k in want):
            continue
        dist, n, detail = row_distance(query, row, weights, want)
        if dist != dist:                      # NaN: 没有任何共同可用特征
            continue
        rows.append({"date": d, "distance": dist, "n_features": n, "detail": detail})
    return sorted(rows, key=lambda r: (r["distance"], str(r["date"])))


def dedup(ranked: list[dict], positions: dict, top_n: int = TOP_N, min_gap: int = MIN_GAP) -> list[dict]:
    """贪心去重: 与已选日期相隔不足 min_gap 个交易日的直接跳过, 免得五个结果是同一次危机的连续五天。"""
    out: list[dict] = []
    for r in ranked:
        i = positions[r["date"]]
        if all(abs(i - positions[k["date"]]) >= min_gap for k in out):
            out.append(r)
        if len(out) >= top_n:
            break
    return out


# ---------------------------------------------------------------- 后续结果 (排序之后才算, 只用于展示)

def forward_outcomes(a: pf.Account, panel: pf.Panel, start_date, horizons=HORIZONS) -> list[dict]:
    """
    从 start_date 收盘起持有 1..h 个交易日 (不调仓) 的结果。抵押品在这里全额参与。
    与 portfolio.multi_day_worst 同一套口径: 收盘价水平表; **强平看路径上任意一天**。
    """
    lv = panel.close_level
    if start_date not in lv.index:
        return []
    i = lv.index.get_loc(start_date)
    e0 = pf.equity0(a, panel.btc_price, panel.eth_price)
    if e0 <= 0:
        return []
    # 开仓时离强平的距离。全现货账户没有维持保证金、也就没有强平线 ——
    # 这种账户不该算出「缓冲剩 X%」, 否则等于暗示存在强平风险 (第三轮复核指出)。
    has_margin = any(p.kind != "spot" for p in a.positions)
    buf0 = (e0 - pf.maint0(a)) if has_margin else 0.0
    out = []
    for h in horizons:
        if i + h >= len(lv.index):
            out.append({"days": h, "ret_pct": None, "worst_pct": None, "worst_buffer": None,
                        "liquidated": None, "note": "样本末尾, 不够 %d 个交易日" % h})
            continue
        path = []
        for j in range(1, h + 1):
            rel = lv.iloc[i + j] / lv.iloc[i] - 1
            pnl = sum((p.sign * p.notional * float(rel[p.symbol]) for p in a.positions), 0.0)
            maint = sum((a.maint_margin * p.notional * (1 + float(rel[p.symbol]))
                         for p in a.positions if p.kind != "spot"), 0.0)
            coll = a.btc * panel.btc_price * a.btc_haircut * float(rel.get(pf.BTC, 0.0))
            if a.eth > 0 and pf.ETH in rel.index:
                coll += a.eth * panel.eth_price * a.eth_haircut * float(rel[pf.ETH])
            path.append((e0 + pnl + coll, maint))
        end_eq = path[-1][0]
        worst_eq = min(eq for eq, _m in path)
        # 强平看「路径上任意一天」, 不是只看权益最低那天 —— 空头上涨会同时压低权益并抬高维持保证金,
        # 最早触线的那天不一定是权益最低的那天 (与 portfolio.multi_day_worst 同一口径)。
        breach = [j for j, (eq, m) in enumerate(path, 1) if eq <= m]
        worst_buffer = min(eq - m for eq, m in path)
        out.append({"days": h,
                    "ret_pct": (end_eq - e0) / e0,
                    "worst_pct": (worst_eq - e0) / e0,
                    # 绝对金额不同账户之间没法比, 所以同时给出「占开仓时那段距离的百分之几」——
                    # 这才是本工具的核心口径 (离强平还剩多远), 界面用百分比不用 USDT。
                    "worst_buffer": worst_buffer,
                    "worst_buffer_pct": (worst_buffer / buf0) if buf0 > 0 else None,
                    "liquidated": bool(breach),
                    # 第几天首次触线。一旦触线, 后面几天的收益只是「假设没被平掉还继续持有」的
                    # 理论值 —— 真实账户那时已经被强平, 界面必须写明, 不能当成实际结果。
                    "liq_day": breach[0] if breach else None,
                    "counterfactual": bool(breach) and breach[0] < h})
    return out


def forward_return_series(a: pf.Account, panel: pf.Panel, h: int = 5) -> pd.Series:
    """每个起点往后 h 个交易日的账户收益率 (向量化, 给邻域统计用)。抵押品参与。"""
    lv = panel.close_level
    e0 = pf.equity0(a, panel.btc_price, panel.eth_price)
    if e0 <= 0:
        return pd.Series(float("nan"), index=lv.index)
    rel = lv.shift(-h) / lv - 1
    pnl = pd.Series(0.0, index=lv.index)
    for p in a.positions:
        pnl = pnl + p.sign * p.notional * rel[p.symbol]
    coll = a.btc * panel.btc_price * a.btc_haircut * rel[pf.BTC] if pf.BTC in rel.columns else 0.0
    if a.eth > 0 and pf.ETH in rel.columns:
        coll = coll + a.eth * panel.eth_price * a.eth_haircut * rel[pf.ETH]
    return (pnl + coll) / e0


# ---------------------------------------------------------------- 顶层

def fmt_feature(key: str, value) -> str:
    if value is None or value != value:
        return "—"
    _, _, spec = FEATURES[key]
    v = float(value) * 100 if key in PCT_FEATURES else float(value)
    return spec % v


@dataclass
class Analog:
    date: str
    distance: float
    n_features: int
    reasons: list[str]                             # 最像的两项
    unlike: str = ""                               # 最不像的一项 (只说像不说不像是选择性呈现)
    state: dict = field(default_factory=dict)      # 当日各特征的原始值 (已格式化)
    outcomes: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _describe(raw: pd.DataFrame, query_date, d, key: str) -> str:
    """
    一条「为什么像」。**不能写「今天」** —— 查询日按完整性规则可能比今天早一到两天
    (周一盘中时通常是上周五), 写「今天 VIX 15.4」会让人以为用的是实时状态
    (2026-09-19 第四轮复核指出)。所以标签直接带上查询日期。
    """
    return "%s %s (查询日 %s: %s)" % (FEATURES[key][0], fmt_feature(key, raw.loc[d, key]),
                                      query_date, fmt_feature(key, raw.loc[query_date, key]))


def _build_analog(a, panel, raw, today, r, used_keys, horizons) -> dict:
    d = r["date"]
    # 按「对距离的实际贡献」排, 不按裸差值 —— 权重小的特征差得多也贡献不大, 说它「最像」是误导
    order = sorted(r["detail"].items(), key=lambda kv: kv[1]["contrib"])
    return Analog(date=str(d), distance=round(r["distance"], 3), n_features=r["n_features"],
                  reasons=[_describe(raw, today, d, k) for k, _ in order[:2]],
                  unlike=_describe(raw, today, d, order[-1][0]) if len(order) > 2 else "",
                  state={FEATURES[k][0]: fmt_feature(k, raw.loc[d, k]) for k in used_keys},
                  outcomes=forward_outcomes(a, panel, d, horizons)).to_dict()


def effective_weights(keys: list[str], weights: dict[str, float] | None = None) -> dict:
    """
    降级之后**真正用在距离里**的组权重。缺一个组时 row_distance 会在剩下的组之间重新归一,
    所以对外只报配置权重是错的: 缺 SPY 时 mkt 组整个消失, 实际权重是 37.5/31.25/31.25
    而不是配置里的 30/20/25/25 (2026-09-19 第三轮复核指出)。
    """
    gw = weights or GROUP_WEIGHTS
    groups: dict[str, list[str]] = {}
    for k in keys:
        groups.setdefault(FEATURES[k][1], []).append(k)
    total = sum(gw.get(g, 0.0) for g in groups)
    if total <= 0:
        return {}
    return {GROUP_LABELS.get(g, g): gw.get(g, 0.0) / total for g in groups}


def _pick_query_date(scaled: pd.DataFrame, keys: list[str], cutoff):
    """
    这张榜的查询日 = 截止日之前、**这张榜要用的特征全部有值**的最新那一天。

    不能直接拿面板最后一天: 那一行可能缺某个特征 (比如 DVOL 当天还没出),
    旧写法会因此返回一张空榜, 而 notes 只检查「整列在不在」, 于是既给不出结果也说不出原因
    (2026-09-19 第三轮复核指出)。
    返回 (查询日 | None, 最后一天缺了哪些特征)。
    """
    within = scaled.index[[d <= cutoff for d in scaled.index]]
    if not len(within) or not keys:
        return None, []
    sub = scaled.loc[within, keys]
    ok = sub.index[sub.notna().all(axis=1)]
    last = within[-1]
    missing_on_last = [FEATURES[k][0] for k in keys if pd.isna(scaled.loc[last, k])]
    return (ok[-1] if len(ok) else None), missing_on_last


def _rank_tier(a, panel, raw, scaled, positions, keys, weights, top_n, min_gap,
               exclude_recent, horizons, cutoff, max_h) -> dict:
    """一张排行榜。keys 里任何一项缺失的候选日都不进这张榜, 保证同榜内距离可比。"""
    have = [k for k in keys if k in scaled.columns]
    empty = {"analogs": [], "n_candidates": 0, "features_used": [], "query_date": "",
             "date_range": "", "effective_weights": {}, "ranked": []}
    if not have:
        return {**empty, "note": "这张榜要用的特征一个都没取到"}
    today, missing_on_last = _pick_query_date(scaled, have, cutoff)
    if today is None:
        return {**empty, "features_used": [FEATURES[k][0] for k in have],
                "note": "截止 %s 之前找不到一个把这 %d 项特征凑齐的交易日, 这张榜暂不可用%s"
                        % (cutoff, len(have),
                           " (最后一天缺: %s)" % ", ".join(missing_on_last) if missing_on_last else "")}
    dates = list(scaled.index)
    ti = dates.index(today)
    lv_pos = {d: i for i, d in enumerate(panel.close_level.index)}
    # 候选必须早于查询日, 排除最近 exclude_recent 天, 且后面还够 max_h 天算结果
    candidates = [d for i, d in enumerate(dates)
                  if i <= ti - exclude_recent
                  and lv_pos.get(d, 10 ** 9) + max_h < len(panel.close_level.index)]
    ranked = rank_candidates(scaled, scaled.loc[today], candidates, weights, have, require_complete=True)
    picked = dedup(ranked, positions, top_n, min_gap)
    return {"analogs": [_build_analog(a, panel, raw, today, r, have, horizons) for r in picked],
            "n_candidates": len(ranked),
            "features_used": [FEATURES[k][0] for k in have],
            "query_date": str(today),
            # ranked 按距离排序, 取首尾会得到「最像的那天 ~ 最不像的那天」, 不是时间范围
            "date_range": ("%s ~ %s" % (min(r["date"] for r in ranked), max(r["date"] for r in ranked))
                           if ranked else ""),
            "effective_weights": effective_weights(have, weights),
            "query_missing": missing_on_last,
            "note": "", "ranked": ranked}


def _neighborhood(a, panel, ranked, fwd5, positions, n: int = NEIGHBORHOOD,
                  adverse_n: int = ADVERSE_N, min_gap: int = NB_MIN_GAP,
                  horizons=HORIZONS, raw=None, today=None) -> dict:
    """
    相似邻域的统计 + 压力反例。

    三条口径, 每一条都影响下面那几个百分比能不能当结论用:
    1. **邻域按时间间隔过滤** (相隔不足 min_gap 个交易日的只留最像的一个)。不过滤的话,
       一次崩盘前后连着的五六天会各算一票, 同一段行情被重复计票。
       ⚠️ 过滤只保证「日期不挨着」, **证明不了统计独立** —— 一次持续数周的危机里,
       相隔 5 个交易日以上的两天仍可能属于同一件事。所以文案只说「经时间间隔过滤的日期」,
       不说「独立事件」(2026-09-19 第三轮复核指出)。
    2. **基准必须来自同一个候选宇宙**。拿「全部有 5 日收益的日子」当基准, 里面混进了
       被排除的预热期和最近 N 天, 和邻域不是同一批样本, 比出来的差值没有意义。
    3. **中位数用 pandas 算**。自己 `vals[len(vals)//2]` 在偶数样本上取的是偏大的那一个,
       不是中位数 ([1,3] 会得到 3 而不是 2)。

    ⚠️ 反例是**按事后 5 日损失挑的**, 不是最相似的日子 —— 必须和「最相似」分开呈现,
    否则就是拿事后结果冒充相似度结论。
    """
    have = [r for r in ranked if pd.notna(fwd5.get(r["date"], float("nan")))]
    if not have:
        return {}
    near = an_dedup_by_event(have, positions, n, min_gap)
    # 诊断: 未去重的前 n 名里, 经同样的间隔过滤后还能留下几个? 留下得越少说明日期越聚集。
    # 只报 n_raw = n 是没信息量的 (过滤后会继续往下取, 最终总能凑够 n 个)。
    # ⚠️ 这是「贪心间隔过滤后的保留数」, **不是**连通分量意义上的簇数, 也不证明统计独立。
    n_retained = len(an_dedup_by_event(have[:n], positions, n, min_gap))
    vals = pd.Series([float(fwd5[r["date"]]) for r in near], dtype=float)
    # 基准 = 同一张榜的全部合格候选日 (不是整条 fwd5 序列)
    base = pd.Series([float(fwd5[r["date"]]) for r in have], dtype=float)
    worst = sorted(near, key=lambda r: float(fwd5[r["date"]]))[:adverse_n]
    return {"n": int(len(vals)),
            "n_raw": len(have[:n]),
            # 未过滤的前 n 名经同样间隔过滤后的保留数。等于 n 表示日期本来就分散;
            # 明显小于 n 表示那一批里有连着的日子, 不过滤的话同一段行情会被重复计票。
            "n_retained_from_raw_top_n": n_retained,
            "min_gap": min_gap,
            "neg_share": float((vals < 0).mean()),
            "median": float(vals.median()),
            "worst": float(vals.min()),
            "base_n": int(len(base)),
            "base_neg_share": float((base < 0).mean()) if len(base) else float("nan"),
            "base_median": float(base.median()) if len(base) else float("nan"),
            "adverse": [_build_analog(a, panel, raw, today, r, [], horizons) for r in worst],
            "note": ADVERSE_NOTE % len(vals)}


def an_dedup_by_event(ranked: list[dict], positions: dict, n: int, min_gap: int) -> list[dict]:
    """邻域去重: 与 dedup 同一套贪心逻辑, 只是窗口更小 (邻域要样本量, 主榜要代表性)。"""
    return dedup(ranked, positions, top_n=n, min_gap=min_gap)


def find_analogs(a: pf.Account, panel: pf.Panel, vix: pd.Series | None = None,
                 spy: pd.Series | None = None, dvol: pd.Series | None = None,
                 top_n: int = TOP_N, min_gap: int = MIN_GAP, exclude_recent: int = EXCLUDE_RECENT,
                 horizons=HORIZONS, weights: dict[str, float] | None = None,
                 now: pd.Timestamp | None = None) -> dict:
    """
    返回 {query, query_date, as_of, analogs, n_candidates, features_used, effective_weights,
          dvol_tier, neighborhood, configured_weights, scaler, notes, disclaimer}。

    `analogs` 是五年核心版 (默认展示); `dvol_tier` 是近期 DVOL 增强版, 距离与核心版**不可比**。
    两张榜各自挑自己的查询日 (它们要的特征不一样, 凑齐的最新日期也就可能不同), 各自报 query_date。
    `now` 只给测试注入用 —— 决定哪些日线算走完。
    """
    cutoff, us_cut, cr_cut = as_of_cutoff(now)
    # ⚠️ 上面三个是**日历边界**, 不是行情日期 —— 周日运行时 us_cut 会是周日, 而那天没有 bar。
    # 真正的「最后一根完整日线」要拿边界去实际数据里取, 界面只能显示后者 (第四轮复核指出)。
    empty = {"analogs": [], "n_candidates": 0, "query": {}, "query_date": "",
             "as_of": {"calendar_cutoff": str(cutoff), "us_calendar_limit": str(us_cut),
                       "crypto_calendar_limit": str(cr_cut), "us_last_complete_bar": "",
                       "core_query_date": "", "dvol_query_date": "", "dropped_incomplete": 0},
             "features_used": [], "effective_weights": {}, "dvol_tier": {}, "neighborhood": {},
             "configured_weights": {GROUP_LABELS.get(g, g): w
                                    for g, w in (weights or GROUP_WEIGHTS).items()},
             "scaler": {}, "disclaimer": DISCLAIMER}
    raw_all = build_features(a, panel, vix, spy, dvol)
    if raw_all.empty:
        return {**empty, "notes": ["行情样本不足, 算不了相似情景"]}
    # 只留已经走完的日线: 盘中那根还在形成的当日 bar 必须丢掉, 否则「今天」是半天、
    # 历史日期全是整天, 收益和波动根本不可比 (第三轮复核指出)
    raw = raw_all[[d <= cutoff for d in raw_all.index]]
    dropped = len(raw_all) - len(raw)
    if len(raw) < 2:
        return {**empty, "notes": ["截止 %s 之前的完整交易日不够, 算不了相似情景" % cutoff]}

    scaled, scaler = robust_scale(raw)
    positions = {d: i for i, d in enumerate(raw.index)}
    max_h = max(horizons)

    tier = lambda keys: _rank_tier(a, panel, raw, scaled, positions, keys, weights,  # noqa: E731
                                   top_n, min_gap, exclude_recent, horizons, cutoff, max_h)
    core = tier([k for k in CORE_KEYS if k in scaled.columns])
    dv = (tier(list(DVOL_KEYS)) if "dvol_btc" in scaled.columns else
          {**{"analogs": [], "n_candidates": 0, "features_used": [], "query_date": "",
              "date_range": "", "effective_weights": {}}, "note": "DVOL 取数失败, 这张榜不可用"})

    fwd5 = forward_return_series(a, panel, max_h)
    nb = (_neighborhood(a, panel, core["ranked"], fwd5, positions, horizons=horizons,
                        raw=raw, today=pd.Timestamp(core["query_date"]).date())
          if core.get("query_date") and core.get("ranked") else {})

    notes = []
    if dropped:
        notes.append("丢掉了 %d 根还没走完的日线: 只用完整交易日 —— 美股要过当日收盘并留 15 分钟"
                     "发布缓冲, 加密要过 UTC 次日 00:00。日历边界 %s, 对应实际最后一个完整的"
                     "美股交易日是 %s。" % (dropped, cutoff,
                                          last_bar_on_or_before(raw_all.index, cutoff) or "—"))
    missing = [FEATURES[k][0] for k in CORE_KEYS if k not in scaled.columns]
    if missing:
        notes.append("核心版这次没用上的特征: %s (整列取数失败或无变化)" % ", ".join(missing))
    if core.get("query_missing"):
        notes.append("查询日那天缺了 %s, 所以核心版退回到最近一个把特征凑齐的交易日 %s。"
                     % ("、".join(core["query_missing"]), core.get("query_date") or "—"))
    if core.get("note"):
        notes.append(core["note"])
    if "dvol_btc" in raw.columns:
        cover = int(raw["dvol_btc"].notna().sum())
        notes.append("DVOL 覆盖 %d / %d 个完整交易日 (Deribit 历史有上限), 所以它单独成榜, "
                     "不混进五年核心版 —— 两张榜的距离不能互相比较" % (cover, len(raw)))
    if core["features_used"]:
        notes.append("五年核心版里每个候选日都具备完全相同的 %d 项特征, 距离才可比; "
                     "不满足的日子 (刚上市、窗口预热期) 不进榜。" % len(core["features_used"]))
    eff, cfg = core.get("effective_weights") or {}, {GROUP_LABELS.get(g, g): w
                                                    for g, w in (weights or GROUP_WEIGHTS).items()}
    if eff and any(abs(eff.get(k, 0) - v) > 1e-9 for k, v in cfg.items()):
        notes.append("有特征组整组缺失, 距离在剩下的组之间重新归一了 —— "
                     "下面显示的是**本次实际权重**, 不是配置里的比例。")

    key_by_label = {FEATURES[k][0] for k in FEATURES}
    lab2key = {FEATURES[k][0]: k for k in FEATURES}
    qd = core.get("query_date")
    used = core["features_used"] or [FEATURES[k][0] for k in scaled.columns]
    return {"query": ({lab: fmt_feature(lab2key[lab], raw.loc[pd.Timestamp(qd).date(), lab2key[lab]])
                       for lab in used if lab in key_by_label and lab2key[lab] in raw.columns}
                      if qd else {}),
            "query_date": qd or "",
            "as_of": {"calendar_cutoff": str(cutoff),
                      "us_calendar_limit": str(us_cut), "crypto_calendar_limit": str(cr_cut),
                      # 这一个才是真实存在的美股交易日 (从面板里取出来的)
                      "us_last_complete_bar": str(last_bar_on_or_before(raw_all.index, cutoff) or ""),
                      "core_query_date": core.get("query_date", ""),
                      "dvol_query_date": dv.get("query_date", ""),
                      "dropped_incomplete": dropped},
            "analogs": core["analogs"],
            "n_candidates": core["n_candidates"],
            "features_used": core["features_used"],
            "date_range": core.get("date_range", ""),
            "effective_weights": core.get("effective_weights") or {},
            "configured_weights": cfg,
            "dvol_tier": {k: v for k, v in dv.items() if k != "ranked"},
            "neighborhood": nb,
            "scaler": scaler,
            "notes": notes,
            "disclaimer": DISCLAIMER}
