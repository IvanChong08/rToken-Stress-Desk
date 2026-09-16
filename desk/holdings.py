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
            qty = round(p.notional / prices[p.symbol], 4)
        rows.append({
            "标的": p.symbol,
            "类型": KIND_CN.get(p.kind, "杠杆"),
            "方向": SIDE_CN.get(p.side, "做多"),
            "买入价": ("%.2f" % p.entry) if p.entry else "",
            "数量": float(qty) if qty else None,
            "名义 USDT": float(p.notional),
        })
    return rows


def rows_to_positions(rows, prices, auto, supported):
    """表格行 -> Position 列表。auto=True 时名义价值由 数量 × 当前价 算出。"""
    out = []
    for r in rows:
        sym = str(r.get("标的") or "").strip().upper()
        if sym not in supported:
            continue
        qty = _num(r.get("数量"))
        entry = _num(r.get("买入价"))
        px = prices.get(sym)
        notional = _num(r.get("名义 USDT")) or 0.0
        if auto and qty and px:
            notional = qty * px
        if notional <= 0:
            continue
        out.append(pf.Position(sym, SIDE_EN.get(str(r.get("方向")), "long"), notional,
                               KIND_EN.get(str(r.get("类型")), "leverage"), entry, px, qty))
    return out
