"""
把自然语言交易想法解析成结构化假设。

两条路径:
  parse_llm   用大模型解析 (更自然, 能理解口语)
  parse_rules 纯规则解析 (不依赖大模型; 大模型不可用或输出不合规时自动退回, 保证 Demo 离线也能跑)

无论哪条路径, 结果都要过 validate(): 只接受已接入数据的标的, 杠杆和方向必须合法。
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

from . import llm
from . import universe

# 组合模式支持 Bitget 上架的全部 rToken (清单见 desk/universe.py);
# 单笔模式还要用本地缓存的 rToken 小时线算周末重锚, 所以只覆盖 TRADE_SUPPORTED 那几只。
SUPPORTED = universe.SUPPORTED
TRADE_SUPPORTED = universe.CACHED
ALIASES = {
    "英伟达": "NVDA", "辉达": "NVDA", "特斯拉": "TSLA", "苹果": "AAPL", "微软": "MSFT", "亚马逊": "AMZN",
    "谷歌": "GOOGL", "google": "GOOGL", "脸书": "META", "facebook": "META", "标普": "SPY", "纳指": "QQQ",
    "纳斯达克": "QQQ", "coinbase": "COIN", "微策略": "MSTR", "strategy": "MSTR",
}
SCENARIOS = {"weekend", "overnight"}


@dataclass
class TradeIdea:
    symbol: str            # 正股代码, 如 NVDA
    side: str              # long | short
    leverage: float
    scenario: str          # weekend (持仓过周末) | overnight (隔夜/财报跳空)
    raw_text: str
    parsed_by: str         # llm | rules
    note: str = ""

    @property
    def rtoken(self) -> str:
        return "R%sUSDT" % self.symbol

    def to_dict(self) -> dict:
        return {**asdict(self), "rtoken": self.rtoken}


def validate(d: dict) -> str | None:
    """返回错误说明; 合法返回 None。"""
    sym = d.get("symbol")
    if sym not in SUPPORTED:
        return "不支持的标的: %r (支持 %s)" % (sym, universe.describe())
    if sym not in TRADE_SUPPORTED:
        # 周末重锚跳空要用 rToken 的真实小时线, 本地只缓存了这几只
        return ("单笔模式目前只覆盖有 rToken 小时线缓存的 %d 只 (%s)。%s 请用「组合」模式测 —— "
                "组合用正股 5 年日线重演, 不需要小时线缓存。"
                % (len(TRADE_SUPPORTED), "、".join(sorted(TRADE_SUPPORTED)), sym))
    if d.get("side") not in ("long", "short"):
        return "方向必须是 long 或 short"
    try:
        lev = float(d.get("leverage"))
    except (TypeError, ValueError):
        return "杠杆不是数字"
    if not 1 <= lev <= 20:
        return "杠杆需在 1-20 倍之间"
    if d.get("scenario") not in SCENARIOS:
        return "场景必须是 weekend 或 overnight"
    return None


def parse_rules(text: str) -> TradeIdea | None:
    t = text.strip()
    low = t.lower()
    symbol = None
    for alias, sym in ALIASES.items():
        if alias in low:
            symbol = sym
            break
    if symbol is None:
        for m in re.findall(r"\b[A-Za-z]{2,5}\b", t):
            if m.upper() in TRADE_SUPPORTED:
                symbol = m.upper()
                break
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:x|X|×|倍|times?|-?fold)", t)
    leverage = float(m.group(1)) if m else 1.0
    side = "short" if re.search(r"做空|空单|看空|short", low) else "long"
    # 「周末」优先: 「周五收盘前…过周末」里的「收盘前」不能被当成「盘前」(测试用例抓到的 bug)
    if re.search(r"周末|weekend", low):
        scenario = "weekend"
    elif re.search(r"隔夜|财报|(?<!收)盘前|盘后|overnight|earnings", low):
        scenario = "overnight"
    else:
        scenario = "weekend"
    d = {"symbol": symbol, "side": side, "leverage": leverage, "scenario": scenario}
    if validate(d):
        return None
    return TradeIdea(symbol, side, leverage, scenario, t, "rules")


SYSTEM_PROMPT = """你是交易想法解析器。把用户的一句话交易想法解析成 JSON, 只输出 JSON, 不要解释。
字段:
  symbol    正股代码 (美股/ETF, 如 %s); 不认识就填 null
  side      "long" 或 "short"
  leverage  数字, 用户没说就填 1
  scenario  "weekend"(持仓过周末) 或 "overnight"(隔夜/财报/盘前盘后跳空); 用户没说默认 "weekend"
  note      用户想法里无法映射到上面字段的关键信息, 原样简述; 没有就填 ""
规则: 不要编造任何价格、概率或历史数据; 拿不准就把 symbol 填 null。""" % universe.sample()


def parse_llm(text: str, provider: str | None = None):
    """返回 (TradeIdea | None, Provenance, 错误说明 | None)。"""
    reply, prov = llm.chat([{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": text}],
                           provider=provider, json_mode=True)
    if reply is None:
        return None, prov, prov.error
    try:
        d = json.loads(re.sub(r"^```(?:json)?|```$", "", reply.strip(), flags=re.M))
    except ValueError:
        return None, prov, "大模型输出不是合法 JSON"
    err = validate(d)
    if err:
        return None, prov, err
    return TradeIdea(d["symbol"], d["side"], float(d["leverage"]), d["scenario"], text.strip(), "llm",
                     str(d.get("note") or "")), prov, None


def parse(text: str, provider: str | None = None):
    """先试大模型, 失败自动退回规则解析。返回 (TradeIdea | None, [Provenance], 说明)。"""
    idea, prov, err = parse_llm(text, provider)
    if idea is not None:
        return idea, [prov], "由大模型解析"
    rule_idea = parse_rules(text)
    if rule_idea is not None:
        return rule_idea, [prov], "大模型不可用或输出不合规 (%s), 已退回规则解析" % err
    from .portfolio_idea import unsupported_message   # 延迟导入, 避免循环依赖
    bad = unsupported_message(text)
    if bad:
        return None, [prov], bad + "。"
    # 大模型的内部错误 (err) 只进调用记录, 不直接给用户看
    return None, [prov], ("没识别出标的或参数。单笔模式支持 %s; 杠杆 1-20 倍。"
                          "例: 「周五收盘前 3 倍做多 NVDA 过周末」" % "、".join(sorted(TRADE_SUPPORTED)))
