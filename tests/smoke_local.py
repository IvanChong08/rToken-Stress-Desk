"""本机冒烟测试: Yahoo 取数 / MCP 健康检查 / Bitget (预期本机被屏蔽) / 调用日志哈希链。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from desk.provenance import CallLog, verify_log  # noqa: E402
from desk.sources import bitget, mcp, yahoo  # noqa: E402

LOG = os.path.join(ROOT, "data", "calls_smoke.jsonl")
log = CallLog(LOG)

df, p = yahoo.ohlcv("NVDA", "5y", "1d")
log.append(p)
print("[Yahoo]   ", p.label())
if df is not None:
    gap = (df["open"] / df["close"].shift(1) - 1).abs()
    print("           NVDA %d 根日线 %s ~ %s; |开盘跳空|>=10%%: %d 次"
          % (len(df), df.ts.iloc[0].date(), df.ts.iloc[-1].date(), int((gap >= 0.10).sum())))

c = mcp.MCPClient(timeout=15)
print("[MCP]      server:", c.connect(), "| 工具数:", len(c.list_tools()))
for prov in mcp.health_check(c, log=log):
    print("          ", ("OK  " if prov.ok else "FAIL"), prov.label())

_, p = bitget.candles("RNVDAUSDT", "spot", "1h", pages=1, timeout=8)
log.append(p)
print("[Bitget]  ", p.label(), "(本机预期失败, 需在 AWS 运行)" if not p.ok else "")

ok, n = verify_log(LOG)
print("[日志]     %s  %d 行, 哈希链 %s" % (LOG, n, "完整" if ok else "损坏"))
