"""调整步骤 + Bitget 链接的离线测试。  python -X utf8 tests/test_links.py"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from desk import links, suggest  # noqa: E402
from desk.portfolio import Account, Position  # noqa: E402

fails = 0


def check(name, got, want):
    global fails
    ok = got == want
    fails += not ok
    print("%s  %-36s got=%r%s" % ("PASS" if ok else "FAIL", name, got, "" if ok else "  want=%r" % (want,)))


check("现货网址", links.spot_url("NVDA"), "https://www.bitget.com/spot/RNVDAUSDT")
check("合约网址", links.futures_url("NVDA"), "https://www.bitget.com/futures/usdt/NVDAUSDT")

base = Account([Position("NVDA", "long", 15000), Position("MSTR", "long", 8000), Position("SPY", "short", 5000)],
               usdt=5000, btc=0.1)

# 砍半 MSTR: 只有一步, 8000 -> 4000
s = links.adjustment_steps(base, suggest.halve_position(base, "MSTR", "long"))
check("砍半只有一步", len(s), 1)
check("砍半文字", s[0]["text"], "MSTR 做多 减仓 4,000 USDT (8,000 → 4,000)")
check("MSTR 名称提示", s[0]["note"], "合约页面上的名称是 MSTRRWAUSDT")

# 同比例缩到 30%: 三步, 空单也要减
s = links.adjustment_steps(base, suggest.scaled(base, 0.3))
check("缩仓三步", [x["symbol"] for x in s], ["MSTR", "NVDA", "SPY"])
check("空单减仓文字", s[2]["text"], "SPY 做空 减仓 3,500 USDT (5,000 → 1,500)")

# BTC 换 USDT (价格 80000): 两步保证金变化, 没有仓位变化
s = links.adjustment_steps(base, suggest.swap_btc_to_usdt(base, 80000))
check("换保证金两步", [x["symbol"] for x in s], ["BTC", "USDT"])
check("USDT 文字", s[1]["text"], "保证金 USDT 5,000 → 13,000")

# 去掉一笔 + 新增一笔
after = Account([Position("NVDA", "long", 15000), Position("MSTR", "long", 8000), Position("QQQ", "short", 2000)],
                usdt=5000, btc=0.1)
s = links.adjustment_steps(base, after)
check("平掉 + 新开", [x["text"] for x in s], ["QQQ 做空 加仓 2,000 USDT (0 → 2,000)", "平掉 SPY 做空 全部 5,000 USDT"])
check("没有变化", links.adjustment_steps(base, base), [])

# ETH 保证金 2 个按 2500 全换 USDT
be = Account(base.positions, usdt=5000, btc=0.1, eth=2)
s = links.adjustment_steps(be, suggest.swap_crypto_to_usdt(be, 80000, 2500))
check("BTC+ETH 换 USDT 三步", [x["symbol"] for x in s], ["BTC", "ETH", "USDT"])
check("ETH 链接", s[1]["spot_url"], "https://www.bitget.com/spot/ETHUSDT")
check("USDT 5000 + 8000 + 5000", s[2]["text"], "保证金 USDT 5,000 → 18,000")

print("\n%s" % ("全部通过" if not fails else "%d 项失败" % fails))
sys.exit(1 if fails else 0)
