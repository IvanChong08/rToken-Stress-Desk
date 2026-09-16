"""规则解析器的离线测试 (不需要大模型 key)。  python -X utf8 tests/test_idea_rules.py"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from desk.idea import parse_rules  # noqa: E402

CASES = [
    ("周五收盘前我想 3 倍做多 NVDA rToken 过周末", ("NVDA", "long", 3.0, "weekend")),
    ("英伟达财报前 5x 做多", ("NVDA", "long", 5.0, "overnight")),
    ("想做空特斯拉 2倍 持有过周末", ("TSLA", "short", 2.0, "weekend")),
    ("buy SPY 10x over the weekend", ("SPY", "long", 10.0, "weekend")),
    ("MSTR 盘后 short 4X", ("MSTR", "short", 4.0, "overnight")),
    ("纳指 ETF 做多", ("QQQ", "long", 1.0, "weekend")),
    ("我想买点狗狗币", None),               # 不支持的标的
    ("3 倍做多 NVDA 50倍", ("NVDA", "long", 3.0, "weekend")),  # 取第一个杠杆数字
    ("做多 NVDA 30 倍", None),               # 杠杆超范围
]

fails = 0
for text, expect in CASES:
    got = parse_rules(text)
    got_t = None if got is None else (got.symbol, got.side, got.leverage, got.scenario)
    ok = got_t == expect
    fails += not ok
    print("%s  %-36s -> %s%s" % ("PASS" if ok else "FAIL", text, got_t, "" if ok else "   (期望 %s)" % (expect,)))
print("\n%d/%d 通过" % (len(CASES) - fails, len(CASES)))
sys.exit(1 if fails else 0)
