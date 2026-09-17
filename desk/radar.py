"""
开仓前风险雷达: 只放和持仓风险直接相关、且来源可追溯的市场数据。

设计原则 (和本工具其他部分一致):
  1. 每个数字都记录来源 (Provenance), 界面上能点开看是哪次调用
  2. 同一个数字尽量用多个来源交叉核对, 不一致就标出来 —— 而不是挑一个顺眼的
  3. 不做新闻 / 政策解读 (无法逐句溯源, 且与 Bitget GetAgent 重复)

来源实测 (2026-09-16, 本机直连):
  ✅ Yahoo ^VIX · Deribit DVOL (BTC/ETH 波动率指数) · alternative.me 恐惧贪婪 · CoinGecko 价格与全局
     Nasdaq 财报日历 (实测覆盖约未来 50 天) · Nasdaq 报价 (交叉核对美股价格) · Deribit 指数价格
  ❌ Stooq / StockAnalysis (拦截程序访问) · FRED (超时) · CoinDesk (需 API key) · Nasdaq 单标的财报日接口 (404)
"""
from __future__ import annotations

import datetime as dt
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field

import requests

from .provenance import CallLog, Provenance
from .sources import yahoo

UA = {"User-Agent": "Mozilla/5.0"}
DERIBIT = "https://www.deribit.com/api/v2/public"
COINGECKO = "https://api.coingecko.com/api/v3"
ALTME = "https://api.alternative.me/fng/"
NASDAQ = "https://api.nasdaq.com/api"
ETFS = {"SPY", "QQQ"}          # Nasdaq 报价要用 assetclass=etf; ETF 没有财报


@dataclass
class Metric:
    key: str
    label: str
    value: float | None
    display: str
    sources: list[str] = field(default_factory=list)   # 给界面「来源」弹出框
    status: str = "ok"                                 # ok | warn (取数失败或多来源不一致)
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _get(source: str, url: str, params: dict | None, parse, timeout: float = 20.0):
    """通用取数: 返回 (解析结果 | None, Provenance)。失败不抛异常, 如实留痕。"""
    t0 = time.time()
    try:
        r = requests.get(url, params=params, headers=UA, timeout=timeout)
        r.raise_for_status()
        value = parse(r.json())
        return value, Provenance(source, url, params or {}, time.time(), int((time.time() - t0) * 1000), True,
                                 n_records=len(value) if isinstance(value, (list, dict)) else None)
    except Exception as e:
        return None, Provenance(source, url, params or {}, time.time(), int((time.time() - t0) * 1000), False,
                                error="%s: %s" % (type(e).__name__, str(e)[:160]))


def _pct_rank(series: list[float], value: float) -> float:
    """value 在历史序列里的百分位 (0-100): 多少比例的历史日子低于它。"""
    return 100.0 * sum(1 for x in series if x <= value) / len(series)


def _log_all(log: CallLog | None, provs: list[Provenance]):
    for p in provs:
        if log is not None:
            log.append(p)


# ---------------------------------------------------------------- 单项指标

def vix(log: CallLog | None = None):
    """美股恐慌指数 VIX (Cboe 官方指数, 经 Yahoo)。给出现值与 5 年百分位。"""
    df, prov = yahoo.ohlcv("^VIX", "5y", "1d")
    _log_all(log, [prov])
    if df is None or df.empty:
        return Metric("vix", "VIX (美股波动率)", None, "取数失败", [prov.label()], "warn"), [prov]
    closes = [float(x) for x in df["close"].dropna()]
    last, pr = closes[-1], _pct_rank(closes, closes[-1])
    return Metric("vix", "VIX (美股波动率)", last, "%.1f · 5 年百分位 %.0f%%" % (last, pr), [prov.label()],
                  "ok", "越高说明美股越紧张; 百分位 = 5 年里有多少比例的日子比现在低"), [prov]


def dvol(currency: str = "BTC", days: int = 1825, log: CallLog | None = None):
    """加密波动率指数 DVOL (Deribit 官方, 相当于加密版 VIX)。"""
    now = int(time.time() * 1000)
    params = {"currency": currency, "start_timestamp": now - days * 86400000, "end_timestamp": now, "resolution": "86400"}
    data, prov = _get("deribit", DERIBIT + "/get_volatility_index_data", params, lambda j: j["result"]["data"])
    _log_all(log, [prov])
    key, label = "dvol_%s" % currency.lower(), "%s 波动率指数 DVOL" % currency
    if not data:
        return Metric(key, label, None, "取数失败", [prov.label()], "warn"), [prov]
    closes = [float(row[4]) for row in data]
    last, pr = closes[-1], _pct_rank(closes, closes[-1])
    # Deribit 实际返回的历史长度有上限 (实测约 1000 天), 百分位只能按真实拿到的长度说
    return Metric(key, label, last, "%.1f · 近 %d 天里的百分位 %.0f%%" % (last, len(closes), pr),
                  [prov.label()], "ok", "Deribit 官方波动率指数 (加密版 VIX); 你的保证金就是 %s" % currency), [prov]


def put_call(currency: str = "BTC", log: CallLog | None = None):
    """
    期权未平仓的看跌/看涨比 (Deribit)。比 DVOL 更前瞻: DVOL 说「波动有多大」,
    这个说「市场在为下跌付多少钱」。>1 = 看跌未平仓更多。
    """
    data, prov = _get("deribit", DERIBIT + "/get_book_summary_by_currency",
                      {"currency": currency, "kind": "option"}, lambda j: j["result"])
    _log_all(log, [prov])
    key, label = "pc_%s" % currency.lower(), "%s 期权 看跌/看涨" % currency
    if not data:
        return Metric(key, label, None, "取数失败", [prov.label()], "warn"), [prov]
    put = sum(float(x.get("open_interest") or 0) for x in data if x["instrument_name"].endswith("-P"))
    call = sum(float(x.get("open_interest") or 0) for x in data if x["instrument_name"].endswith("-C"))
    if call <= 0:
        return Metric(key, label, None, "没有看涨未平仓", [prov.label()], "warn"), [prov]
    ratio = put / call
    return Metric(key, label, ratio, "%.2f" % ratio, [prov.label()], "ok",
                  "看跌 %s / 看涨 %s 张未平仓 · 大于 1 说明市场更愿意为下跌付钱"
                  % (format(round(put), ","), format(round(call), ","))), [prov]


def insider(symbols: list[str], log: CallLog | None = None, days: int = 90, max_filings: int = 3):
    """
    持仓标的的内部人交易 (SEC Form 4)。美股最硬的「筹码」数据, 免 key。
    ⚠️ 金额只统计已解析的那几份 —— 展示时必须写明「已解析 N / 共 M 份」。
    """
    from .sources import sec as sec_src
    rows: list[dict] = []
    provs: list[Provenance] = []
    stocks = [s for s in symbols if s not in ETFS]
    if not stocks:
        return rows, provs
    with ThreadPoolExecutor(max_workers=min(4, len(stocks))) as ex:
        for res in ex.map(lambda s: sec_src.insider_activity(s, days=days, max_filings=max_filings), stocks):
            d, ps = res
            provs += ps
            if d is not None:
                rows.append(d)
    _log_all(log, provs)
    rows.sort(key=lambda r: -r["n_filings"])
    return rows, provs


def _stock_mcp(timeout: float = 8.0):
    """
    连一次官方美股 MCP。连不上就整块跳过 —— 每个标的各等满超时会把雷达从 14 秒拖到 140 秒
    (2026-09-17 本机实测: 运营商挡 agent.bitget.com, 6 次调用各等 25 秒)。
    返回 (client | None, 来源记录)。
    """
    from .sources import bitget_mcp as bmcp
    c = bmcp.StockMCP(timeout=timeout)
    t0 = time.time()
    try:
        info = c.connect()
        return c, Provenance("bitget_mcp", bmcp.URL, {"probe": "initialize"}, time.time(),
                             int((time.time() - t0) * 1000), True, n_records=1)
    except Exception as e:                                          # noqa: BLE001
        return None, Provenance("bitget_mcp", bmcp.URL, {"probe": "initialize"}, time.time(),
                                int((time.time() - t0) * 1000), False,
                                error="%s: %s" % (type(e).__name__, str(e)[:120]))


def insider_crosscheck(rows: list[dict], client=None, log: CallLog | None = None):
    """
    拿 Bitget 官方美股 MCP 的内部人交易, 和我们自己解析的 SEC Form 4 对一遍。
    同一件事两个独立来源对得上 = 可信; 对不上要标出来, 不能悄悄选一个信。
    """
    from .sources import bitget_mcp as bmcp
    out, provs = [], []
    if not rows or client is None:
        return out, provs
    for r in rows:
        official, p = bmcp.insider_trading(r["symbol"], client)
        provs.append(p)
        item = {"symbol": r["symbol"], "sec_latest": r.get("latest"), "sec_filings": r["n_filings"],
                "official_latest": None, "official_rows": None, "agree": None, "error": p.error}
        if official:
            item["official_rows"] = len(official)
            item["official_latest"] = (official[0] or {}).get("filing_date")
            item["agree"] = (item["official_latest"] == item["sec_latest"])
        out.append(item)
    _log_all(log, provs)
    return out, provs


def price_targets(symbols: list[str], client=None, log: CallLog | None = None):
    """分析师目标价 (Bitget 官方美股 MCP)。开仓前的参考: 市场共识价位在哪、最近谁改了评级。"""
    from .sources import bitget_mcp as bmcp
    out, provs = [], []
    stocks = [s for s in symbols if s not in ETFS]
    if not stocks or client is None:
        return out, provs
    for sym in stocks:
        rows, p = bmcp.price_target(sym, client)
        provs.append(p)
        if not rows:
            continue
        latest = rows[0]
        out.append({"symbol": sym, "date": latest.get("published_date"), "firm": latest.get("analyst_firm"),
                    "target": latest.get("price_target"), "rating": latest.get("rating_current"),
                    "action": latest.get("action"), "n": len(rows)})
    _log_all(log, provs)
    return out, provs


def fear_greed(log: CallLog | None = None):
    """加密恐惧贪婪指数 (alternative.me)。只有这一个免费来源, 界面上要标明。"""
    data, prov = _get("alternative.me", ALTME, {"limit": 30}, lambda j: j["data"])
    _log_all(log, [prov])
    if not data:
        return Metric("fng", "加密恐惧贪婪指数", None, "取数失败", [prov.label()], "warn"), [prov]
    vals = [float(x["value"]) for x in data]
    return Metric("fng", "加密恐惧贪婪指数", vals[0],
                  "%.0f (%s) · 30 天均值 %.0f" % (vals[0], data[0]["value_classification"], statistics.mean(vals)),
                  [prov.label()], "ok", "只有这一个免费来源, 无法交叉核对"), [prov]


def crypto_context(log: CallLog | None = None):
    """加密市场概况 (CoinGecko): 总市值与 BTC 占比 —— 抵押品所在市场的背景。"""
    d, prov = _get("coingecko", COINGECKO + "/global", None, lambda j: j["data"])
    _log_all(log, [prov])
    if not d:
        return Metric("cg_global", "加密总市值 / BTC 占比", None, "取数失败", [prov.label()], "warn"), [prov]
    mcap = float(d["total_market_cap"]["usd"]) / 1e12
    dom = float(d["market_cap_percentage"]["btc"])
    chg = float(d.get("market_cap_change_percentage_24h_usd") or 0)
    return Metric("cg_global", "加密总市值 / BTC 占比", mcap,
                  "%.2f 万亿美元 (24h %+.1f%%) · BTC 占比 %.1f%%" % (mcap, chg, dom), [prov.label()]), [prov]


def crypto_price_crosscheck(log: CallLog | None = None, tol: float = 0.005):
    """BTC / ETH 现价: Yahoo vs CoinGecko vs Deribit 指数, 三家对不上就标警告。"""
    provs: list[Provenance] = []
    prices: dict[str, dict[str, float]] = {"BTC": {}, "ETH": {}}

    cg, p = _get("coingecko", COINGECKO + "/simple/price", {"ids": "bitcoin,ethereum", "vs_currencies": "usd"}, lambda j: j)
    provs.append(p)
    if cg:
        prices["BTC"]["CoinGecko"] = float(cg["bitcoin"]["usd"])
        prices["ETH"]["CoinGecko"] = float(cg["ethereum"]["usd"])
    for cur, idx in (("BTC", "btc_usd"), ("ETH", "eth_usd")):
        v, p = _get("deribit", DERIBIT + "/get_index_price", {"index_name": idx}, lambda j: j["result"]["index_price"])
        provs.append(p)
        if v:
            prices[cur]["Deribit"] = float(v)
        df, yp = yahoo.ohlcv("%s-USD" % cur, "5d", "1d")
        provs.append(yp)
        if df is not None and not df.empty:
            prices[cur]["Yahoo"] = float(df["close"].dropna().iloc[-1])
    _log_all(log, provs)

    out = []
    for cur, d in prices.items():
        if not d:
            out.append(Metric("px_" + cur.lower(), "%s 现价" % cur, None, "全部来源取数失败", [p.label() for p in provs], "warn"))
            continue
        lo, hi = min(d.values()), max(d.values())
        spread = (hi - lo) / lo if lo else 0.0
        detail = " · ".join("%s %s" % (k, format(round(v), ",")) for k, v in sorted(d.items()))
        out.append(Metric("px_" + cur.lower(), "%s 现价 (%d 个来源)" % (cur, len(d)), statistics.median(d.values()),
                          "%s · 最大差异 %.2f%%" % (detail, 100 * spread), [p.label() for p in provs],
                          "ok" if (spread <= tol and len(d) >= 2) else "warn",
                          "多来源互相核对; 差异超过 %.1f%% 标警告 (Yahoo 是收盘价, 另两家是实时, 盘后本来就有差)" % (100 * tol)))
    return out, provs


def stock_price_crosscheck(symbols: list[str], log: CallLog | None = None, tol: float = 0.01):
    """美股价格: Yahoo 收盘价 vs Nasdaq 官方报价页 lastSalePrice。"""
    provs: list[Provenance] = []
    out: list[Metric] = []

    def one(sym):
        df, yp = yahoo.ohlcv(sym, "5d", "1d")
        nv, np_ = _get("nasdaq", NASDAQ + "/quote/%s/info" % sym,
                       {"assetclass": "etf" if sym.upper() in ETFS else "stocks"},
                       lambda j: float(j["data"]["primaryData"]["lastSalePrice"].replace("$", "").replace(",", "")))
        y = float(df["close"].dropna().iloc[-1]) if df is not None and not df.empty else None
        return sym, y, nv, [yp, np_]

    if not symbols:
        return out, provs
    with ThreadPoolExecutor(max_workers=min(8, len(symbols))) as ex:
        for sym, y, n, ps in ex.map(one, sorted(set(symbols))):
            provs += ps
            vals = {k: v for k, v in (("Yahoo", y), ("Nasdaq", n)) if v is not None}
            if not vals:
                out.append(Metric("px_" + sym.lower(), "%s 价格" % sym, None, "取数失败", [p.label() for p in ps], "warn"))
                continue
            lo, hi = min(vals.values()), max(vals.values())
            spread = (hi - lo) / lo if lo else 0.0
            out.append(Metric("px_" + sym.lower(), "%s 价格 (%d 个来源)" % (sym, len(vals)),
                              statistics.median(vals.values()),
                              "%s · 差异 %.2f%%" % (" · ".join("%s %.2f" % kv for kv in sorted(vals.items())), 100 * spread),
                              [p.label() for p in ps], "ok" if (spread <= tol and len(vals) >= 2) else "warn"))
    _log_all(log, provs)
    return out, provs


def earnings(symbols: list[str], horizon_days: int = 50, log: CallLog | None = None, today: dt.date | None = None):
    """
    下次财报日: 先查 Nasdaq 财报日历 (实测覆盖约未来 50 天, 权威);
    查不到的用该标的历史财报节奏推算, 并明确标注「估算」。
    """
    today = today or dt.date.today()
    want = {s.upper() for s in symbols}
    found: dict[str, str] = {}
    provs: list[Provenance] = []
    want -= ETFS                # ETF 不用扫日历
    days = [d for d in (today + dt.timedelta(days=i) for i in range(1, horizon_days + 1)) if d.weekday() < 5]

    def cal(day):
        rows, p = _get("nasdaq", NASDAQ + "/calendar/earnings", {"date": str(day)},
                       lambda j: [r["symbol"].upper() for r in ((j.get("data") or {}).get("rows") or [])])
        return day, rows or [], p

    if want:
        with ThreadPoolExecutor(max_workers=8) as ex:
            for day, rows, p in ex.map(cal, days):
                provs.append(p)
                for s in want & set(rows):
                    if s not in found or day < dt.date.fromisoformat(found[s]):
                        found[s] = str(day)

    out = []
    for s in sorted({x.upper() for x in symbols}):
        if s in ETFS:
            out.append({"symbol": s, "date": None, "days": None, "source": "ETF 没有财报 (成分股财报分散在各自日期)",
                        "estimated": False})
            continue
        if s in found:
            d = dt.date.fromisoformat(found[s])
            out.append({"symbol": s, "date": found[s], "days": (d - today).days,
                        "source": "Nasdaq 财报日历", "estimated": False})
            continue
        hist, p = _get("nasdaq", NASDAQ + "/company/%s/earnings-surprise" % s, None,
                       lambda j: [int(c["x"]) for c in ((j.get("data") or {}).get("chart") or [])])
        provs.append(p)
        if hist:
            dates = sorted(dt.datetime.utcfromtimestamp(t).date() for t in hist)
            gaps = [(b - a).days for a, b in zip(dates, dates[1:])] or [91]
            step = max(int(statistics.median(gaps)), 30)
            est = dates[-1] + dt.timedelta(days=step)
            while est <= today:
                est += dt.timedelta(days=step)
            out.append({"symbol": s, "date": str(est), "days": (est - today).days,
                        "source": "按历史财报间隔推算 (上次 %s, 中位间隔 %d 天)" % (dates[-1], step), "estimated": True})
        else:
            out.append({"symbol": s, "date": None, "days": None, "source": "查不到", "estimated": True})
    _log_all(log, provs)
    return out, provs


def radar(symbols: list[str], log: CallLog | None = None, with_earnings: bool = True) -> dict:
    """一次取齐雷达要用的全部数据 (并行)。返回 {metrics, earnings, provenance, warn}。"""
    jobs = {
        "vix": lambda: vix(log),
        "dvol_btc": lambda: dvol("BTC", log=log),
        "dvol_eth": lambda: dvol("ETH", log=log),
        "fng": lambda: fear_greed(log),
        "ctx": lambda: crypto_context(log),
        "crypto_px": lambda: crypto_price_crosscheck(log),
        "stock_px": lambda: stock_price_crosscheck(symbols, log),
        "pc_btc": lambda: put_call("BTC", log),
    }
    with ThreadPoolExecutor(max_workers=len(jobs)) as ex:
        results = dict(zip(jobs, ex.map(lambda f: f(), jobs.values())))
    metrics: list[Metric] = []
    provs: list[Provenance] = []
    for key in ("vix", "dvol_btc", "dvol_eth", "pc_btc", "fng", "ctx"):
        m, ps = results[key]
        metrics.append(m)
        provs += ps
    for key in ("crypto_px", "stock_px"):
        ms, ps = results[key]
        metrics += ms
        provs += ps
    earn, eps = earnings(symbols, log=log) if with_earnings else ([], [])
    provs += eps
    ins, ips = insider(symbols, log=log) if with_earnings else ([], [])
    provs += ips
    # 官方美股 MCP: 先连一次, 连不上就整块跳过 (否则每个标的都等满超时)
    xcheck, targets = [], []
    if with_earnings:
        mcp_client, mprov = _stock_mcp()
        provs.append(mprov)
        _log_all(log, [mprov])
        if mcp_client is not None:
            xcheck, xps = insider_crosscheck(ins, mcp_client, log=log)
            provs += xps
            targets, tps = price_targets(symbols, mcp_client, log=log)
            provs += tps
    return {"metrics": [m.to_dict() for m in metrics], "earnings": earn, "insider": ins,
            "insider_xcheck": xcheck, "price_targets": targets,
            "provenance": [p.to_dict() for p in provs],
            "warn": [m.key for m in metrics if m.status == "warn"]}
