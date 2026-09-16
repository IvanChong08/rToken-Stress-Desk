"""大模型数字核对器的离线测试: 合法引用要放行, 编造的数字必须拦下。  python -X utf8 tests/test_verifier.py

背景 (2026-09-16): 单笔结论被误判弃用 —— Qwen 引用了提示词里给它的样本分级说明「样本 10–29」和标签里的「5 年」,
核对器原先只认事实表 display。修复后放行 label 与 extra, 这里钉住「放宽不等于放掉编造」。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from desk.analysis.weekend_gap import TIER_TEXT  # noqa: E402
from desk.card import Fact, unverified_numbers  # noqa: E402

facts = [Fact("on_worst", "5 年最差单日不利变动", -0.1817, "-18.17% (2025-01-27)", []),
         Fact("lev_cap", "建议杠杆上限", 3.7, "3.7 倍", [])]
tier = TIER_TEXT["case_study"]
CASES = [
    ("引用事实与标签: 5 年最差 -18.17%, 上限 3.7 倍", [], ()),
    ("引用样本分级: 样本 10–29 只能看案例", [], (tier,)),
    ("没传分级说明时 10 和 29 仍算未核实", ["10", "29"], ()),
    ("编造: 历史胜率 62%, 目标价 250", ["62", "250"], (tier,)),
    ("编造: 最差单日 -21.5%", ["-21.5"], (tier,)),
]
fails = 0
for text, want, extra in CASES:
    got = unverified_numbers(text, facts, extra=extra)
    ok = got == want
    fails += not ok
    print("%s  %-40s -> %s%s" % ("PASS" if ok else "FAIL", text, got, "" if ok else "  (期望 %s)" % want))
print("\n%s" % ("全部通过" if not fails else "%d 项失败" % fails))
sys.exit(1 if fails else 0)
