"""
Bitget 官方美股数据 MCP (`bitget-mcp-server`, HTTP 传输, 免账号免 API Key)。

和 `desk/sources/mcp.py` 里那个 `bitget-signal` 不是同一个东西:
  bitget-signal      加密货币的宏观 / 情绪 / 技术面 Skill (datahub.noxiaohao.com), 实测 7 个探测只活 2 个
  bitget-mcp-server  美股 / ETF 数据 (agent.bitget.com), 67 个数据入口: 加密 40 · 美股 21 · ETF 3 · 新闻 1 · 情绪 2

两个坑 (2026-09-17 实测):
1. **Cloudflare 按 User-Agent 拦**: 默认的 python-requests UA 直接 403 (Error 1010),
   换成浏览器 UA 就 200。所以这里写死一个浏览器 UA。
2. **马来西亚本地网络连不上 agent.bitget.com** (和 api.bitget.com 一样被运营商挡),
   AWS 与 Streamlit Cloud 正常 -> 所有调用都必须能优雅失败, 界面上如实显示「本机网络不可达」,
   不能让整页崩掉。

协议: initialize -> notifications/initialized -> tools/call。响应是 SSE (text/event-stream),
真正的 JSON 在 "data: " 那一行。
"""
from __future__ import annotations

import json
import time

import requests

from ..provenance import Provenance

URL = "https://agent.bitget.com/mcp"
# Cloudflare 会拦默认的 python-requests UA (Error 1010), 必须伪装成浏览器
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"


def _parse_sse(text: str) -> dict:
    """
    解析 SSE 响应。长响应会被拆成多行 data:, 只读第一行就会撞上
    JSONDecodeError: Unterminated string (2026-09-17 实测: guide 和 price_target 都超过一行)。
    先把所有 data: 行拼起来, 直接相连拼不出来再按 SSE 规范用换行拼一次。
    """
    i = text.find("data:")
    if i < 0:
        return json.loads(text) if text.strip() else {}
    payload = text[i + 5:].lstrip(" ")
    # SSE 事件以空行结束; 但 JSON 内部可能带真实换行 (分析师评论就有中文换行),
    # 所以先按「到空行为止」试一次, 不行再拿整段 —— 只按行过滤 data: 前缀会把续行丢掉。
    end = payload.find("\n\n")
    for candidate in ([payload[:end]] if end >= 0 else []) + [payload]:
        try:
            return json.loads(candidate)
        except ValueError:
            continue
    raise ValueError("SSE 响应解析不了 (%d 字节)" % len(payload))


class StockMCP:
    def __init__(self, url: str = URL, timeout: float = 25.0):
        self.url = url
        self.timeout = timeout
        self.session_id: str | None = None
        self.server: dict | None = None

    def _post(self, method: str, params: dict | None = None, notify: bool = False):
        body = {"jsonrpc": "2.0", "method": method}
        if not notify:
            body["id"] = 1
        if params is not None:
            body["params"] = params
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                   "User-Agent": UA}
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        r = requests.post(self.url, json=body, headers=headers, timeout=self.timeout)
        r.raise_for_status()
        # 响应头没声明 charset, requests 会按 ISO-8859-1 解码 -> 中文评级变成 "å¢æ" (2026-09-17 实测)
        r.encoding = "utf-8"
        self.session_id = r.headers.get("mcp-session-id", self.session_id)
        if notify:
            return {}
        return _parse_sse(r.text)

    def connect(self) -> dict:
        res = self._post("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                        "clientInfo": {"name": "rtoken-stress-desk", "version": "1.0"}})
        self.server = (res.get("result") or {}).get("serverInfo")
        self._post("notifications/initialized", {}, notify=True)
        return self.server or {}

    def query(self, entry_id: str, params: dict | None = None) -> tuple[list | None, Provenance]:
        """执行一个数据入口, 返回 (results | None, 来源记录)。"""
        t0 = time.time()
        meta = {"entry_id": entry_id, **(params or {})}
        try:
            if self.session_id is None:
                self.connect()
            res = self._post("tools/call", {"name": "do_query",
                                            "arguments": {"entry_id": entry_id, "params": params or {}}})
            out = (res.get("result") or {}).get("structuredContent") or {}
            if not out.get("success"):
                raise RuntimeError(str(out.get("error") or res.get("error") or "返回 success=false")[:160])
            rows = ((out.get("data") or {}).get("results")) or []
            return rows, Provenance("bitget_mcp", "%s#%s" % (self.url, entry_id), meta, time.time(),
                                    int((time.time() - t0) * 1000), True, n_records=len(rows))
        except Exception as e:                      # noqa: BLE001
            return None, Provenance("bitget_mcp", "%s#%s" % (self.url, entry_id), meta, time.time(),
                                    int((time.time() - t0) * 1000), False,
                                    error="%s: %s" % (type(e).__name__, str(e)[:160]))


def insider_trading(symbol: str, client: StockMCP | None = None) -> tuple[list | None, Provenance]:
    """内部人交易 (官方口径)。用来和我们自己解析的 SEC Form 4 交叉核对。"""
    c = client or StockMCP()
    return c.query("equity_ownership_insider_trading", {"symbol": symbol})


def price_target(symbol: str, client: StockMCP | None = None) -> tuple[list | None, Provenance]:
    """分析师目标价与评级。"""
    c = client or StockMCP()
    return c.query("equity_estimates_price_target", {"symbol": symbol})


def quote(symbol: str, client: StockMCP | None = None) -> tuple[list | None, Provenance]:
    """实时报价 (第三个价格来源, 和 Yahoo / Nasdaq 一起交叉核对)。"""
    c = client or StockMCP()
    return c.query("equity_price_quote", {"symbol": symbol})


def health(timeout: float = 15.0) -> tuple[bool, str, Provenance]:
    """握手 + 取一次目录, 用来在界面上如实显示这个官方数据源通不通。"""
    c = StockMCP(timeout=timeout)
    t0 = time.time()
    try:
        info = c.connect()
        res = c._post("tools/call", {"name": "guide", "arguments": {}})
        cats = ((res.get("result") or {}).get("structuredContent") or {}).get("categories") or []
        n = sum(int(x.get("entry_count") or 0) for x in cats)
        return True, "%s v%s · %d 个数据入口" % (info.get("name", "bitget-mcp-server"), info.get("version", "?"), n), \
            Provenance("bitget_mcp", URL, {"probe": "guide"}, time.time(), int((time.time() - t0) * 1000), True,
                       n_records=n)
    except Exception as e:                          # noqa: BLE001
        msg = str(e)[:120]
        hint = "本机网络不可达 (马来西亚运营商挡 Bitget 域名; 云端正常)" if "Timeout" in type(e).__name__ else msg
        return False, hint, Provenance("bitget_mcp", URL, {"probe": "guide"}, time.time(),
                                       int((time.time() - t0) * 1000), False,
                                       error="%s: %s" % (type(e).__name__, msg))
