"""组合规则解析 + 追问修改的离线测试。  python -X utf8 tests/test_portfolio_idea.py"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from desk.portfolio import Account, Position  # noqa: E402
from desk.portfolio_idea import edit, parse_rules  # noqa: E402

fails = 0


def summary(a):
    if a is None:
        return None
    return (round(a.usdt, 2), round(a.btc, 6), tuple(sorted((p.symbol, p.side, round(p.notional, 2)) for p in a.positions)))


def check(name, got, want):
    global fails
    ok = got == want
    fails += not ok
    print("%s  %-44s got=%s%s" % ("PASS" if ok else "FAIL", name, got, "" if ok else "\n      want=%s" % (want,)))


CASES = [
    ("0.1 BTC 和 5000 USDT 做保证金，做多 NVDA 15000U、MSTR 8000U、COIN 5000U，做空 SPY 5000U",
     (5000, 0.1, (("COIN", "long", 5000), ("MSTR", "long", 8000), ("NVDA", "long", 15000), ("SPY", "short", 5000)))),
    ("保证金 2万U, 多英伟达 1.5万, 空纳指 1万",
     (20000, 0, (("NVDA", "long", 15000), ("QQQ", "short", 10000)))),
    ("I have 0.5 BTC as margin, long RNVDAUSDT 30k and long TSLA 10k",
     (0, 0.5, (("NVDA", "long", 30000), ("TSLA", "long", 10000)))),
    ("3000u 保证金 做多 MSTR 6000u", (3000, 0, (("MSTR", "long", 6000),))),
    ("做多 NVDA 10000U", None),                     # 没有保证金
    ("1 BTC 保证金 做多狗狗币 5000U", None),          # 不支持的标的
]
for text, want in CASES:
    check(text[:40], summary(parse_rules(text)), want)

base = Account([Position("NVDA", "long", 15000), Position("MSTR", "long", 8000), Position("SPY", "short", 5000)],
               usdt=5000, btc=0.1)
a, note = edit("把 MSTR 砍半呢", base, 80000)
check("砍半", summary(a), (5000, 0.1, (("MSTR", "long", 4000), ("NVDA", "long", 15000), ("SPY", "short", 5000))))
a, note = edit("去掉 SPY 的空单", base, 80000)
check("去掉", summary(a), (5000, 0.1, (("MSTR", "long", 8000), ("NVDA", "long", 15000))))
a, note = edit("BTC 全部换成 USDT", base, 80000)          # 0.1 x 80000 = 8000 -> usdt 13000
check("BTC 换 USDT", summary(a), (13000, 0, (("MSTR", "long", 8000), ("NVDA", "long", 15000), ("SPY", "short", 5000))))
a, note = edit("NVDA 减到 1万", base, 80000)
check("改金额", summary(a), (5000, 0.1, (("MSTR", "long", 4000 * 2), ("NVDA", "long", 10000), ("SPY", "short", 5000))))
a, note = edit("保证金再加 2000u", base, 80000)
check("加保证金", summary(a), (7000, 0.1, (("MSTR", "long", 8000), ("NVDA", "long", 15000), ("SPY", "short", 5000))))
check("原组合未被修改", summary(base), (5000, 0.1, (("MSTR", "long", 8000), ("NVDA", "long", 15000), ("SPY", "short", 5000))))
a, note = edit("今天天气怎么样", base, 80000)
check("无关文字", a, None)

# 「把 A 换成 B」
a, note = edit("把 MSTR 换成 AAPL 呢", base, 80000)
check("换标的", summary(a), (5000, 0.1, (("AAPL", "long", 8000), ("NVDA", "long", 15000), ("SPY", "short", 5000))))
a, note = edit("MSTR 全部换成英伟达", base, 80000)       # NVDA 已有同方向仓位 -> 合并
check("换标的并入已有仓位", summary(a), (5000, 0.1, (("NVDA", "long", 23000), ("SPY", "short", 5000))))
check("「NVDA 改成 1万」仍是改金额", summary(edit("NVDA 改成 1万", base, 80000)[0]),
      (5000, 0.1, (("MSTR", "long", 8000), ("NVDA", "long", 10000), ("SPY", "short", 5000))))

# 不支持的资产: 明确说出来, 不能悄悄忽略
from desk.portfolio_idea import unsupported_mentions, unsupported_message  # noqa: E402
check("没有 ETH 时 ETH 换 USDT 不改组合", edit("ETH 全部换成 USDT 呢", base, 80000, 2500)[0], None)
check("ETH 已支持作保证金, 不再报不支持", unsupported_mentions("ETH 全部换成 USDT 呢"), [])
check("识别不支持的币", unsupported_mentions("SOL 和狗狗币做保证金"), ["SOL", "DOGE"])
# GOOG 是真实存在的代码, 但 Bitget 只上了 GOOGL 的 rToken —— 这种「差一个字母」的才是真会踩的坑
check("识别不支持的股票代码", unsupported_mentions("做多 GOOG 5000U 和 NVDA"), ["GOOG"])
check("扩容后 AMD 已支持, 不再误报", unsupported_mentions("做多 AMD 5000U 和 NVDA"), [])
check("支持的标的与 USDT/BTC 不误报", unsupported_mentions("0.1 BTC 5000 USDT 做多 NVDA、RMSTRUSDT"), [])
check("英文小写词不误报", unsupported_mentions("long NVDA 10k and short SPY as margin"), [])
check("SOL 说明文字", unsupported_message("SOL 全部换成 USDT 呢"), "「SOL」暂不支持: 保证金目前只支持 USDT、BTC 和 ETH, 仓位只支持美股 rToken")
check("ETH 不能作仓位", "只能作保证金" in (unsupported_message("做多 ETH 5000U") or ""), True)
check("ETH 作保证金不报错", unsupported_message("0.5 ETH 做保证金"), None)

# ETH 抵押品 (2026-09-16 加入)
be = Account(base.positions, usdt=5000, btc=0.1, eth=2)
a, note = edit("ETH 全部换成 USDT", be, 80000, 2500)          # 2 x 2500 = 5000 -> USDT 10000, BTC 不动
check("ETH 换 USDT", (summary(a)[:2], a.eth), ((10000, 0.1), 0.0))
a = parse_rules("0.5 ETH 和 3000U 做保证金, 做多 NVDA 1万")
check("解析 ETH 保证金", (summary(a), a.eth), ((3000, 0, (("NVDA", "long", 10000),)), 0.5))

print("\n%s" % ("全部通过" if not fails else "%d 项失败" % fails))
sys.exit(1 if fails else 0)
