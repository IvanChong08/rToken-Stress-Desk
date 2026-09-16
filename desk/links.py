"""
「去 Bitget 调整」: 调整方案 -> 每只标的要改多少 + 对应的 Bitget 交易页面链接。

只做导航, 不下单、不需要 API key、不预填数量 (Bitget 网页是否支持预填参数未核实)。
所以每一步都写清楚「从多少调到多少」, 用户到 Bitget 页面后自己操作。

网址格式 2026-09-16 从 AWS 实测: 11 只标的的现货和永续合约页面全部返回实时价格 (og:title);
不存在的代码页面也返回 200 但价格为「--」, 所以判断依据是价格而不是状态码。
MSTR 永续合约页面标题显示为 MSTRRWAUSDT (网址 futures/usdt/MSTRUSDT 仍可打开并显示价格)。
"""
from __future__ import annotations

from .portfolio import Account

BITGET = "https://www.bitget.com"
VERIFIED_AT = "2026-09-16"
NAME_NOTES = {"MSTR": "合约页面上的名称是 MSTRRWAUSDT"}


def spot_url(symbol: str) -> str:
    return "%s/spot/R%sUSDT" % (BITGET, symbol)


def futures_url(symbol: str) -> str:
    return "%s/futures/usdt/%sUSDT" % (BITGET, symbol)


def adjustment_steps(before: Account, after: Account, tol: float = 0.5) -> list[dict]:
    """
    对比调整前后的组合, 列出每一处变化。金额变化小于 tol USDT 视为不变。
    返回 [{kind, symbol, side, from, to, delta, text, spot_url, futures_url, note}]。
    """
    steps = []
    b = {(p.symbol, p.side): p.notional for p in before.positions}
    a = {(p.symbol, p.side): p.notional for p in after.positions}
    kinds = {(p.symbol, p.side): p.kind for p in list(before.positions) + list(after.positions)}
    for key in sorted(set(b) | set(a)):
        x, y = b.get(key, 0.0), a.get(key, 0.0)
        if abs(y - x) < tol:
            continue
        sym, side = key
        spot = kinds.get(key) == "spot"
        # 现货没有多空, 也不该把人往永续合约页面带 —— 那是他没持有的产品
        side_cn = "现货" if spot else ("做多" if side == "long" else "做空")
        less, more = ("卖出", "买入") if spot else ("减仓", "加仓")
        if y == 0:
            text = "%s %s 全部卖出 %s USDT" % (sym, side_cn, format(round(x), ",")) if spot else                    "平掉 %s %s 全部 %s USDT" % (sym, side_cn, format(round(x), ","))
        elif y < x:
            text = "%s %s %s %s USDT (%s → %s)" % (sym, side_cn, less, format(round(x - y), ","), format(round(x), ","), format(round(y), ","))
        else:
            text = "%s %s %s %s USDT (%s → %s)" % (sym, side_cn, more, format(round(y - x), ","), format(round(x), ","), format(round(y), ","))
        steps.append({"kind": "position", "symbol": sym, "side": side, "from": x, "to": y, "delta": y - x, "text": text,
                      "spot_url": spot_url(sym), "futures_url": "" if spot else futures_url(sym),
                      "note": NAME_NOTES.get(sym, "")})
    if abs(after.btc - before.btc) > 1e-9:
        steps.append({"kind": "collateral", "symbol": "BTC", "side": "", "from": before.btc, "to": after.btc,
                      "delta": after.btc - before.btc,
                      "text": "保证金 BTC %.4g → %.4g 个 (%s)" % (before.btc, after.btc,
                                                                "在 Bitget 买入或划入" if after.btc > before.btc else "在 Bitget 卖出换成 USDT"),
                      "spot_url": "%s/spot/BTCUSDT" % BITGET, "futures_url": "", "note": ""})
    if abs(after.eth - before.eth) > 1e-9:
        steps.append({"kind": "collateral", "symbol": "ETH", "side": "", "from": before.eth, "to": after.eth,
                      "delta": after.eth - before.eth,
                      "text": "保证金 ETH %.4g → %.4g 个 (%s)" % (before.eth, after.eth,
                                                                "在 Bitget 买入或划入" if after.eth > before.eth else "在 Bitget 卖出换成 USDT"),
                      "spot_url": "%s/spot/ETHUSDT" % BITGET, "futures_url": "", "note": ""})
    if abs(after.usdt - before.usdt) >= tol:
        # 全是现货的账户没有「保证金」, USDT 只是卖出后留在手上的钱
        spot_only = all(p.kind == "spot" for p in list(before.positions) + list(after.positions))
        label = "卖出后 USDT 余额" if spot_only else "保证金 USDT"
        steps.append({"kind": "collateral", "symbol": "USDT", "side": "", "from": before.usdt, "to": after.usdt,
                      "delta": after.usdt - before.usdt,
                      "text": "%s %s → %s" % (label, format(round(before.usdt), ","), format(round(after.usdt), ",")),
                      "spot_url": "", "futures_url": "", "note": ""})
    return steps
