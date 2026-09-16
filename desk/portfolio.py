"""
组合压力测试 (跨资产统一账户): 多只 rToken 杠杆仓位 + USDT / BTC / ETH 抵押品, 重演 5 年里的每一个交易日。

为什么要组合级: 单笔测试看不到「同一天所有仓位一起跌、抵押品 BTC 也一起跌」的双重打击。
实测 (Yahoo 5 年): 2024-08-05 11 只标的平均盘中较前收 -11.73%, 同期 BTC -20.02%。

账户模型 (全仓, 公开写明, 界面展示):
  初始权益 E0      = USDT + Σ 加密数量_c x 价格_c x 折算率_c            (c = BTC, ETH)
  情景后权益 E     = E0 + Σ 方向_i x 名义_i x r_i + Σ 加密数量_c x 价格_c x 折算率_c x r_c
  维持保证金 M     = 维持保证金率 x Σ 名义_i x (1 + r_i)
  触及强平         : E <= M
  全仓下强平只看总权益 vs 总维持保证金, 单笔「杠杆」不决定风险, 名义仓位才决定。
  维持保证金率、BTC / ETH 抵押折算率都是【用户假设参数】, 不是 Bitget 官方数值。
  不含手续费、资金费、滑点、分档维持保证金、强平罚金; 假设开仓时未实现盈亏为 0。
  ETH 只作抵押品 (2026-09-16 加入), 不作仓位标的。

历史重演的两种口径 (都展示, 让用户看到区间):
  extreme  每个资产取「前收 -> 当日最不利极值」(多头看最低, 空头看最高, 加密抵押品看最低)
           假设各资产的最不利点同时发生 -> 偏保守的上限
  close    每个资产取「前收 -> 当日收盘」-> 偏乐观的下限

对齐近似: 股票按纽约交易日; BTC-USD / ETH-USD 是 UTC 日线。股票日 d (前一交易日 p) 对应的加密变动
= p 日 (或之前最近一行) 收盘 -> (p, d] 这几个 UTC 日内的极值 / 最后一行收盘 (周一会覆盖整个周末)。
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field

import pandas as pd

from .idea import SUPPORTED
from .provenance import CallLog, Provenance
from .sources import yahoo

BTC, ETH = "BTC", "ETH"
BTC_YAHOO, ETH_YAHOO = "BTC-USD", "ETH-USD"


@dataclass
class Position:
    """
    一笔持仓。notional 永远是【按当前价算的名义价值】。

    kind = "leverage" 杠杆仓: 占维持保证金, 会被强平
    kind = "spot"     现货:   市值计入权益, 不占维持保证金, 自己不会被强平 (但照样会亏)
    entry / price 可选; 两个都有时能算出这笔的未实现盈亏, 并计入开仓权益
    (不填就退回旧假设: 当作刚开仓, 浮盈浮亏为 0)。
    """
    symbol: str          # 正股代码 (对应 R<symbol>USDT)
    side: str            # long | short
    notional: float      # 名义价值 USDT (> 0), 按当前价
    kind: str = "leverage"          # leverage | spot
    entry: float | None = None      # 买入价
    price: float | None = None      # 当前价 (取数时填, 用于算未实现盈亏)
    qty: float | None = None        # 数量 (界面用 买价 x 数量 推名义价值时保留)

    @property
    def sign(self) -> int:
        return 1 if self.side == "long" else -1

    @property
    def pnl(self) -> float:
        """未实现盈亏 (USDT)。缺买入价或当前价时按 0 处理。"""
        if not self.entry or not self.price or self.entry <= 0:
            return 0.0
        return self.sign * self.notional * (1 - self.entry / self.price)


@dataclass
class Account:
    positions: list[Position]
    usdt: float = 0.0
    btc: float = 0.0                 # 作抵押的 BTC 数量
    maint_margin: float = 0.01       # 用户假设
    btc_haircut: float = 0.95        # 用户假设: BTC 计入保证金的折算率
    eth: float = 0.0                 # 作抵押的 ETH 数量
    eth_haircut: float = 0.95        # 用户假设: ETH 计入保证金的折算率

    def to_dict(self) -> dict:
        return asdict(self)


def account_from_dict(d: dict) -> Account:
    """to_dict() 的逆操作 (兼容没有 eth 字段的旧数据)。"""
    return Account([Position(**p) for p in d["positions"]], d["usdt"], d["btc"], d["maint_margin"], d["btc_haircut"],
                   d.get("eth", 0.0), d.get("eth_haircut", 0.95))


def validate_account(a: Account) -> str | None:
    if not a.positions:
        return "组合里至少要有一个仓位"
    seen = set()
    for p in a.positions:
        if p.symbol not in SUPPORTED:
            return "不支持的标的: %r (目前支持 %s)" % (p.symbol, ", ".join(sorted(SUPPORTED)))
        if p.side not in ("long", "short"):
            return "%s 的方向必须是 long 或 short" % p.symbol
        if p.kind not in ("leverage", "spot"):
            return "%s 的类型必须是 leverage 或 spot" % p.symbol
        if p.kind == "spot" and p.side == "short":
            return "%s: 现货不能做空 (要做空请用杠杆仓)" % p.symbol
        if not p.notional > 0:
            return "%s 的名义价值必须大于 0" % p.symbol
        if (p.symbol, p.side) in seen:
            return "%s %s 重复出现, 请合并成一笔" % (p.symbol, p.side)
        seen.add((p.symbol, p.side))
    if a.usdt < 0 or a.btc < 0 or a.eth < 0:
        return "抵押品数量不能为负"
    if a.usdt == 0 and a.btc == 0 and a.eth == 0 and spot_value(a) == 0:
        return "没有抵押品 (USDT、BTC 或 ETH), 也没有现货"
    if not 0 < a.maint_margin < 0.5:
        return "维持保证金率需在 0-50% 之间"
    if not 0 < a.btc_haircut <= 1 or not 0 < a.eth_haircut <= 1:
        return "抵押折算率需在 0-100% 之间"
    return None


def crypto_names(a: Account) -> str:
    """界面文字用: 这个组合里有哪些加密抵押品, 如 'BTC' / 'ETH' / 'BTC/ETH'。"""
    names = [n for n, q in ((BTC, a.btc), (ETH, a.eth)) if q > 0]
    return "/".join(names) if names else "加密"


# ---------------------------------------------------------------- 数据面板

@dataclass
class Panel:
    """按股票交易日对齐的逐日变动 (相对前一交易日收盘)。列: 标的代码 + 'BTC' (+ 'ETH')。"""
    close_ret: pd.DataFrame
    low_ret: pd.DataFrame
    high_ret: pd.DataFrame
    close_level: pd.DataFrame
    btc_price: float                       # 最新 BTC 收盘价 (计算抵押品价值用)
    btc_price_date: str
    provenance: dict[str, Provenance] = field(default_factory=dict)
    eth_price: float = 0.0                 # 最新 ETH 收盘价; 面板没有 ETH 数据时为 0
    eth_price_date: str = ""

    @property
    def dates(self) -> list[str]:
        return [str(d) for d in self.close_ret.index]


def _stock_daily(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["date"] = d["ts"].dt.tz_convert("America/New_York").dt.date
    return d.drop_duplicates("date", keep="last").set_index("date").sort_index()[["open", "high", "low", "close"]]


def _btc_daily(df: pd.DataFrame) -> pd.DataFrame:
    """加密日线 (UTC 日期)。BTC 与 ETH 共用。"""
    d = df.copy()
    d["date"] = d["ts"].dt.tz_convert("UTC").dt.date
    return d.drop_duplicates("date", keep="last").set_index("date").sort_index()[["open", "high", "low", "close"]]


def build_panel(stocks: dict[str, pd.DataFrame], btc: pd.DataFrame,
                provenance: dict[str, Provenance] | None = None, eth: pd.DataFrame | None = None) -> Panel:
    """stocks: {代码: Yahoo 日线}; btc / eth: Yahoo BTC-USD / ETH-USD 日线。只保留所有股票和加密都有数据的交易日。"""
    sd = {s: _stock_daily(df) for s, df in stocks.items()}
    dates = sorted(set.intersection(*(set(d.index) for d in sd.values())))
    cryptos = {BTC: _btc_daily(btc)}
    if eth is not None:
        cryptos[ETH] = _btc_daily(eth)
    rows_c, rows_l, rows_h, idx = [], [], [], []
    for p, d in zip(dates, dates[1:]):
        c, lo, hi = {}, {}, {}
        ok = True
        for name, b in cryptos.items():
            # p 日 (或之前最近一行) 收盘 -> (p, d] 内的极值与最后一行收盘
            # Yahoo BTC-USD 日线偶有缺行 (2026-09-14 实测缺), 不能要求当天必须有行
            before = b[b.index <= p]
            win = b[(b.index > p) & (b.index <= d)]
            if before.empty or win.empty:
                ok = False
                break
            ref = float(before["close"].iloc[-1])
            c[name] = float(win["close"].iloc[-1]) / ref - 1
            lo[name] = float(win["low"].min()) / ref - 1
            hi[name] = float(win["high"].max()) / ref - 1
        if not ok:
            continue
        for s, df in sd.items():
            pc = float(df.loc[p, "close"])
            c[s] = float(df.loc[d, "close"]) / pc - 1
            lo[s] = float(df.loc[d, "low"]) / pc - 1
            hi[s] = float(df.loc[d, "high"]) / pc - 1
        rows_c.append(c), rows_l.append(lo), rows_h.append(hi), idx.append(d)
    mk = lambda rows: pd.DataFrame(rows, index=idx)  # noqa: E731
    # 收盘价水平: 含第一个交易日, 多日回撤要从它起算
    lv_dates = [d for d in dates if all((b.index <= d).any() for b in cryptos.values())]
    level = pd.DataFrame({**{s: [float(df.loc[d, "close"]) for d in lv_dates] for s, df in sd.items()},
                          **{n: [float(b[b.index <= d]["close"].iloc[-1]) for d in lv_dates] for n, b in cryptos.items()}},
                         index=lv_dates)
    bb = cryptos[BTC]
    eb = cryptos.get(ETH)
    return Panel(mk(rows_c), mk(rows_l), mk(rows_h), level, float(bb["close"].iloc[-1]), str(bb.index[-1]),
                 provenance or {}, float(eb["close"].iloc[-1]) if eb is not None else 0.0,
                 str(eb.index[-1]) if eb is not None else "")


def fetch_panel(symbols: list[str], log: CallLog | None = None, range_: str = "5y", retries: int = 2) -> Panel:
    """并行从 Yahoo 取所需标的 + BTC-USD + ETH-USD 日线, 每次调用 (含失败的重试) 都留痕。"""
    want = sorted(set(symbols)) + [BTC_YAHOO, ETH_YAHOO]

    def get(s):
        attempts = []
        for i in range(retries + 1):
            df, prov = yahoo.ohlcv(s, range_, "1d")
            attempts.append(prov)
            if df is not None:
                break
            time.sleep(1.5 * (i + 1))    # 2026-09-15 实测 Yahoo 偶发单次失败
        return df, attempts

    with ThreadPoolExecutor(max_workers=len(want)) as ex:
        results = dict(zip(want, ex.map(get, want)))
    provs = {}
    names = {BTC_YAHOO: BTC, ETH_YAHOO: ETH}
    for s, (df, attempts) in results.items():
        for prov in attempts:
            if log is not None:
                log.append(prov)
        provs[names.get(s, s)] = attempts[-1]
        if df is None:
            raise RuntimeError("%s 行情取数失败 (%d 次): %s" % (s, len(attempts), attempts[-1].error))
    stocks = {s: results[s][0] for s in want if s not in names}
    return build_panel(stocks, results[BTC_YAHOO][0], provs, results[ETH_YAHOO][0])


# ---------------------------------------------------------------- 情景计算

@dataclass
class ShockResult:
    equity0: float
    equity: float
    maint: float
    liquidated: bool
    position_pnl: dict[str, float]     # 键: "NVDA long"
    collateral_pnl: float              # 全部加密抵押品的盈亏合计
    loss_pct: float                    # (E0 - E) / E0, 亏损为正
    buffer0: float                     # E0 - M0: 开仓时离强平的距离 (USDT)
    buffer_used: float                 # (E0 - E) / buffer0

    def to_dict(self) -> dict:
        return asdict(self)


def spot_value(a: Account) -> float:
    """现货市值: 本身就是账户资产的一部分 (不要再把买现货花掉的 USDT 重复算进保证金)。"""
    return sum(p.notional for p in a.positions if p.kind == "spot")


def unrealized_pnl(a: Account) -> float:
    """所有仓位的未实现盈亏合计 (只有填了买入价和当前价的仓位才算)。"""
    return sum(p.pnl for p in a.positions if p.kind != "spot")


def equity0(a: Account, btc_price: float, eth_price: float = 0.0) -> float:
    """开仓时账户权益 = 抵押品 (折算后) + 现货市值 + 杠杆仓的未实现盈亏。"""
    return (a.usdt + a.btc * btc_price * a.btc_haircut + a.eth * eth_price * a.eth_haircut
            + spot_value(a) + unrealized_pnl(a))


def maint0(a: Account) -> float:
    """维持保证金只看杠杆仓; 现货不占维持保证金。"""
    return a.maint_margin * sum(p.notional for p in a.positions if p.kind != "spot")


def gross_notional(a: Account) -> float:
    return sum(p.notional for p in a.positions)


def _need_eth(a: Account, panel: Panel):
    if a.eth > 0 and (ETH not in panel.close_ret.columns or panel.eth_price <= 0):
        raise ValueError("组合有 ETH 抵押品, 但行情面板里没有 ETH 数据")


def shock(a: Account, btc_price: float, moves: dict[str, float], eth_price: float = 0.0) -> ShockResult:
    """moves: {代码: 价格变动, 'BTC': BTC 价格变动, 'ETH': ETH 价格变动}; 缺失视为 0。"""
    e0 = equity0(a, btc_price, eth_price)
    m0 = maint0(a)
    pos_pnl = {}
    maint = 0.0
    for p in a.positions:
        r = moves.get(p.symbol, 0.0)
        pos_pnl["%s %s" % (p.symbol, p.side)] = p.sign * p.notional * r
        if p.kind != "spot":                     # 现货不占维持保证金
            maint += a.maint_margin * p.notional * (1 + r)
    coll = (a.btc * btc_price * a.btc_haircut * moves.get(BTC, 0.0)
            + a.eth * eth_price * a.eth_haircut * moves.get(ETH, 0.0))
    e = e0 + sum(pos_pnl.values()) + coll
    buf = e0 - m0
    return ShockResult(e0, e, maint, e <= maint, pos_pnl, coll, (e0 - e) / e0 if e0 else 0.0,
                       buf, (e0 - e) / buf if buf > 0 else float("inf"))


def day_moves(a: Account, panel: Panel, date, mode: str = "extreme") -> dict[str, float]:
    """某历史交易日在该组合上的变动。extreme: 每个资产取对自己最不利的极值 (同时发生的保守假设)。"""
    cryptos = [c for c in (BTC, ETH) if c in panel.close_ret.columns]
    if mode == "close":
        row = panel.close_ret.loc[date]
        return {**{p.symbol: float(row[p.symbol]) for p in a.positions}, **{c: float(row[c]) for c in cryptos}}
    if mode != "extreme":
        raise ValueError(mode)
    lo, hi = panel.low_ret.loc[date], panel.high_ret.loc[date]
    m = {p.symbol: float(lo[p.symbol] if p.side == "long" else hi[p.symbol]) for p in a.positions}
    for c in cryptos:
        m[c] = float(lo[c])   # 加密抵押品是多头敞口
    return m


def _collateral_series(a: Account, panel: Panel, frame: pd.DataFrame) -> pd.Series:
    coll = a.btc * panel.btc_price * a.btc_haircut * frame[BTC]
    if a.eth > 0:
        coll = coll + a.eth * panel.eth_price * a.eth_haircut * frame[ETH]
    return coll


def replay(a: Account, panel: Panel, mode: str = "extreme") -> pd.DataFrame:
    """把每个历史交易日重演到组合上 (向量化)。返回按亏损从大到小排序的表。"""
    _need_eth(a, panel)
    e0 = equity0(a, panel.btc_price, panel.eth_price)
    m0 = maint0(a)
    frame = panel.close_ret if mode == "close" else panel.low_ret
    btc_r = frame[BTC]
    pnl = pd.Series(0.0, index=panel.close_ret.index)
    maint = pd.Series(0.0, index=panel.close_ret.index)
    worst_pos = pd.DataFrame(index=panel.close_ret.index)
    for p in a.positions:
        if mode == "close":
            r = panel.close_ret[p.symbol]
        else:
            r = panel.low_ret[p.symbol] if p.side == "long" else panel.high_ret[p.symbol]
        pp = p.sign * p.notional * r
        worst_pos["%s %s" % (p.symbol, p.side)] = pp
        pnl += pp
        if p.kind != "spot":                     # 现货不占维持保证金
            maint += a.maint_margin * p.notional * (1 + r)
    coll = _collateral_series(a, panel, frame)
    eq = e0 + pnl + coll
    buf = e0 - m0
    out = pd.DataFrame({
        "date": [str(d) for d in panel.close_ret.index],
        "positions_pnl": pnl.values,
        "collateral_pnl": coll.values,
        "equity": eq.values,
        "maint": maint.values,
        "liquidated": (eq <= maint).values,
        "loss_pct": ((e0 - eq) / e0).values,
        "buffer_used": ((e0 - eq) / buf).values if buf > 0 else float("inf"),
        "btc_move": btc_r.values,
        "eth_move": frame[ETH].values if ETH in frame.columns else float("nan"),
        "worst_position": worst_pos.idxmin(axis=1).values if len(a.positions) else None,
    })
    return out.sort_values("loss_pct", ascending=False).reset_index(drop=True)


def multi_day_worst(a: Account, panel: Panel, horizon: int = 5) -> pd.DataFrame:
    """
    持有 1..horizon 个交易日的累计回撤 (收盘价口径, 不调仓): 对每个起点, 取持有期内权益最低的那天。
    返回按最大亏损排序的表。收盘口径不含盘中极值 -> 与单日 extreme 口径互补。
    """
    _need_eth(a, panel)
    lv = panel.close_level
    e0 = equity0(a, panel.btc_price, panel.eth_price)
    rows = []
    for j in range(1, horizon + 1):
        rel = lv.shift(-j) / lv - 1
        pnl = sum(p.sign * p.notional * rel[p.symbol] for p in a.positions)
        maint = sum(a.maint_margin * p.notional * (1 + rel[p.symbol]) for p in a.positions if p.kind != "spot")
        eq = e0 + pnl + _collateral_series(a, panel, rel)
        rows.append(pd.DataFrame({"start": [str(d) for d in lv.index], "days": j, "equity": eq.values,
                                  "maint": maint.values, "btc_move": rel[BTC].values}))
    df = pd.concat(rows).dropna()
    df["loss_pct"] = (e0 - df["equity"]) / e0
    df["liquidated"] = df["equity"] <= df["maint"]
    worst = df.sort_values("loss_pct", ascending=False).drop_duplicates("start")
    return worst.reset_index(drop=True)


def scale_to_liquidation(a: Account, btc_price: float, moves: dict[str, float],
                         k_max: float = 20.0, tol: float = 1e-6, eth_price: float = 0.0) -> float | None:
    """
    反向压力测试: 把一个情景的所有变动同比例放大 k 倍, 求刚好触及强平的 k。
    k < 1 表示这个情景本身已经强平; 返回 None 表示放大到 k_max 倍 (或价格跌到 0) 都不强平。
    """
    def f(k):
        scaled = {s: max(k * r, -1.0) for s, r in moves.items()}
        res = shock(a, btc_price, scaled, eth_price)
        return res.equity - res.maint

    if f(0) <= 0:
        return 0.0
    hi = k_max
    lim = [1.0 / abs(r) for r in moves.values() if r < 0]   # 价格不能跌破 0
    if lim:
        hi = min(hi, min(lim))
    if f(hi) > 0:
        return None
    lo = 0.0
    while hi - lo > tol:
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if f(mid) > 0 else (lo, mid)
    return hi


def uniform_move_to_liquidation(a: Account, btc_price: float, btc_follows: bool, eth_price: float = 0.0) -> float | None:
    """
    所有仓位同时朝不利方向变动 x (多头跌 x、空头涨 x) 时, x 多大触及强平。
    btc_follows=True: 加密抵押品 (BTC 和 ETH) 同时跌 x (相关性 = 1 的双重打击); False: 加密抵押品不动。
    """
    moves = {p.symbol: -p.sign * 1.0 for p in a.positions}
    if btc_follows:
        moves[BTC] = -1.0
        moves[ETH] = -1.0
    return scale_to_liquidation(a, btc_price, moves, k_max=1.0, eth_price=eth_price)


# 预设假设情景 (全部是【假设】, 不是预测; 数字是情景参数不是历史数据)
# 「BTC/ETH」一栏同时施加到两种加密抵押品上 (假设二者同幅变动, 这是简化)
PRESETS = [
    {"key": "tech_btc_crash", "name": "美股同步 -10% 且 BTC/ETH -20%", "stocks": -0.10, "btc": -0.20},
    {"key": "weekend_vacuum", "name": "周末流动性真空: rToken -5% 且 BTC/ETH -10%", "stocks": -0.05, "btc": -0.10},
    {"key": "btc_only", "name": "只有 BTC/ETH 闪崩 -30%, 美股不动", "stocks": 0.0, "btc": -0.30},
    {"key": "stocks_only", "name": "只有美股 -15%, BTC/ETH 不动", "stocks": -0.15, "btc": 0.0},
]


def preset_moves(a: Account, preset: dict) -> dict[str, float]:
    """预设情景里的「美股变动」按市场方向施加: 多头和空头看到的是同一个价格变动。"""
    return {**{p.symbol: preset["stocks"] for p in a.positions}, BTC: preset["btc"], ETH: preset["btc"]}
