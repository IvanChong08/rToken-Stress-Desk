"""
SEC EDGAR: 内部人交易 (Form 4)。免 key, 实测 0.7~1 秒一次。

为什么放这个: 这是美股最硬的「筹码」数据 —— 公司内部人在买还是在卖, 每一条都能追到
sec.gov 上的原始申报。和财报日一样, 是隔夜跳空的常见来源, 而且完全符合本工具
「每个数字都能追到某次调用」的要求。

⚠️ 边界:
- Form 4 里的交易代码只解析最常见的两类: P = 公开市场买入, S = 公开市场卖出。
  其余 (A 授予 / M 行权 / F 代扣税 / G 赠与 ...) 一律归为「其他」, 不当成买卖 ——
  高管「卖出」里很大一部分其实是行权和代扣税, 把它们算成看空是常见的误读。
- ETF (SPY / QQQ / GLD) 没有内部人, 这里返回空。
- SEC 要求请求带能联系到人的 User-Agent, 否则封 IP。
"""
from __future__ import annotations

import html as _html
import re
import time
from datetime import date, datetime, timedelta

import requests

from ..provenance import Provenance

UA = {"User-Agent": "rToken-Stress-Desk/1.0 (hackathon research; chongivan9332@gmail.com)"}
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUB_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/"

BUY, SELL = "P", "S"          # 公开市场买入 / 卖出; 其余代码不当成买卖
_TICKERS: dict[str, str] = {}


def _get(url: str, timeout: float = 20.0):
    return requests.get(url, headers=UA, timeout=timeout)


def ticker_to_cik(symbol: str, timeout: float = 20.0) -> tuple[str | None, Provenance]:
    """代码 -> 10 位 CIK。整张表一次取回 (约 1 万条), 之后走进程内缓存。"""
    global _TICKERS
    t0 = time.time()
    if _TICKERS:
        return _TICKERS.get(symbol.upper()), Provenance("sec", TICKERS_URL, {"cached": True}, time.time(), 0, True,
                                                        n_records=len(_TICKERS))
    try:
        r = _get(TICKERS_URL, timeout)
        r.raise_for_status()
        _TICKERS = {v["ticker"].upper(): str(v["cik_str"]).zfill(10) for v in r.json().values()}
        return _TICKERS.get(symbol.upper()), Provenance("sec", TICKERS_URL, {}, time.time(),
                                                        int((time.time() - t0) * 1000), True, n_records=len(_TICKERS))
    except Exception as e:                                          # noqa: BLE001
        return None, Provenance("sec", TICKERS_URL, {}, time.time(), int((time.time() - t0) * 1000), False,
                                error="%s: %s" % (type(e).__name__, str(e)[:160]))


def _parse_form4(cik: str, accession: str, timeout: float = 20.0) -> tuple[dict | None, Provenance]:
    """取一份 Form 4 原文, 解析出申报人 / 交易代码 / 股数。"""
    base = ARCHIVE.format(cik_int=int(cik), acc_nodash=accession.replace("-", ""))
    t0 = time.time()
    try:
        items = _get(base + "index.json", timeout).json()["directory"]["item"]
        xmls = [f["name"] for f in items if f["name"].endswith(".xml")]
        if not xmls:
            raise ValueError("这份申报里没有 xml")
        doc = _get(base + xmls[0], timeout).text
        who = re.search(r"<rptOwnerName>([^<]+)</rptOwnerName>", doc)
        title = re.search(r"<officerTitle>([^<]+)</officerTitle>", doc)
        codes = re.findall(r"<transactionCode>(\w)</transactionCode>", doc)
        shares = [float(x) for x in re.findall(r"<transactionShares>\s*<value>([\d.]+)</value>", doc)]
        prices = [float(x) for x in re.findall(r"<transactionPricePerShare>\s*<value>([\d.]+)</value>", doc)]
        out = {"who": _html.unescape(who.group(1).strip()) if who else "?",
               "title": _html.unescape(title.group(1).strip()) if title else "",
               "codes": codes,
               "buy_shares": sum(s for c, s in zip(codes, shares) if c == BUY),
               "sell_shares": sum(s for c, s in zip(codes, shares) if c == SELL),
               "buy_usd": sum(s * p for c, s, p in zip(codes, shares, prices) if c == BUY),
               "sell_usd": sum(s * p for c, s, p in zip(codes, shares, prices) if c == SELL),
               "url": base + xmls[0]}
        return out, Provenance("sec", base + xmls[0], {"form": "4"}, time.time(),
                               int((time.time() - t0) * 1000), True, n_records=len(codes))
    except Exception as e:                                          # noqa: BLE001
        return None, Provenance("sec", base, {"form": "4"}, time.time(), int((time.time() - t0) * 1000), False,
                                error="%s: %s" % (type(e).__name__, str(e)[:160]))


def insider_activity(symbol: str, days: int = 90, max_filings: int = 6,
                     today: date | None = None, timeout: float = 20.0) -> tuple[dict | None, list[Provenance]]:
    """
    某只票最近 days 天的内部人交易。返回 (结果 | None, [来源记录])。
    结果: {symbol, cik, n_filings, buy_usd, sell_usd, net_usd, latest, people, parsed, url}
    只解析最近 max_filings 份 —— 每份是一次网络请求, 全量解析会拖到十几秒。
    """
    provs: list[Provenance] = []
    cik, p0 = ticker_to_cik(symbol, timeout)
    provs.append(p0)
    if not cik:
        return None, provs

    url = SUB_URL.format(cik=cik)
    t0 = time.time()
    try:
        r = _get(url, timeout)
        r.raise_for_status()
        rec = r.json()["filings"]["recent"]
        provs.append(Provenance("sec", url, {"symbol": symbol}, time.time(), int((time.time() - t0) * 1000), True,
                                n_records=len(rec.get("form", []))))
    except Exception as e:                                          # noqa: BLE001
        provs.append(Provenance("sec", url, {"symbol": symbol}, time.time(), int((time.time() - t0) * 1000), False,
                                error="%s: %s" % (type(e).__name__, str(e)[:160])))
        return None, provs

    cutoff = (today or date.today()) - timedelta(days=days)
    hits = [(rec["filingDate"][i], rec["accessionNumber"][i])
            for i, f in enumerate(rec["form"])
            if f == "4" and datetime.strptime(rec["filingDate"][i], "%Y-%m-%d").date() >= cutoff]

    buy_usd = sell_usd = 0.0
    people: list[str] = []
    parsed = 0
    for filed, acc in hits[:max_filings]:
        one, p = _parse_form4(cik, acc, timeout)
        provs.append(p)
        if one is None:
            continue
        parsed += 1
        buy_usd += one["buy_usd"]
        sell_usd += one["sell_usd"]
        who = one["who"] + (" (%s)" % one["title"] if one["title"] else "")
        if who not in people:
            people.append(who)

    return {"symbol": symbol.upper(), "cik": cik, "days": days,
            # ⚠️ 金额只统计 parsed 这几份 (每份是一次网络请求, 全量解析要十几秒)。
            # 上层展示必须写明「已解析 N / 共 M 份」, 否则 24 份里只算 4 份的金额会严重误导。
            "n_filings": len(hits), "parsed": parsed, "partial": parsed < len(hits),
            "buy_usd": buy_usd, "sell_usd": sell_usd, "net_usd": buy_usd - sell_usd,
            "latest": hits[0][0] if hits else None,
            "people": people[:4],
            "url": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=%s&type=4&dateb=&owner=include&count=40"
                   % cik}, provs
