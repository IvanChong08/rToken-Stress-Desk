"""
bitget-signal 官方研究 Skill 的数据后端 (MCP, HTTP transport, 免 key)。

地址取自官方安装器 `@bitget-ai/bitget-signal` 1.2.0 的 scripts/install.js (MCP_URL),
CHANGELOG 1.2.0 也确认该地址不变。

⚠️ 2026-09-14 多次实测: 依赖外部数据源的工具 (恐慌贪婪 / Yahoo / CoinGecko / 利率 / 宏观 / 新闻 ...)
大多超时或返回空错误, 本机和 AWS 结果一致 -> 服务端对外取数故障。
而 Skill 提示词要求工具失败时只对用户说「数据暂时不可用」, 故障对终端用户不可见。
所以这里: 每次调用都判定成功/失败并记录耗时; 提供健康检查, 让界面如实显示哪些工具可用。
"""
from __future__ import annotations

import json
import time
from typing import Any

import requests

from ..provenance import Provenance

URL = "https://datahub.noxiaohao.com/mcp"

HEALTH_PROBES: list[tuple[str, dict]] = [
    ("crypto_derivatives", {"action": "price", "symbol": "BTC/USDT"}),
    ("technical_analysis", {"action": "atr", "symbol": "BTC/USDT", "timeframe": "1d"}),
    ("sentiment_index", {"action": "current"}),
    ("global_assets", {"action": "price", "symbol": "SPY"}),
    ("crypto_market", {"action": "global"}),
    ("rates_yields", {"action": "fed_funds"}),
    ("news_feed", {"action": "latest", "limit": 3}),
]


def _looks_failed(obj: Any) -> str | None:
    """工具「正常返回」但内容其实是失败的几种形态 (都在实测里见过)。"""
    if isinstance(obj, dict):
        if "error" in obj:
            return "error field: %r" % str(obj["error"])[:80]
        bad = [k for k in obj if k.endswith("_error")]
        if bad:
            return "error field: %s" % bad[0]
        vals = [v for v in obj.values() if not isinstance(v, str)]
        if vals and all(v is None or (isinstance(v, dict) and "error" in v) for v in vals):
            return "all values empty or errored"
    if isinstance(obj, list) and obj and all(
            isinstance(x, dict) and "error" in x and not x.get("items") for x in obj):
        return "all feeds errored/empty"
    return None


class MCPClient:
    def __init__(self, url: str = URL, timeout: float = 25.0):
        self.url, self.timeout = url, timeout
        self.session_id: str | None = None
        self.server_info: dict | None = None
        self._id = 0

    def _post(self, method: str, params: dict | None = None, notify: bool = False):
        body: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if not notify:
            self._id += 1
            body["id"] = self._id
        if params is not None:
            body["params"] = params
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        r = requests.post(self.url, json=body, headers=headers, timeout=self.timeout)
        self.session_id = r.headers.get("mcp-session-id", self.session_id)
        if "event-stream" in r.headers.get("content-type", ""):
            ds = [l[5:].strip() for l in r.text.splitlines() if l.startswith("data:") and l[5:].strip()]
            return json.loads(ds[-1]) if ds else None
        return r.json() if r.text.strip() else None

    def connect(self) -> dict:
        res = self._post("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                        "clientInfo": {"name": "rtoken-stress-desk", "version": "0.1"}})
        self.server_info = ((res or {}).get("result") or {}).get("serverInfo")
        self._post("notifications/initialized", notify=True)
        return self.server_info or {}

    def list_tools(self) -> list[dict]:
        return ((self._post("tools/list", {}) or {}).get("result") or {}).get("tools", [])

    def call(self, tool: str, **args):
        """返回 (解析后的数据 | None, Provenance)。"""
        endpoint = "%s.%s" % (tool, args.get("action", ""))
        t0 = time.time()
        try:
            res = self._post("tools/call", {"name": tool, "arguments": args}) or {}
            result = res.get("result") or {}
            txt = " ".join(c.get("text", "") for c in result.get("content", []) if isinstance(c, dict))
            if res.get("error") or result.get("isError") or txt.startswith("Error executing tool"):
                raise RuntimeError(txt[:160] or str(res.get("error"))[:160])
            try:
                data = json.loads(txt)
            except ValueError:
                data = txt
            why = _looks_failed(data)
            if why:
                raise RuntimeError(why)
            n = len(data) if isinstance(data, list) else None
            return data, Provenance("mcp", endpoint, args, time.time(),
                                    int((time.time() - t0) * 1000), True, n_records=n)
        except Exception as e:
            return None, Provenance("mcp", endpoint, args, time.time(),
                                    int((time.time() - t0) * 1000), False,
                                    error="%s: %s" % (type(e).__name__, str(e)[:160]))


def health_check(client: MCPClient, probes=HEALTH_PROBES, log=None, parallel: bool = True) -> list[Provenance]:
    """
    默认并行探测: 串行时每个坏掉的工具都要等满超时, 实测 7 个探测首次加载要 67 秒;
    并行后总耗时 ≈ 单个超时。日志在全部完成后按顺序写入 (哈希链必须串行)。
    """
    if parallel:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=len(probes)) as ex:
            out = list(ex.map(lambda pa: client.call(pa[0], **pa[1])[1], probes))
    else:
        out = [client.call(tool, **args)[1] for tool, args in probes]
    if log is not None:
        for prov in out:
            log.append(prov)
    return out
