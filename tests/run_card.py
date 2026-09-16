"""完整研究任务的离线演示: 一句话想法 -> 结论卡 (没有大模型 key 时自动走模板)。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from desk import card  # noqa: E402
from desk.idea import parse  # noqa: E402
from desk.provenance import CallLog, verify_log  # noqa: E402

LOG = os.path.join(ROOT, "data", "calls_card.jsonl")
log = CallLog(LOG)

for text in ("周五收盘前我想 3 倍做多 NVDA rToken 过周末", "MSTR 5倍做多 过周末"):
    idea, provs, how = parse(text)
    for p in provs:
        log.append(p)
    print("=" * 100)
    print("输入:", text)
    print("解析:", idea.to_dict() if idea else None, "|", how)
    if idea is None:
        continue
    c = card.build_card(idea, log=log)
    print("\n【%s】  样本分级: %s" % (c.headline, c.tier_text))
    for f in c.facts:
        print("  %-34s %-32s 来源: %s" % (f.label, f.display, " || ".join(s[:70] for s in f.sources)))
    print("\n结论 (%s):\n  %s" % (c.narrative_by, c.narrative))
    print("\n提示:")
    for w in c.warnings:
        print("  -", w)
    print("\n风险规则:", card.RISK_RULE)

ok, n = verify_log(LOG)
print("\n调用日志 %s: %d 行, 哈希链 %s" % (LOG, n, "完整" if ok else "损坏"))
