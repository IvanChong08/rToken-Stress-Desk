"""rToken 周一盘前价格 vs Yahoo 正股盘前价格: rToken 是否只是在跟正股盘前。"""
import sys

import pandas as pd
import requests

sys.path.insert(0, r"D:\ivan-agent\rtoken-stress-desk")
from desk.card import load_rtoken_cache  # noqa: E402

NY = "America/New_York"
for s in ["NVDA", "TSLA", "MSTR", "SPY", "AAPL"]:
    j = requests.get("https://query1.finance.yahoo.com/v8/finance/chart/" + s,
                     params={"range": "6mo", "interval": "1h", "includePrePost": "true"},
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=20).json()["chart"]["result"][0]
    q = j["indicators"]["quote"][0]
    y = pd.DataFrame({"ts": pd.to_datetime(j["timestamp"], unit="s", utc=True), "close": q["close"],
                      "open": q["open"]}).dropna()
    y["ny"] = y.ts.dt.tz_convert(NY)
    rt, _ = load_rtoken_cache(s)
    rt = rt.set_index("ts").sort_index()
    mons = sorted({d.date() for d in y.ny if d.weekday() == 0 and d >= pd.Timestamp("2026-06-01", tz=NY)})
    rows = []
    for m in mons:
        ym = y[(y.ny.dt.date == m)]
        pre = ym[(ym.ny.dt.hour >= 4) & (ym.ny.dt.hour < 9)]
        reg = ym[ym.ny.dt.hour >= 9]
        if pre.empty or reg.empty:
            continue
        for _, b in pre.iterrows():
            t = b.ts
            if t not in rt.index:
                continue
            rows.append({"mon": str(m), "ny_hour": b.ny.strftime("%H:%M"), "yahoo_pre_open": b.open,
                         "rtoken_open": float(rt.loc[t, "open"])})
    d = pd.DataFrame(rows)
    if d.empty:
        print(s, "无 Yahoo 盘前小时数据")
        continue
    d["diff%"] = 100 * (d.rtoken_open / d.yahoo_pre_open - 1)
    print("%s: Yahoo 盘前小时 %d 个 (周一 %d 个) · |rToken - 正股盘前| 中位 %.3f%% 最大 %.3f%%" % (
        s, len(d), d.mon.nunique(), d["diff%"].abs().median(), d["diff%"].abs().max()))
    print("  Yahoo 1h 盘前时间戳样例:", sorted(d.ny_hour.unique()))
