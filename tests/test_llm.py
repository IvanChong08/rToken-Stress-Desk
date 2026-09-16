"""
Qwen / Gemini 连通测试。不会打印 key。

    python -X utf8 tests/test_llm.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from desk import llm  # noqa: E402
from desk.idea import parse_llm  # noqa: E402

for p, cfg in llm.PROVIDERS.items():
    found = [n for n in cfg["key_envs"] if os.environ.get(n)]
    print("%-6s key: %s" % (p, ("已设置 (%s, 长度 %d)" % (found[0], len(os.environ[found[0]]))) if found else "未设置"))

prov = llm.available_provider()
if prov is None:
    print("\n没有可用的 key。PowerShell 里执行: setx BITGET_QWEN_API_KEY \"你的key\"  然后重开终端")
    sys.exit(1)

text, p = llm.chat([{"role": "user", "content": "只回复两个字: 收到"}], provider=prov)
print("\n[连通] %s" % p.label())
print("       回复: %r" % text)

idea, p2, err = parse_llm("周五收盘前我想 3 倍做多 NVDA rToken 过周末", provider=prov)
print("\n[解析] %s" % p2.label())
print("       结果: %s  错误: %s" % (idea.to_dict() if idea else None, err))
