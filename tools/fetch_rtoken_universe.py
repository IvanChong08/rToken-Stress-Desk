"""
拉取 Bitget 当前上架的 rToken 全量清单, 并逐个核对 Yahoo 有没有 5 年日线。

分两步 (因为两边的网络限制不一样):

  1) 在 AWS 上 (本机连不上 api.bitget.com):
         ssh aws-Hummingbot 'python3 -' < tools/fetch_rtoken_universe.py    # 或 --bitget
     只用标准库, 打印一段 JSON 到 stdout: {"asof":..., "spot":[...], "futures":[...]}

  1b) 还在 AWS 上, 取全部永续合约的最新价 (用来判别币股同名):
         ssh aws-Hummingbot 'python3 -c "..."' > data/bitget_futures_prices.json

  2) 在本机 (Yahoo 本机可直连):
         python -X utf8 tools/fetch_rtoken_universe.py --yahoo data/rtoken_bitget_raw.json
     逐个查 Yahoo, 写出 data/rtoken_universe.json

为什么还要比价: Bitget 的合约里股票永续和币永续同名同构, 而**币股同名很常见** ——
BCHUSDT 是比特币现金, 但 Bitget 也上了智利银行 (BCH) 的 rToken; SUI / AR / W / T 同理。
所以「这只 rToken 能不能加杠杆」不能只看名字, 要看该合约最新价与正股收盘价是否对得上 (差 >5% 判为币)。

为什么要查 Yahoo: Bitget 的合约清单里, 股票永续和币永续同名同构 (BCHUSDT 是币, NVDAUSDT 是股),
API 没有字段能分开。Yahoo 有没有这只票的日线, 既能滤掉同名的币, 也正好是本工具能不能压力测试的前提。
"""
import json
import sys
import time

SPOT_URL = "https://api.bitget.com/api/v2/spot/public/symbols"
FUT_URL = "https://api.bitget.com/api/v2/mix/market/contracts?productType=USDT-FUTURES"


def from_bitget() -> dict:
    """在 AWS 上跑: 只用标准库。"""
    import urllib.request

    def get(url):
        with urllib.request.urlopen(url, timeout=30) as r:
            return json.load(r)["data"]

    spot = [s for s in get(SPOT_URL)
            if s.get("status") == "online" and str(s.get("baseCoin", "")).startswith("r")
            and s.get("quoteCoin") == "USDT"]
    fut = [c["baseCoin"] for c in get(FUT_URL) if c.get("symbolStatus") == "normal"]
    return {"asof": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
            "spot": sorted({s["baseCoin"][1:].upper() for s in spot}),
            "spot_symbols": {s["baseCoin"][1:].upper(): s["symbol"] for s in spot},
            "futures": sorted(set(fut))}


def check_yahoo(tickers: list[str], futures: set[str], pause: float = 0.25) -> dict:
    """本机跑: 逐个查 Yahoo 5 年日线。Yahoo 没有 = 不收录 (多半是同名的币, 或已退市)。"""
    sys.path.insert(0, __file__.rsplit("tools", 1)[0])
    from desk.sources import yahoo

    ok, bad = {}, {}
    for i, t in enumerate(tickers, 1):
        ysym = t.replace(".", "-")                    # BRK.B -> BRK-B
        df, prov = yahoo.ohlcv(ysym, "5y", "1d")
        if df is not None and len(df) >= 250:         # 至少一年日线才算数
            ok[t] = {"yahoo": ysym, "days": len(df),
                     "first": str(df.ts.iloc[0].date()), "last": str(df.ts.iloc[-1].date()),
                     "futures": t in futures}
        else:
            bad[t] = (prov.error or "")[:80] or "日线不足 %d 根" % (0 if df is None else len(df))
        if i % 50 == 0:
            print("  %d/%d  收录 %d  跳过 %d" % (i, len(tickers), len(ok), len(bad)), flush=True)
        time.sleep(pause)
    return {"ok": ok, "skipped": bad}


def match_perps(universe: dict, fut_prices: dict, near: float = 0.10, far: float = 0.40) -> dict:
    """
    判断每只 rToken 的同名永续合约到底是股票永续, 还是恰好同名的币。
    用 Yahoo 实时价 (含盘前盘后) 和合约最新价比:
        差 <= near  -> 股票永续, 允许加杠杆
        差 >= far   -> 同名的币 (BCH=比特币现金 / SUI / NMR=Numeraire...), 这只票只能现货
        中间地带    -> 不下结论, 一律按「不能加杠杆」处理, 并把数字留在清单里供人工复核
    两个阈值之间留空档是故意的: 单一阈值会在「盘前大涨的股票」和「价位接近的同名币」上两头翻车。
    """
    sys.path.insert(0, __file__.rsplit("tools", 1)[0])
    from desk.sources import yahoo

    stat = {"stock_perp": 0, "name_clash": 0, "uncertain": 0, "no_perp": 0, "no_price": 0}
    uncertain = []
    for tic, meta in universe["symbols"].items():
        px = fut_prices.get(tic + "USDT")
        if px is None:
            meta["futures"], meta["perp"] = False, None
            stat["no_perp"] += 1
            continue
        live, _ = yahoo.last_price(meta["yahoo"])
        if not live:
            meta["futures"], meta["perp"] = False, {"status": "无正股实时价"}
            stat["no_price"] += 1
            continue
        diff = abs(float(px) - live) / live
        status = "stock" if diff <= near else ("clash" if diff >= far else "uncertain")
        meta["futures"] = status == "stock"
        meta["perp"] = {"status": status, "price": float(px), "stock_live": round(live, 4),
                        "diff_pct": round(100 * diff, 2)}
        stat[{"stock": "stock_perp", "clash": "name_clash", "uncertain": "uncertain"}[status]] += 1
        if status == "uncertain":
            uncertain.append("%s 合约 %s vs 正股 %s (差 %.1f%%)" % (tic, px, round(live, 4), 100 * diff))
        time.sleep(0.2)
    universe["perp_check"] = {"near_pct": 100 * near, "far_pct": 100 * far, **stat, "uncertain_list": uncertain,
                              "asof": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())}
    return universe


if __name__ == "__main__":
    if "--perps" in sys.argv:
        root = __file__.rsplit("tools", 1)[0]
        uni = json.load(open(root + "data/rtoken_universe.json", encoding="utf-8"))
        fp = json.load(open(root + "data/bitget_futures_prices.json", encoding="utf-8"))
        print("核对 %d 只标的的同名永续合约..." % len(uni["symbols"]), flush=True)
        uni = match_perps(uni, fp)
        with open(root + "data/rtoken_universe.json", "w", encoding="utf-8") as f:
            json.dump(uni, f, ensure_ascii=False, indent=1, sort_keys=True)
        print(json.dumps(uni["perp_check"], ensure_ascii=False))
    elif "--yahoo" in sys.argv:
        raw = json.load(open(sys.argv[sys.argv.index("--yahoo") + 1], encoding="utf-8"))
        print("Bitget 现货 rToken %d 个 (asof %s), 开始核对 Yahoo..." % (len(raw["spot"]), raw["asof"]), flush=True)
        res = check_yahoo(raw["spot"], set(raw["futures"]))
        out = {"asof_bitget": raw["asof"], "asof_yahoo": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
               "source": {"bitget_spot": SPOT_URL, "bitget_futures": FUT_URL, "yahoo": "v8/finance/chart"},
               "n_bitget": len(raw["spot"]), "n_supported": len(res["ok"]),
               "symbols": res["ok"], "skipped": res["skipped"]}
        path = __file__.rsplit("tools", 1)[0] + "data/rtoken_universe.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1, sort_keys=True)
        print("收录 %d / %d, 其中 %d 个有永续合约 -> %s"
              % (len(res["ok"]), len(raw["spot"]), sum(v["futures"] for v in res["ok"].values()), path))
    else:
        print(json.dumps(from_bitget(), ensure_ascii=False))
