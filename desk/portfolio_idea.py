"""
把自然语言的持仓描述解析成组合 (Account), 以及对已有组合的追问修改。

  parse(text)                      大模型优先, 失败退回规则解析
  edit(text, account, btc_price)   追问: 「把 MSTR 砍半」「去掉 COIN」「BTC 全换成 USDT」「NVDA 加到 20000」
无论哪条路径, 结果都要过 portfolio.validate_account()。
大模型只做「理解文字」; 涉及计算的修改 (砍半、按现价换算 BTC) 一律由规则代码执行。
"""
from __future__ import annotations

import copy
import json
import re

from . import llm
from . import universe
from .idea import ALIASES, SUPPORTED
from .portfolio import Account, Position, validate_account

_AMOUNT = r"(\d+(?:\.\d+)?)\s*(万|w|k|K)?\s*(?:u\b|U\b|usdt|USDT|刀|美元|美金)?"
_SIDE_SHORT = r"做空|空单|看空|short|空"
_SIDE_LONG = r"做多|多单|看多|long|买入|买|多"


def _num(value: str, unit: str | None) -> float:
    x = float(value)
    if unit in ("万", "w"):
        x *= 10000
    elif unit in ("k", "K"):
        x *= 1000
    return x


def _symbols_in(seg: str) -> list[tuple[int, str]]:
    """返回 [(位置, 代码)], 按出现顺序。"""
    low = seg.lower()
    found = []
    for alias, sym in ALIASES.items():
        i = low.find(alias)
        if i >= 0:
            found.append((i, sym))
    for m in re.finditer(r"(?<![A-Za-z])R?([A-Za-z]{2,5})(?:USDT)?(?![A-Za-z])", seg):
        w = m.group(1)
        # 支持的标的从 11 只涨到上千只后, 小写英文词会大量撞上代码 (all/now/open/hood/app/key...)。
        # 规则: 小写只认最常用的那几只 (nvda/tsla...), 其余必须大写 —— 代码本来就是大写写的。
        if w.upper() in SUPPORTED and (w.isupper() or w.upper() in universe.CACHED):
            found.append((m.start(), w.upper()))
    seen, out = set(), []
    for i, s in sorted(found):
        if s not in seen:
            seen.add(s)
            out.append((i, s))
    return out


_BTC_QTY = r"(\d+(?:\.\d+)?)\s*(?:个|枚)?\s*(?:btc|BTC|比特币)"
_ETH_QTY = r"(\d+(?:\.\d+)?)\s*(?:个|枚)?\s*(?:eth|ETH|以太坊|以太)"
_COLL = re.compile(_AMOUNT + r"\s*(?:做|作|当|as)?\s*(?:保证金|抵押|margin)|(?:保证金|抵押|margin)\s*(?:是|有|:|：)?\s*" + _AMOUNT, re.I)


def _extract_collateral(t: str) -> tuple[str, float, float, float]:
    """
    从文本里取出抵押品并把这些片段删掉, 返回 (剩下的文本, usdt, btc, eth)。
    BTC / ETH 不是支持的仓位标的, 所以出现「数量 + BTC/ETH」一律当抵押品。
    ⚠️ 例外: 「用 0.1 BTC 等额买入 …」里的 BTC 是买入预算, 不是抵押品 -> 由 parse_allocate 先处理。
    """
    usdt = btc = eth = 0.0
    for m in re.finditer(_BTC_QTY, t):
        btc += float(m.group(1))
    t = re.sub(_BTC_QTY, " ", t)
    for m in re.finditer(_ETH_QTY, t):
        eth += float(m.group(1))
    t = re.sub(_ETH_QTY, " ", t)
    for m in _COLL.finditer(t):
        usdt += _num(m.group(1), m.group(2)) if m.group(1) else _num(m.group(3), m.group(4))
    return _COLL.sub(" ", t), usdt, btc, eth


_SPOT = r"现货|现价买入|spot"


def split_segments(text: str) -> list[str]:
    t = text.replace("，", ",").replace("；", ";").replace("、", ",")
    return [s for s in re.split(r"[,;\n]|和|及|以及|另外|还有|\band\b", t) if s.strip()]


def unparsed_segments(text: str) -> list[str]:
    """
    有金额、却一个标的都没认出来的片段 —— 这些必须告诉用户, 不能悄悄扔掉。
    09-17 Ivan 实测: 「现货买进台积电 1万」整段被丢了, 页面还说「由规则解析为新组合」, 像是全读懂了。
    """
    t, _, _, _ = _extract_collateral(text.replace("，", ",").replace("；", ";").replace("、", ","))
    out = []
    for seg in split_segments(t):
        if _symbols_in(seg) or not re.search(r"\d", seg):
            continue
        if re.search(r"保证金|抵押|margin|collateral|杠杆|倍|维持", seg.lower()):
            continue                                    # 保证金/杠杆片段本来就不该有标的
        out.append(seg.strip(" ,;"))
    return out


def parse_rules(text: str, maint_margin: float = 0.01, btc_haircut: float = 0.95) -> Account | None:
    return parse_rules_verbose(text, maint_margin, btc_haircut)[0]


def parse_rules_verbose(text: str, maint_margin: float = 0.01,
                        btc_haircut: float = 0.95) -> tuple[Account | None, str]:
    """和 parse_rules 一样, 但把「读懂了、可是组合不合法」的原因带出来 (例如这只票没有永续却填了杠杆)。"""
    t = text.replace("，", ",").replace("；", ";").replace("、", ",")
    t, usdt, btc, eth = _extract_collateral(t)
    # 3. 仓位: 按分隔符切片, 方向词向后沿用
    segs = split_segments(t)
    side = "long"
    pos: dict[tuple[str, str, str], float] = {}
    for seg in segs:
        syms = _symbols_in(seg)
        if re.search(_SIDE_SHORT, seg.lower()):
            side = "short"
        elif re.search(_SIDE_LONG, seg.lower()):
            side = "long"
        # 「现货」只作用于本片段: 「多英伟达 1.5万, 现货买进台积电 1万」里只有台积电是现货
        kind = "spot" if re.search(_SPOT, seg.lower()) else "leverage"
        if kind == "spot":
            side = "long"                               # 现货没有做空
        if not syms:
            continue
        # 一个片段里多个标的: 每个标的取它后面最近的金额
        for k, (i, sym) in enumerate(syms):
            end = syms[k + 1][0] if k + 1 < len(syms) else len(seg)
            m = re.search(_AMOUNT, seg[i + len(sym):end] if seg[i:i + len(sym)].upper() == sym else seg[i:end])
            if m:
                pos[(sym, side, kind)] = pos.get((sym, side, kind), 0.0) + _num(m.group(1), m.group(2))
    a = Account([Position(s, sd, n, kd) for (s, sd, kd), n in pos.items()],
                usdt, btc, maint_margin, btc_haircut, eth, btc_haircut)
    err = validate_account(a)
    if not err:
        return a, ""
    # 一个标的都没认出来 = 这句话规则读不懂 (交给大模型); 认出来了但不合法 = 把原因带回去告诉用户
    return None, ("" if not pos else err)


# 用户常提到、但本工具还没接入的资产 (识别出来要明确告诉用户, 不能悄悄忽略)
# 有公认定义的统称 -> 展开成具体标的 (只展开定义明确的; 没有公认定义的见 AMBIGUOUS_GROUPS, 要反问用户)
GROUPS = {
    "七巨头": ["AAPL", "AMZN", "GOOGL", "META", "MSFT", "NVDA", "TSLA"],
    "7巨头": ["AAPL", "AMZN", "GOOGL", "META", "MSFT", "NVDA", "TSLA"],
    "科技七巨头": ["AAPL", "AMZN", "GOOGL", "META", "MSFT", "NVDA", "TSLA"],
    "mag7": ["AAPL", "AMZN", "GOOGL", "META", "MSFT", "NVDA", "TSLA"],
    "magnificent 7": ["AAPL", "AMZN", "GOOGL", "META", "MSFT", "NVDA", "TSLA"],
}
AMBIGUOUS_GROUPS = ["大巨头", "巨头", "龙头", "蓝筹", "科技股", "大厂", "大盘股", "几只"]


def expand_groups(text: str) -> tuple[str, str]:
    """把「七巨头」这类有公认定义的统称换成具体代码。返回 (新文本, 说明)。"""
    note = ""
    low = text.lower()
    for word, syms in GROUPS.items():
        if word in low:
            i = low.find(word)
            text = text[:i] + "、".join(syms) + text[i + len(word):]
            low = text.lower()
            note = "「%s」按公认定义展开为 %s" % (word, "、".join(syms))
    return text, note


_ALLOC_WORDS = r"等额|均分|平分|平均|分别|各"
_BUDGET = re.compile(r"(?:用|拿|花|以)\s*(\d+(?:\.\d+)?)\s*(万|w|k|K)?\s*(btc|比特币|eth|以太坊|以太|usdt|u|美元|美金|刀)?", re.I)


def parse_allocate(text: str, btc_price: float = 0.0, eth_price: float = 0.0,
                   maint_margin: float = 0.01, btc_haircut: float = 0.95) -> tuple[Account | None, str]:
    """
    「(0.05 BTC 做保证金,) 用 0.1 BTC 等额买入 A、B、C」-> 预算换算成 USDT 后等额分配。
    纯规则、瞬间完成: 09-16 实测这句话交给 Qwen 要 39 秒, 会超时退回「没看懂」。
    返回 (Account | None, 说明)。
    """
    text, gnote = expand_groups(text)
    if not re.search(_ALLOC_WORDS, text):
        return None, ""
    m = _BUDGET.search(text)
    if not m:
        return None, ""
    unit = (m.group(3) or "").lower()
    price = {"btc": btc_price, "比特币": btc_price, "eth": eth_price, "以太坊": eth_price, "以太": eth_price}.get(unit, 1.0)
    if price <= 0:
        return None, "没有 %s 现价, 无法换算预算" % unit.upper()
    budget_usdt = _num(m.group(1), m.group(2)) * price
    rest = text[:m.start()] + " " + text[m.end():]          # 预算片段不能再被当成抵押品
    rest, usdt, btc, eth = _extract_collateral(rest)
    syms = [s for _, s in _symbols_in(rest)]
    if not syms or budget_usdt <= 0:
        return None, ""
    side = "short" if re.search(_SIDE_SHORT, rest.lower()) else "long"
    each = budget_usdt / len(syms)
    a = Account([Position(s, side, each) for s in syms], usdt, btc, maint_margin, btc_haircut, eth, btc_haircut)
    err = validate_account(a)
    if err:
        return None, err
    note = "规则解析: 预算 %s 换算成 %s USDT, 在 %d 只标的上等额分配 (每只 %s)" % (
        m.group(0).strip(), format(round(budget_usdt), ","), len(syms), format(round(each), ","))
    return a, ((gnote + "。") if gnote else "") + note


def clarify(text: str) -> str | None:
    """
    看不懂时不要只说「没看懂」: 指出到底缺什么, 并反问。
    只在能明确指出问题时返回文字, 否则返回 None。
    """
    low = text.lower()
    qs = []
    if any(w in low for w in AMBIGUOUS_GROUPS) and not _symbols_in(text):
        m = re.search(r"[\d一二两三四五六七八九十]?\s*大?\s*(?:%s)" % "|".join(AMBIGUOUS_GROUPS), text)
        qs.append("「%s」这种统称没有公认定义, 请直接说是哪几只 (%s; 如果你指的是「七巨头」, 我可以直接认)"
                  % (m.group(0).strip() if m else next(w for w in AMBIGUOUS_GROUPS if w in low), universe.describe()))
    # 「0.05 保证金」这种没写单位, 而句子里又提到 BTC/ETH -> 不猜, 反问
    m = re.search(r"(?<![\d.])(\d+(?:\.\d+)?)\s*(?:做|作|当)?\s*(?:保证金|抵押)", text)
    if m and not re.search(r"\d\s*(?:u\b|usdt|刀|美元|美金|万|k)\s*(?:做|作|当)?\s*(?:保证金|抵押)", low) \
            and re.search(r"btc|比特币|eth|以太", low) and float(m.group(1)) < 10:
        qs.append("「%s 保证金」没写单位: 是 %s BTC / ETH, 还是 %s USDT?" % (m.group(1), m.group(1), m.group(1)))
    syms = [s for _, s in _symbols_in(text)]
    allocate_like = re.search(_ALLOC_WORDS + r"|各占|按预算", low)     # 「用 0.1 BTC 等额/分别买入 A、B」不算缺金额
    # 只有「像在描述新持仓」时才问金额: 追问改仓 (一半 NVDA 换成 SPY 空单) 和闲聊 (英伟达最新新闻) 不能被拦下
    edit_like = re.search(r"换|砍半|减半|一半|平掉|去掉|删除|加仓|减仓|改成|改为|加到|减到|调到|全部", low)
    intent_like = re.search(r"做多|做空|买入|买进|开仓|持仓|保证金|多单|空单|建仓", low)
    if syms and intent_like and not allocate_like and not edit_like \
            and not re.search(r"\d", re.sub(r"\d+(?:\.\d+)?\s*(?:个|枚)?\s*(?:btc|eth|以太坊|比特币)", " ", low)):
        qs.append("没看到每笔的金额: 可以说「每只 5000U」, 或者「用 0.1 BTC 等额买入 %s」" % "、".join(syms[:3]))
    return "; ".join(qs) if qs else None


# (ETH 2026-09-16 起支持作保证金, 不在这里; 但 ETH 不能作仓位, 见 unsupported_message)
CRYPTO_NAMES = {"sol": "SOL", "xrp": "XRP", "doge": "DOGE", "狗狗币": "DOGE", "bnb": "BNB", "usdc": "USDC",
                "bgb": "BGB", "ada": "ADA", "trx": "TRX"}
NOT_ASSET = {"USDT", "BTC", "ETH", "USD", "U", "ETF", "AI", "LLM", "OK", "RTOKEN", "SKILL"}
_ETH_AS_POSITION = re.compile(r"(做多|做空|多单|空单|long|short|买入|开仓)\s*(eth|以太)|(eth|以太坊?)\s*\d[\d.]*\s*(万|k)?\s*(u\b|usdt)"
                              r"|(eth|以太坊?)[^,，;；。]{0,6}(多单|空单)")


def unsupported_mentions(text: str) -> list[str]:
    """找出文字里提到、但不支持的资产: 常见币种名 + 大写股票代码 (不在 11 只支持标的内)。"""
    out: list[str] = []
    low = text.lower()
    for name, code in CRYPTO_NAMES.items():
        if re.search(r"(?<![a-z])%s(?![a-z])" % re.escape(name), low) if name.isascii() else name in low:
            if code not in out:
                out.append(code)
    for m in re.finditer(r"(?<![A-Za-z])R?([A-Z]{2,5})(?:USDT)?(?![A-Za-z])", text):
        code = m.group(1)
        if code not in SUPPORTED and code not in NOT_ASSET and code not in out and code not in CRYPTO_NAMES.values():
            out.append(code)
    return out


def unsupported_message(text: str) -> str | None:
    bad = unsupported_mentions(text)
    parts = []
    if _ETH_AS_POSITION.search(text.lower()):
        parts.append("ETH 目前只能作保证金, 不能作仓位 (仓位只支持美股 rToken)")
    if not bad and not parts:
        return None
    crypto = [b for b in bad if b in CRYPTO_NAMES.values()]
    stocks = [b for b in bad if b not in crypto]
    if crypto:
        parts.append("「%s」暂不支持: 保证金目前只支持 USDT、BTC 和 ETH, 仓位只支持美股 rToken" % "、".join(crypto))
    if stocks:
        parts.append("「%s」暂不支持: %s" % ("、".join(stocks), universe.describe()))
    return "; ".join(parts)


SYSTEM_PROMPT = """你是持仓解析器。把用户描述的跨资产统一账户组合解析成 JSON, 只输出 JSON, 不要解释。
格式: {"usdt": 数字, "btc": 数字, "eth": 数字, "positions": [{"symbol": "NVDA", "side": "long", "notional": 数字}]}
  usdt       作保证金的 USDT 数量, 没说填 0
  btc        作保证金的 BTC 数量 (个数, 不是价值), 没说填 0
  eth        作保证金的 ETH 数量 (个数, 不是价值), 没说填 0; ETH 不能作仓位
  symbol     美股/ETF 代码 (rToken 如 RNVDAUSDT 填 NVDA), 如 %s; 拿不准就不要放进 positions
  side       "long" 或 "short"
  notional   仓位名义价值 (USDT)。「1.5万」= 15000。用户只说了保证金和杠杆时, 名义价值 = 保证金 x 杠杆
规则: 只转写用户给出的数字, 不要编造。""" % universe.sample()


def _from_json(d: dict, maint_margin: float, btc_haircut: float) -> Account:
    return Account([Position(str(p["symbol"]).upper(), p["side"], float(p["notional"])) for p in d.get("positions", [])],
                   float(d.get("usdt") or 0), float(d.get("btc") or 0), maint_margin, btc_haircut,
                   float(d.get("eth") or 0), btc_haircut)


def parse(text: str, maint_margin: float = 0.01, btc_haircut: float = 0.95):
    """返回 (Account | None, [Provenance], 说明)。大模型的内部错误只进调用记录, 不直接给用户看。"""
    bad = unsupported_message(text)
    reply, prov = llm.chat([{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": text}], json_mode=True)
    if reply is not None:
        try:
            a = _from_json(json.loads(re.sub(r"^```(?:json)?|```$", "", reply.strip(), flags=re.M)), maint_margin, btc_haircut)
            if validate_account(a) is None:
                return a, [prov], "由大模型解析" + ("。⚠️ " + bad + ", 已忽略" if bad else "")
        except (ValueError, KeyError, TypeError):
            pass
    a = parse_rules(text, maint_margin, btc_haircut)
    if a is not None:
        return a, [prov], "由规则解析" + ("。⚠️ " + bad + ", 已忽略" if bad else "")
    if bad:
        return None, [prov], bad + "。"
    return None, [prov], ("没看懂这句话。可以描述一个完整组合 (例: 「5000U 做保证金, 做多 NVDA 1万」), "
                          "或者对当前组合追问 (例: 「把 MSTR 砍半」「把 MSTR 换成 AAPL」「BTC 全部换成 USDT」「保证金再加 3000u」)。")


def edit(text: str, account: Account, btc_price: float, eth_price: float = 0.0) -> tuple[Account | None, str]:
    """
    追问修改 (纯规则, 计算由代码完成)。返回 (新组合 | None, 改动说明)。
    支持: 砍半/减半 · 去掉/平掉/删除 · <标的> 改成/加到/减到 <金额> · 把 A 换成 B · BTC/ETH 全换成 USDT (按当前价) · USDT 加/减 <金额>
    """
    a = copy.deepcopy(account)
    low = text.lower()
    notes = []
    # 规则只处理简单说法; 「一半 NVDA 换成 SPY 空单」这类带比例 / 方向的换仓, 规则容易理解错, 交给大模型 (understand)
    # 「成」只在「三成 / 3成」这种比例里算数, 不能匹配「换成」「改成」(09-16 测试抓到)
    if re.search(r"换|转|改|替换", text) and re.search(r"一半|半|%|％|[\d一二两三四五六七八九]\s*成|部分|空单|多单|做空|做多|long|short", low) \
            and not re.search(r"砍半|减半", text):
        return None, "句子较复杂, 交给大模型理解"
    syms = [s for _, s in _symbols_in(text)]
    # 一句话里有多种操作 (「平掉 COIN 再把 MSTR 砍半」), 规则会把同一个操作套到所有标的上 (09-16 实测出错) -> 交给大模型
    kinds = [r"砍半|减半|一半|halve|cut .*half", r"去掉|平掉|删除|不要|清掉|close|remove|drop|exit",
             r"改成|改为|加到|减到|调到|变成|set .*to|change .*to", r"换成|换为|换到|替换|转成|转为|swap|switch|replace|rotate"]
    if sum(bool(re.search(k, low)) for k in kinds) >= 2             or (len(syms) >= 2 and re.search(r"再|然后|并且|同时|另外|then|and then|also", low)):
        return None, "一句话里有多个操作, 交给大模型理解"
    # 「把 A 换成 B」: 名义价值和方向不变; B 已有同方向仓位就合并
    if len(syms) >= 2 and re.search(r"换成|换为|换到|替换|转成|转为|改成|改为|swap|switch|replace|rotate|into", low):
        src_sym, dst_sym = syms[0], syms[1]
        for p in [q for q in a.positions if q.symbol == src_sym]:
            same = next((q for q in a.positions if q.symbol == dst_sym and q.side == p.side), None)
            if same is not None:
                notes.append("%s %s %.0f 换成 %s (并入已有的 %.0f → %.0f)" % (src_sym, p.side, p.notional, dst_sym,
                                                                        same.notional, same.notional + p.notional))
                same.notional += p.notional
                a.positions.remove(p)
            else:
                notes.append("%s %s %.0f 换成 %s" % (src_sym, p.side, p.notional, dst_sym))
                p.symbol = dst_sym
        if notes:
            syms = []
    # 语序两边都要认: 中文「BTC 全部换成 USDT」/ 英文「swap all BTC to USDT」(动词在前)
    if (re.search(r"btc|比特币|eth|以太", low) and re.search(r"usdt|cash|stable", low)
            and re.search(r"换|转|swap|convert|sell|move", low)):
        for name, attr, px, pat in (("BTC", "btc", btc_price, r"btc|比特币"), ("ETH", "eth", eth_price, r"eth|以太")):
            qty = getattr(a, attr)
            if re.search(pat, low) and qty > 0 and px > 0:     # 没持有就不算修改 (交给后面说明)
                notes.append("%s %.4g 个按现价 %.0f 换成 %.0f USDT" % (name, qty, px, qty * px))
                a.usdt += qty * px
                setattr(a, attr, 0.0)
    for sym in syms:
        targets = [p for p in a.positions if p.symbol == sym]
        if not targets:
            continue
        if re.search(r"砍半|减半|一半|halve|half", low):
            for p in targets:
                notes.append("%s %s %.0f → %.0f" % (sym, p.side, p.notional, p.notional / 2))
                p.notional /= 2
        elif re.search(r"去掉|平掉|删除|不要|清掉|remove|close|drop|exit|get rid of", low):
            a.positions = [p for p in a.positions if p.symbol != sym]
            notes.append("去掉 %s" % sym)
        else:
            m = re.search(r"(?:改成|改为|加到|减到|调到|变成|=|to|at)\s*" + _AMOUNT, text, re.I)
            if m:
                new = _num(m.group(1), m.group(2))
                for p in targets:
                    notes.append("%s %s %.0f → %.0f" % (sym, p.side, p.notional, new))
                    p.notional = new
    m = re.search(r"(?:usdt|保证金|margin|collateral).*?(加|增加|减|减少|add|more|top up|increase|reduce|cut|less|withdraw)\s*" + _AMOUNT, low)         or re.search(r"(加|增加|减|减少|add|top up|increase|reduce|cut|withdraw)\s*" + _AMOUNT + r"\s*(?:usdt|保证金|margin|collateral)", low)
    if m and not syms:
        up = m.group(1).startswith(("加", "增", "add", "more", "top", "incr"))
        delta = _num(m.group(2), m.group(3)) * (1 if up else -1)
        notes.append("USDT %.0f → %.0f" % (a.usdt, a.usdt + delta))
        a.usdt += delta
    if not notes:
        return None, "没识别出修改"
    err = validate_account(a)
    return (None, err) if err else (a, "; ".join(notes))


# ---------------------------------------------------------------- 大模型理解任意说法 -> 修改指令 (计算由代码执行)

_CN_DIGIT = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_FRACTION_WORDS = {"一半": 0.5, "半": 0.5, "全部": 1.0, "全": 1.0, "所有": 1.0, "三分之一": 1 / 3, "四分之一": 0.25}


def mentioned_values(text: str) -> set[float]:
    """用户原话里出现过的数值 (含单位换算、百分比、几成、一半/全部 以及比例的补数)。指令里的数值必须在这里面。"""
    vals: set[float] = set()
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(万|w|k|K)?\s*(%|％|成)?", text):
        x, unit, pct = float(m.group(1)), m.group(2), m.group(3)
        if pct in ("%", "％"):
            vals.add(x / 100)
        elif pct == "成":
            vals.add(x / 10)
        else:
            vals.update({x, _num(m.group(1), unit)})
    for ch, v in _CN_DIGIT.items():
        if ch + "成" in text:
            vals.add(v / 10)
    for w, f in _FRACTION_WORDS.items():
        if w in text:
            vals.add(f)
    vals.update({1 - x for x in list(vals) if 0 < x < 1})   # 「减三成」-> 保留 0.7
    return vals


def _value_ok(v: float, vals: set[float]) -> bool:
    return any(abs(v - x) <= 1e-6 * max(1.0, abs(x)) for x in vals)


def apply_ops(account: Account, ops: list[dict], btc_price: float, text: str,
              eth_price: float = 0.0) -> tuple[Account | None, list[str], str | None]:
    """
    执行修改指令。返回 (新组合 | None, 改动说明, 错误)。
    安全规则: 指令里的金额 / 比例必须在用户原话里出现过 (「全部」换仓时比例 1 默认允许), 否则整条拒绝执行;
    执行后组合没有任何变化 (例如要换的 ETH 本来就是 0) 也拒绝, 并说明原因。
    """
    from .links import adjustment_steps   # 延迟导入, 避免循环依赖

    a = copy.deepcopy(account)
    vals = mentioned_values(text)

    def need(v, what):
        if not _value_ok(float(v), vals):
            raise ValueError("大模型给出的%s %g 不在你的原话里, 为安全起见不执行" % (what, float(v)))

    def sym_of(x):
        s = str(x or "").upper().removeprefix("R").removesuffix("USDT") if str(x or "").upper() not in SUPPORTED else str(x).upper()
        if s not in SUPPORTED:
            raise ValueError("不支持的标的: %s" % x)
        return s

    def side_of(x, default=None):
        return x if x in ("long", "short") else default

    def find(sym, side):
        return [p for p in a.positions if p.symbol == sym and (side is None or p.side == side)]

    def add_to(sym, side, amt):
        cur = find(sym, side)
        if cur:
            cur[0].notional += amt
        else:
            a.positions.append(Position(sym, side, amt))

    try:
        if not ops:
            raise ValueError("没有可执行的修改")
        for op in ops:
            kind = op.get("op")
            if kind == "set_notional":
                sym, side = sym_of(op.get("symbol")), side_of(op.get("side"))
                need(op["notional"], "金额")
                targets = find(sym, side)
                if targets:
                    for p in targets:
                        p.notional = float(op["notional"])
                else:
                    add_to(sym, side or "long", float(op["notional"]))
            elif kind == "scale":
                sym, side = sym_of(op.get("symbol")), side_of(op.get("side"))
                need(op["factor"], "比例")
                if not find(sym, side):
                    raise ValueError("当前组合里没有 %s" % sym)
                for p in find(sym, side):
                    p.notional *= float(op["factor"])
            elif kind == "add":
                sym, side = sym_of(op.get("symbol")), side_of(op.get("side"), "long")
                need(op["notional"], "金额")
                add_to(sym, side, float(op["notional"]))
            elif kind == "remove":
                sym, side = sym_of(op.get("symbol")), side_of(op.get("side"))
                if not find(sym, side):
                    raise ValueError("当前组合里没有 %s" % sym)
                a.positions = [p for p in a.positions if p not in find(sym, side)]
            elif kind == "move":
                src_sym, src_side = sym_of(op.get("from_symbol")), side_of(op.get("from_side"))
                dst_sym = sym_of(op.get("to_symbol"))
                frac = float(op.get("fraction", 1))
                if frac != 1:
                    need(frac, "比例")
                if not 0 < frac <= 1:
                    raise ValueError("比例需在 0-1 之间")
                src = find(src_sym, src_side)
                if not src:
                    raise ValueError("当前组合里没有 %s" % src_sym)
                for p in list(src):
                    amt = p.notional * frac
                    p.notional -= amt
                    if p.notional < 0.5:
                        a.positions.remove(p)
                    add_to(dst_sym, side_of(op.get("to_side"), p.side), amt)
            elif kind == "allocate":
                # 「用 0.1 BTC 等额买入 A、B、C」: 预算换算成 USDT 后等额分配
                asset = str(op.get("budget_asset", "USDT")).upper()
                budget = float(op["budget"])
                need(budget, "预算")
                px = {"USDT": 1.0, "BTC": btc_price, "ETH": eth_price}.get(asset)
                if not px:
                    raise ValueError("「%s」不能作预算单位 (只支持 USDT、BTC、ETH)" % asset)
                syms = [sym_of(x) for x in (op.get("symbols") or [])]
                if not syms:
                    raise ValueError("没说要买哪些标的")
                side = side_of(op.get("side"), "long")
                each = budget * px / len(syms)
                for s in syms:
                    add_to(s, side, each)
            elif kind in ("set_collateral", "add_collateral"):
                asset = str(op.get("asset", "")).upper()
                if asset not in ("USDT", "BTC", "ETH"):
                    raise ValueError("「%s」暂不支持作保证金 (目前只支持 USDT、BTC 和 ETH)" % op.get("asset"))
                v = float(op["value"])
                need(abs(v), "数量")
                attr = asset.lower()
                setattr(a, attr, v if kind == "set_collateral" else getattr(a, attr) + v)
            elif kind == "swap_collateral":
                src = str(op.get("from", "")).upper()
                if src not in ("BTC", "ETH") or str(op.get("to", "")).upper() != "USDT":
                    raise ValueError("目前只支持把 BTC 或 ETH 保证金换成 USDT")
                px = btc_price if src == "BTC" else eth_price
                if px <= 0:
                    raise ValueError("没有 %s 现价, 无法换算" % src)
                frac = float(op.get("fraction", 1))
                if frac != 1:
                    need(frac, "比例")
                attr = src.lower()
                amt = getattr(a, attr) * frac
                setattr(a, attr, getattr(a, attr) - amt)
                a.usdt += amt * px
            else:
                raise ValueError("不认识的操作: %s" % kind)
    except (ValueError, KeyError, TypeError) as e:
        return None, [], str(e) if not isinstance(e, KeyError) else "指令缺少字段 %s" % e
    err = validate_account(a)
    if err:
        return None, [], err
    steps = [s["text"] for s in adjustment_steps(account, a)]
    if not steps:
        return None, [], "修改后组合没有变化 (例如要换的抵押品本来就没有)"
    return a, steps, None


UNDERSTAND_PROMPT = """你是持仓压力测试工具的指令解析器。输入包含用户当前的组合 (JSON) 和用户的一句话。只输出 JSON:
{"intent": "edit" | "new" | "other", "ops": [...], "portfolio": {...}, "reason": "一句话说明你的理解"}
- edit: 用户要修改当前组合 (砍半、减三成、换标的、加仓、平掉、加减保证金、BTC 换 USDT、一半 NVDA 换成 SPY 空单 等), 填 ops
- new: 用户描述了一个全新的完整组合, 填 portfolio: {"usdt": 数字, "btc": 数字, "eth": 数字, "positions": [{"symbol": "NVDA", "side": "long", "notional": 数字}]}
- other: 与调整组合无关 (问新闻、行情、闲聊), 或只提到不支持的资产
ops 可用:
  {"op": "set_notional", "symbol": "NVDA", "side": "long", "notional": 10000}    某仓位名义价值改成
  {"op": "scale", "symbol": "MSTR", "side": "long", "factor": 0.5}                按比例缩放 (砍半=0.5, 减三成=0.7)
  {"op": "add", "symbol": "TSLA", "side": "long", "notional": 5000}              加仓或新开仓
  {"op": "remove", "symbol": "SPY", "side": "short"}                             平掉
  {"op": "move", "from_symbol": "NVDA", "from_side": "long", "to_symbol": "SPY", "to_side": "short", "fraction": 0.5}  把一部分仓位换成另一个标的 (fraction=1 表示全部; to_side 省略表示方向不变)
  {"op": "set_collateral", "asset": "USDT", "value": 8000}                       保证金改成 (asset 只能 USDT、BTC、ETH; BTC/ETH 单位是个数)
  {"op": "add_collateral", "asset": "ETH", "value": 2}                           保证金增加 (减少用负数)
  {"op": "swap_collateral", "from": "BTC", "to": "USDT", "fraction": 1}          把 BTC 或 ETH 保证金按现价换成 USDT
  {"op": "allocate", "budget_asset": "BTC", "budget": 0.1, "symbols": ["NVDA", "AAPL", "MSFT"], "side": "long"}
      用一笔预算等额买入多个标的 (预算单位 USDT / BTC / ETH; 换算成 USDT 和等额分配都由代码完成, 你不要算)
规则:
- symbol 是美股/ETF 代码 (如 %s); side 省略表示该标的所有方向
- 数值只能来自用户原话 (「一半」=0.5, 「全部」=1, 「1.5万」=15000, 「减三成」=0.7); 不要编数字, 不要算价格
- ETH 只能作保证金, 不能作仓位
- 加密货币 (如 SOL、DOGE) 不能作仓位, 不要放进 ops 或 portfolio, 在 reason 里说明""" % universe.sample()


def understand(text: str, account: Account, btc_price: float, maint_margin: float = 0.01, btc_haircut: float = 0.95,
               eth_price: float = 0.0, timeout: float = 45.0, budget_check=None):
    """
    规则追问解析不了时调用: 大模型判断意图 (改当前组合 / 新组合 / 其他) 并给出指令, 由 apply_ops 执行。
    返回 (Account | None, [Provenance], 说明)。
    完整的新组合描述先用规则解析 (瞬间完成; 09-16 实测 Qwen 偶发 60 秒超时); 大模型不可用或失败时也退回规则。
    """
    text, gnote = expand_groups(text)               # 「七巨头」这类统称先展开成具体标的
    bad = unsupported_message(text)
    alloc, anote = parse_allocate(text, btc_price, eth_price, maint_margin, btc_haircut)   # 「用 X BTC 等额买入…」先试
    if alloc is not None:
        return alloc, [], anote + (("。⚠️ " + bad + ", 已忽略") if bad else "")
    ask = clarify(text)                             # 缺金额 / 单位不明 / 统称没有公认定义 -> 直接反问, 不猜
    if ask:
        return None, [], (("%s。" % gnote) if gnote else "") + ask
    quick, quick_err = parse_rules_verbose(text, maint_margin, btc_haircut)
    if quick is not None:
        note = "由规则解析为新组合" + (("。" + gnote) if gnote else "")
        # 读不懂的片段要说出来 —— 悄悄少算一笔仓位, 比看不懂整句话更危险
        missed = unparsed_segments(text)
        if missed:
            note += "。⚠️ 这部分没看懂, 没有计入: 「%s」(可以改成「现货买入 台积电 1万」这种写法, 或直接在下面的表格里加一行)" \
                    % "」「".join(missed)
        return quick, [], note + (("。⚠️ " + bad + ", 已忽略") if bad else "")
    if quick_err:
        # 规则已经读懂了, 只是组合本身不合法 (例如这只票没有永续却填了杠杆) ——
        # 直接把原因说清楚, 不用再等大模型 9–40 秒绕一圈得到同样的结论
        return None, [], quick_err
    payload = {"current_portfolio": {"usdt": account.usdt, "btc": account.btc, "eth": account.eth,
                                     "positions": [{"symbol": p.symbol, "side": p.side, "notional": p.notional}
                                                   for p in account.positions]},
               "user_text": text}
    # budget_check: 公开部署时限制大模型每日调用次数; 不给额度就直接走规则兜底
    if budget_check is not None and not budget_check():
        a = parse_rules(text, maint_margin, btc_haircut)
        if a is not None:
            return a, [], "由规则解析为新组合 (大模型今日额度已用完)"
        return None, [], ("大模型今日额度已用完, 规则没看懂这句话。可以换成更直接的说法, 例如"
                          "「5000U 做保证金, 做多 NVDA 1万」「把 MSTR 砍半」「用 0.1 BTC 等额买入 NVDA、AAPL」。")
    reply, prov = llm.chat([{"role": "system", "content": UNDERSTAND_PROMPT},
                            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
                           json_mode=True, timeout=timeout)
    if reply is not None:
        try:
            d = json.loads(re.sub(r"^```(?:json)?|```$", "", reply.strip(), flags=re.M))
        except ValueError:
            d = {}
        intent = d.get("intent")
        if intent == "edit":
            new, notes, err = apply_ops(account, d.get("ops") or [], btc_price, text, eth_price)
            if new is not None:
                return new, [prov], "大模型理解为: %s → %s%s" % (d.get("reason", ""), "; ".join(notes) or "无变化",
                                                                ("。⚠️ " + bad + ", 已忽略") if bad else "")
            return None, [prov], "没能执行这个修改: %s%s" % (err, ("。" + bad) if bad else "")
        if intent == "new":
            try:
                a = _from_json(d.get("portfolio") or {}, maint_margin, btc_haircut)
                if validate_account(a) is None:
                    return a, [prov], "由大模型解析为新组合" + (("。⚠️ " + bad + ", 已忽略") if bad else "")
            except (ValueError, KeyError, TypeError):
                pass
            if d.get("ops"):       # 新组合也可以用指令描述 (例: 预算等额分配), 就在空组合上执行
                empty = Account([], 0.0, 0.0, maint_margin, btc_haircut)
                new, notes, err = apply_ops(empty, d["ops"], btc_price, text, eth_price)
                if new is not None:
                    return new, [prov], "大模型理解为新组合: %s → %s" % (d.get("reason", ""), "; ".join(notes))
        if intent == "other":
            return None, [prov], (bad + "。" if bad else
                                  "这个问题超出本工具范围: 这里只做持仓压力测试 (描述组合, 或追问怎么调整)。%s" % d.get("reason", ""))
    a = parse_rules(text, maint_margin, btc_haircut)
    if a is not None:
        return a, [prov], "由规则解析为新组合" + (("。⚠️ " + bad + ", 已忽略") if bad else "")
    if bad:
        return None, [prov], bad + "。"
    return None, [prov], ("没看懂这句话。可以描述一个完整组合 (例: 「5000U 做保证金, 做多 NVDA 1万」"
                          "或「0.1 BTC 做保证金, 用 0.2 BTC 等额买入 NVDA、AAPL、MSFT」), "
                          "或者追问 (例: 「MSTR 减三成」「一半 NVDA 换成 SPY 空单」「再加 5000 TSLA 多单」「BTC 全部换成 USDT」)。")
