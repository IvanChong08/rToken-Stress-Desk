"""修改指令执行器的离线测试 (不调大模型): 指令正确执行, 编造的数值被拒绝。  python -X utf8 tests/test_understand.py"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from desk.portfolio import Account, Position  # noqa: E402
from desk.portfolio_idea import apply_ops, edit, mentioned_values  # noqa: E402

fails = 0


def summary(a):
    if a is None:
        return None
    return (round(a.usdt, 2), round(a.btc, 6), tuple(sorted((p.symbol, p.side, round(p.notional, 2)) for p in a.positions)))


def check(name, got, want):
    global fails
    ok = got == want
    fails += not ok
    print("%s  %-40s got=%s%s" % ("PASS" if ok else "FAIL", name, got, "" if ok else "\n      want=%s" % (want,)))


base = Account([Position("NVDA", "long", 15000), Position("MSTR", "long", 8000), Position("SPY", "short", 5000)],
               usdt=5000, btc=0.1)


def run(ops, text, price=80000):
    return apply_ops(base, ops, price, text)


# ---- 原话里的数值 ----
check("一半", 0.5 in mentioned_values("一半 NVDA 换成 SPY 空单"), True)
check("减三成 -> 0.3 和 0.7", {0.3, 0.7} <= {round(x, 6) for x in mentioned_values("MSTR 减三成")}, True)
check("1.5万", 15000 in mentioned_values("加 1.5万 TSLA"), True)
check("20%", 0.2 in mentioned_values("NVDA 减 20%"), True)

# ---- 正确执行 ----
a, notes, err = run([{"op": "move", "from_symbol": "NVDA", "from_side": "long", "to_symbol": "SPY", "to_side": "short",
                      "fraction": 0.5}], "一半 NVDA 换成 SPY 空单")
check("一半 NVDA 换成 SPY 空单", summary(a), (5000, 0.1, (("MSTR", "long", 8000), ("NVDA", "long", 7500), ("SPY", "short", 12500))))
check("改动说明", notes, ["NVDA 做多 减仓 7,500 USDT (15,000 → 7,500)", "SPY 做空 加仓 7,500 USDT (5,000 → 12,500)"])

a, _, _ = run([{"op": "scale", "symbol": "MSTR", "side": "long", "factor": 0.7}], "MSTR 减三成")
check("MSTR 减三成", summary(a)[2][0], ("MSTR", "long", 5600))

a, _, _ = run([{"op": "add", "symbol": "TSLA", "side": "long", "notional": 5000}], "再加 5000 TSLA 多单")
check("新开 TSLA", ("TSLA", "long", 5000) in summary(a)[2], True)

a, _, _ = run([{"op": "swap_collateral", "from": "BTC", "to": "USDT", "fraction": 0.5}], "一半 BTC 换成 USDT")
check("一半 BTC 换 USDT (价 80000)", summary(a)[:2], (9000, 0.05))

a, _, _ = run([{"op": "add_collateral", "asset": "USDT", "value": -2000}], "保证金减 2000u")
check("保证金减 2000u", summary(a)[0], 3000)

a, _, _ = run([{"op": "remove", "symbol": "SPY", "side": "short"}, {"op": "scale", "symbol": "MSTR", "factor": 0.5}],
              "平掉 SPY 空单再把 MSTR 砍半")
check("多条指令一起执行", summary(a)[2], (("MSTR", "long", 4000), ("NVDA", "long", 15000)))

a, _, _ = run([{"op": "move", "from_symbol": "MSTR", "to_symbol": "AAPL"}], "MSTR 全换成 AAPL")
check("move 默认全部 + 方向不变", summary(a)[2], (("AAPL", "long", 8000), ("NVDA", "long", 15000), ("SPY", "short", 5000)))

# ---- 必须拒绝 ----
a, _, err = run([{"op": "add", "symbol": "TSLA", "side": "long", "notional": 9000}], "再加一点 TSLA")
check("编造金额被拒", (a, "不在你的原话里" in (err or "")), (None, True))
a, _, err = run([{"op": "scale", "symbol": "NVDA", "factor": 0.35}], "NVDA 少一点")
check("编造比例被拒", (a, "不在你的原话里" in (err or "")), (None, True))
a, _, err = run([{"op": "remove", "symbol": "COIN"}], "平掉 COIN")
check("删除不存在的仓位", (a, err), (None, "当前组合里没有 COIN"))
a, _, err = run([{"op": "add", "symbol": "AMD", "notional": 5000}], "加 5000 AMD")
check("不支持的标的", (a, err), (None, "不支持的标的: AMD"))
a, _, err = run([{"op": "set_collateral", "asset": "SOL", "value": 2}], "保证金改成 2 SOL")
check("不支持的保证金", a is None and "暂不支持" in err, True)
a, _, err = run([{"op": "add_collateral", "asset": "ETH", "value": 2}], "保证金加上 2 ETH")
check("ETH 作保证金", (a.eth, err), (2.0, None))
ae = Account(base.positions, usdt=5000, btc=0.1, eth=2)
a, _, err = apply_ops(ae, [{"op": "swap_collateral", "from": "ETH", "to": "USDT", "fraction": 0.5}], 80000,
                      "一半 ETH 换成 USDT", eth_price=2500)
check("一半 ETH 换 USDT (价 2500)", (round(a.usdt), a.eth), (7500, 1.0))
a, _, err = run([{"op": "swap_collateral", "from": "ETH", "to": "USDT"}], "ETH 全部换成 USDT")
check("没有 ETH 时换 USDT 被拒", (a, "没有变化" in (err or "") or "现价" in (err or "")), (None, True))
a, _, err = run([{"op": "fly"}], "随便")
check("未知指令", (a, err), (None, "不认识的操作: fly"))
a, _, err = run([{"op": "remove", "symbol": "NVDA"}, {"op": "remove", "symbol": "MSTR"}, {"op": "remove", "symbol": "SPY"}], "全部平掉")
check("改完不合法 (没仓位) 被拒", (a, err), (None, "组合里至少要有一个仓位"))
check("原组合未被修改", summary(base), (5000, 0.1, (("MSTR", "long", 8000), ("NVDA", "long", 15000), ("SPY", "short", 5000))))

# ---- 规则解析遇到复杂句子要让给大模型 ----
check("复杂换仓交给大模型", edit("一半 NVDA 换成 SPY 空单", base, 80000), (None, "句子较复杂, 交给大模型理解"))
check("简单换标的仍走规则", summary(edit("把 MSTR 换成 AAPL", base, 80000)[0])[2][0], ("AAPL", "long", 8000))
check("BTC 全部换成 USDT 仍走规则", summary(edit("BTC 全部换成 USDT", base, 80000)[0])[:2], (13000, 0))
# 09-16 真实 Qwen 测试抓到: 规则把「砍半」套到 COIN 和 MSTR 两个标的上, COIN 没被平掉
check("多操作交给大模型", edit("平掉 SPY 再把 MSTR 砍半", base, 80000), (None, "一句话里有多个操作, 交给大模型理解"))
check("多标的 + 连接词交给大模型", edit("NVDA 改成 1万 然后 MSTR 改成 2000", base, 80000)[0], None)
check("多个标的同一操作仍走规则", summary(edit("MSTR 和 NVDA 都砍半", base, 80000)[0])[2][:2],
      (("MSTR", "long", 4000), ("NVDA", "long", 7500)))

# ---- 统称展开 / 预算等额分配 / 看不懂时要反问 (2026-09-16: Ivan 输入「0.1btc…3大巨头 加0.05保证金」得到无用提示) ----
from desk.portfolio_idea import clarify, expand_groups  # noqa: E402

t, note = expand_groups("用 0.1 BTC 等额买入七巨头")
check("七巨头展开", ("NVDA" in t and "AAPL" in t and "COIN" not in t, "七巨头" in note), (True, True))
check("没有统称就不动", expand_groups("做多 NVDA")[0], "做多 NVDA")

ask = clarify("0.1btc 现在市价分别买进做多3大巨头 加0.05保证金")
check("反问: 统称没有公认定义", "3大巨头" in (ask or "") and "没有公认定义" in (ask or ""), True)
check("反问: 单位不明", "0.05 BTC / ETH, 还是 0.05 USDT" in (ask or ""), True)
check("有金额时不反问", clarify("0.1 BTC 做保证金, 做多 NVDA 1万"), None)
check("等额买入不算缺金额", clarify("0.05 BTC 保证金, 用 0.1 BTC 等额买入 NVDA、AAPL"), None)
check("只给标的不给金额要反问", "没看到每笔的金额" in (clarify("做多 NVDA 和 AAPL") or ""), True)

# 09-16: 反问功能上线后一度把正常句子也拦下 (追问改仓、闲聊都被问「缺金额」) -> 钉住什么时候该反问
for _q, _want in [("0.1btc 现在市价分别买进做多3大巨头 加0.05保证金", True), ("一半 NVDA 换成 SPY 空单", False),
                  ("英伟达最新新闻是什么", False), ("做多 NVDA 和 AAPL", True), ("把 MSTR 砍半呢", False),
                  ("BTC 全部换成 USDT 呢", False), ("保证金再加 3000u 呢", False),
                  ("0.05 BTC 保证金, 用 0.1 BTC 等额买入 NVDA、AAPL", False)]:
    check("反问? %s" % _q[:24], bool(clarify(_q)), _want)

# allocate: 0.1 BTC (价 80000) = 8000 USDT, 两只各 4000
a, notes, err = run([{"op": "allocate", "budget_asset": "BTC", "budget": 0.1,
                      "symbols": ["AAPL", "TSLA"], "side": "long"}], "用 0.1 BTC 等额买入 AAPL、TSLA")
check("预算等额分配", {x for x in summary(a)[2] if x[0] in ("AAPL", "TSLA")}, {("AAPL", "long", 4000), ("TSLA", "long", 4000)})
a, _, err = run([{"op": "allocate", "budget_asset": "BTC", "budget": 0.3, "symbols": ["AAPL"]}], "用 0.1 BTC 买 AAPL")
check("编造预算被拒", (a, "不在你的原话里" in (err or "")), (None, True))
a, _, err = run([{"op": "allocate", "budget_asset": "SOL", "budget": 0.1, "symbols": ["AAPL"]}], "用 0.1 SOL 买 AAPL")
check("不支持的预算单位", (a, "不能作预算单位" in (err or "")), (None, True))

# ---- 按预算等额分配的整句解析 (09-16: Qwen 处理这句要 39 秒会超时, 必须纯规则搞定) ----
from desk.portfolio_idea import parse_allocate  # noqa: E402

a, note = parse_allocate("0.05 BTC 做保证金, 用 0.1 BTC 等额买入七巨头", btc_price=80000, eth_price=2500)
check("七巨头等额买入 (0.1 BTC = 8000U / 7)", (round(a.btc, 4), len(a.positions), round(a.positions[0].notional, 2)),
      (0.05, 7, 1142.86))
check("说明里写清换算", ("8,000" in note and "七巨头" in note), True)
a, _ = parse_allocate("2万U 做保证金, 用 1万U 等额买入 NVDA、TSLA")
check("USDT 预算等额买入", (a.usdt, sorted((p.symbol, p.notional) for p in a.positions)),
      (20000.0, [("NVDA", 5000.0), ("TSLA", 5000.0)]))
a, _ = parse_allocate("0.05 BTC 保证金, 用 0.1 BTC 分别做空 SPY、QQQ", btc_price=80000)
check("分别做空", sorted((p.symbol, p.side, round(p.notional)) for p in a.positions),
      [("QQQ", "short", 4000), ("SPY", "short", 4000)])
check("没有等额词就不走这条路", parse_allocate("0.1 BTC 做保证金, 做多 NVDA 1万", btc_price=80000)[0], None)
check("没有 BTC 现价时不乱算", parse_allocate("用 0.1 BTC 等额买入 NVDA、AAPL", btc_price=0)[0], None)

print("\n%s" % ("全部通过" if not fails else "%d 项失败" % fails))
sys.exit(1 if fails else 0)
