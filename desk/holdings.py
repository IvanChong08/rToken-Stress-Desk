"""持仓表格 <-> Position 的转换。

抽出来是为了能单测: st.data_editor 是 canvas 组件, AppTest 和浏览器自动化都碰不到它,
表格里填的买入价、数量到底有没有正确变成仓位, 只能在这一层验证。
"""

from . import portfolio as pf

KIND_CN = {"leverage": "杠杆", "spot": "现货"}
KIND_EN = {v: k for k, v in KIND_CN.items()}
SIDE_CN = {"long": "做多", "short": "做空"}
SIDE_EN = {v: k for k, v in SIDE_CN.items()}


def _num(v):
    """表格里的值可能是 None / NaN / 空串 / 字符串数字。"""
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.lower() in ("none", "nan"):
        return None
    try:
        return float(s) or None
    except ValueError:
        return None


def position_rows(positions, prices):
    """Position -> 表格行 (买入价用字符串, 空着比 None 好看)。"""
    rows = []
    for p in positions:
        qty = p.qty
        if not qty and prices.get(p.symbol):
            qty = round(p.notional / prices[p.symbol], 6)   # 贵的票 4 位小数不够, 显示多留两位
        rows.append({
            "标的": p.symbol,
            "类型": KIND_CN.get(p.kind, "杠杆"),
            "方向": SIDE_CN.get(p.side, "做多"),
            "买入价": ("%.2f" % p.entry) if p.entry else "",
            "数量": float(qty) if qty else None,
            "名义 USDT": float(p.notional),
        })
    return rows


def parse_rows(rows, prices, auto, supported) -> tuple[list, list[str]]:
    """
    表格行 -> (Position 列表, 被跳过的行的说明)。auto=True 时名义价值由 数量 × 当前价 算出。
    跳过的行必须说清楚: 手动加一行却算不出名义价值时, 它原本会被静默丢掉 (09-17 Ivan 实测)。
    """
    out, skipped = [], []
    for r in rows:
        sym = str(r.get("标的") or "").strip().upper()
        if not sym:
            continue                                    # 表尾的空行, 正常
        if sym not in supported:
            skipped.append("%s 不在支持清单里" % sym)
            continue
        qty = _num(r.get("数量"))
        entry = _num(r.get("买入价"))
        px = prices.get(sym)
        notional = _num(r.get("名义 USDT")) or 0.0
        if auto and qty and px:
            notional = qty * px
        if notional <= 0:
            if auto and qty and not px:
                skipped.append("%s 取不到当前价, 算不出名义价值 —— 取消勾选「自动算」后直接填名义 USDT" % sym)
            elif not qty and not notional:
                skipped.append("%s 没填数量, 也没填名义 USDT" % sym)
            else:
                skipped.append("%s 名义价值算出来是 0" % sym)
            continue
        out.append(pf.Position(sym, SIDE_EN.get(str(r.get("方向")), "long"), notional,
                               KIND_EN.get(str(r.get("类型")), "leverage"), entry, px, qty))
    return out, skipped


def rows_to_positions(rows, prices, auto, supported):
    return parse_rows(rows, prices, auto, supported)[0]
