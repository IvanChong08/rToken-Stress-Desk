"""
rToken Stress Desk —— 网页界面 (定稿设计: B1 危险警示风格 + B2 排版)。

    streamlit run app.py

两个模式:
  组合  多只 rToken 杠杆仓位 + USDT/BTC 保证金 (跨资产统一账户): 5 年地震图 + 强平距离尺 + 可执行调整方案
  单笔  一句话交易想法 (周末 / 隔夜): 周末 rToken + 5 年尾部
每个数字都能点开看来源; 调整方案可一键打开 Bitget 对应交易页面 (只导航, 不下单)。
样式与 HTML 片段在 desk/ui.py, 这里只放流程。
"""
import dataclasses
import json
import os
import re
import sys

import pandas as pd
import streamlit as st

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from desk import budget  # noqa: E402
from desk import card as cardmod  # noqa: E402
from desk import llm, score, ui  # noqa: E402
from desk import holdings  # noqa: E402
from desk import links  # noqa: E402
from desk import portfolio as pf  # noqa: E402
from desk import portfolio_card as pcard  # noqa: E402
from desk import portfolio_idea as pidea  # noqa: E402
from desk import radar as radarmod  # noqa: E402
from desk import universe  # noqa: E402
from desk.idea import TradeIdea, parse, validate  # noqa: E402

SUPPORTED = universe.SUPPORTED
from desk.provenance import CallLog, verify_log  # noqa: E402
from desk.sources import mcp  # noqa: E402

# Streamlit Community Cloud 的密钥放在 Secrets 里, 本项目其余代码统一读环境变量 -> 这里桥接一下。
# 只搬运字符串值, 不打印、不写日志。
try:
    for _k, _v in st.secrets.items():
        if isinstance(_v, str) and _k not in os.environ:
            os.environ[_k] = _v
except Exception:
    pass

LOG_PATH = os.path.join(ROOT, "data", "calls_app.jsonl")
USED_SKILL_TOOLS = {"technical_analysis.atr"}   # 与 desk/card.py 里实际调用的 Skill 工具保持一致
# Skill 的这几个工具在对方服务器上长期取不到上游数据 (09-14 / 09-17 两次实测一致)。
# 同样的上游我们直连都正常, 所以这里写清楚每一个「失败了改用什么」, 而不是只摆一排红叉。
SKILL_FALLBACK = {
    "technical_analysis.atr": "单笔页面的 BTC 日均真实波幅",
    "crypto_derivatives.price": "BTC/ETH 价格 (雷达里另有 CoinGecko + Deribit + Yahoo 三源交叉核对)",
    "sentiment_index.current": "直连 alternative.me (雷达里的加密恐惧贪婪)",
    "global_assets.price": "直连 Yahoo Finance (美股价格, 雷达里再与 Nasdaq 交叉核对)",
    "crypto_market.global": "直连 CoinGecko (加密总市值 / BTC 占比)",
    "rates_yields.fed_funds": "不使用 —— 本工具不做宏观利率",
    "news_feed.latest": "不使用 —— 明确不做新闻解读 (无法逐句溯源)",
}
# 中英文都走规则解析 (瞬间完成, 不消耗大模型额度); 英文示例放最后一个, 让人一眼看到能用英文问
TRADE_EXAMPLES = ["周五收盘前我想 3 倍做多 NVDA rToken 过周末", "MSTR 5倍做多 过周末", "3x long NVDA over the weekend"]
PF_EXAMPLES = [
    "0.1 BTC 和 5000 USDT 做保证金，做多 NVDA 15000U、MSTR 8000U、COIN 5000U，做空 SPY 5000U",
    "保证金 2万U, 多英伟达 1.5万, 多特斯拉 1万, 空纳指 1万",
    "long NVDA 15000, short SPY 5000, 0.1 BTC as margin",
]
PF_FOLLOWUPS = ["把 MSTR 砍半呢", "BTC 全部换成 USDT 呢", "halve MSTR"]

st.set_page_config(page_title="rToken Stress Desk", page_icon="🧯", layout="wide", initial_sidebar_state="collapsed")
st.markdown(ui.CSS, unsafe_allow_html=True)


# ---------------------------------------------------------------- 缓存与数据
@st.cache_resource
def get_log() -> CallLog:
    return CallLog(LOG_PATH)


@st.cache_data(ttl=600, show_spinner=False)
def mcp_health() -> dict:
    """
    全部 Skill 工具探测: 只在用户点击时运行。并行, 总耗时约等于单个超时。
    超时给 22 秒而不是 8 秒 —— 坏掉的工具要 16~31 秒才会返回**它自己**的错误
    (alt_me_error / ConnectTimeout / all feeds errored), 8 秒会在那之前被我们掐断,
    显示成我们这边的 ReadTimeout, 看起来像是我们的网络问题。
    """
    client = mcp.MCPClient(timeout=22)
    try:
        info = client.connect()
    except Exception as e:
        return {"server": None, "error": str(e)[:160], "probes": []}
    return {"server": info, "error": None, "probes": [p.to_dict() for p in mcp.health_check(client)]}


@st.cache_data(ttl=600, show_spinner=False)
def skill_in_use_ok() -> bool:
    """只探测本工具实际在用的 Skill 工具 (technical_analysis.atr)。"""
    client = mcp.MCPClient(timeout=8)
    try:
        client.connect()
        res, prov = client.call("technical_analysis", action="atr", symbol="BTC/USDT", timeframe="1d")
        return bool(prov.ok and isinstance(res, dict) and res.get("atr_pct") is not None)
    except Exception:
        return False


@st.cache_data(ttl=3600, show_spinner=False)
def rtoken_cache_asof() -> str | None:
    try:
        df, _ = cardmod.load_rtoken_cache("NVDA")
        return df["ts"].max().strftime("%m-%d")
    except Exception:
        return None


@st.cache_data(ttl=3600, show_spinner=False)
def get_panel(symbols: tuple, _log: CallLog) -> pf.Panel:
    return pf.fetch_panel(list(symbols), _log)


@st.cache_data(ttl=1800, show_spinner=False)
def get_radar(symbols: tuple, _log: CallLog) -> dict:
    return radarmod.radar(list(symbols), _log)


@st.cache_data(ttl=600, show_spinner=False)
def get_prices(symbols: tuple) -> dict:
    """持仓表要用的当前价 (算名义价值和未实现盈亏)。只取 5 天日线, 比取 5 年快得多。"""
    if not symbols:
        return {}
    from concurrent.futures import ThreadPoolExecutor
    from desk.sources import yahoo
    out = {}
    with ThreadPoolExecutor(max_workers=min(8, len(symbols))) as ex:
        for sym, (df, _prov) in zip(symbols, ex.map(lambda s: yahoo.ohlcv(s, "5d", "1d"), symbols)):
            if df is not None and not df.empty:
                out[sym] = float(df["close"].dropna().iloc[-1])
    return out


@st.cache_data(ttl=600, show_spinner=False)
def get_crypto_prices() -> dict:
    p = get_prices(("BTC-USD", "ETH-USD"))
    return {"BTC": p.get("BTC-USD", 0.0), "ETH": p.get("ETH-USD", 0.0)}


def html(fragment: str, target=None):
    (target or st).markdown(fragment, unsafe_allow_html=True)


def render_facts(facts):
    for f in facts:
        a, b, s = st.columns([4, 5, 1])
        a.write(f.label)
        b.markdown("**%s**" % f.display)
        with s.popover("来源"):
            for src in f.sources:
                st.code(src, language=None)


def mm_haircut() -> tuple[float, float]:
    return st.session_state.get("mm_pct", 1.0) / 100, st.session_state.get("haircut_pct", 95) / 100


def header_status(check_skill: bool) -> tuple[list, str]:
    """顶栏状态。check_skill=False 时不做 Skill 网络检测 (可能要等几秒), 先显示「检测中」。"""
    last = st.session_state.get("pf_report")
    if last is None:
        yahoo = (None, "YAHOO")
    else:
        yahoo = (all(p.get("ok") for p in last.provenance if p.get("source") == "yahoo"), "YAHOO")
    asof = rtoken_cache_asof()
    skill = (skill_in_use_ok(), "SKILL ATR") if check_skill else ("pending", "SKILL ATR 检测中")   # 黄色闪烁
    provider = llm.available_provider()
    left = budget.remaining()
    items = [yahoo, (bool(asof), "RTOKEN %s" % asof if asof else "RTOKEN 缓存读取失败"), skill,
             (True if (provider and left) else (False if provider else None),
              ("%s 今日剩 %d 次" % (provider.upper(), left)) if provider else "未配置大模型")]
    log_text = "日志 暂无记录"
    if os.path.exists(LOG_PATH):
        ok, n = verify_log(LOG_PATH)
        with open(LOG_PATH, encoding="utf-8") as f:
            lines = [ln for ln in f if ln.strip()]
        last_hash = json.loads(lines[-1])["hash"][:12] if lines else ""
        log_text = "日志 %d · %s #%s" % (n, "链完整" if ok else "链损坏", last_hash)
    return items, log_text


header_slot = st.empty()
html(ui.header(*header_status(check_skill=False)), header_slot)   # 先立刻画出顶栏, 页面最后再更新
tab_pf, tab_trade, tab_radar = st.tabs(["组合", "单笔", "雷达"])

# ================================================================ 组合
with tab_pf:
    if "pf_account" not in st.session_state:
        st.session_state["pf_account"] = pidea.parse_rules(PF_EXAMPLES[0])
        st.session_state["pf_ver"] = 0
    acc: pf.Account = st.session_state["pf_account"]

    chips = st.columns(len(PF_EXAMPLES) + len(PF_FOLLOWUPS))
    for i, ex in enumerate(PF_EXAMPLES):
        if chips[i].button("示例 %d" % (i + 1), help=ex, width="stretch", key="pf_ex_%d" % i):
            st.session_state["pf_query"] = ex
    for j, q in enumerate(PF_FOLLOWUPS):
        if chips[len(PF_EXAMPLES) + j].button(q, width="stretch", key="pf_fu_%d" % j):
            st.session_state["pf_query"] = q
    c_in, c_btn = st.columns([8, 1], vertical_alignment="bottom")
    pq = c_in.text_input("用一句话描述持仓, 或者追问修改", key="pf_query", placeholder=PF_EXAMPLES[0],
                         label_visibility="collapsed")
    run_now = False
    if c_btn.button("解析 / 修改", key="pf_parse", width="stretch") and pq.strip():
        log = get_log()
        mm0, hc0 = mm_haircut()
        panel_for_price = get_panel(tuple(sorted({p.symbol for p in acc.positions})), log)
        new, how = pidea.edit(pq, acc, panel_for_price.btc_price, panel_for_price.eth_price)       # 规则优先 (快)
        if new is None:
            with st.spinner("大模型理解中..."):
                new, provs, how = pidea.understand(pq, acc, panel_for_price.btc_price, mm0, hc0,
                                                   panel_for_price.eth_price, budget_check=budget.take)
            for p in provs:
                log.append(p)
        else:
            how = "追问修改: " + how
        if new is None:
            st.error(how)
        else:
            prev = st.session_state.get("pf_report")        # 记下修改前的总分, 新结果旁边显示对比
            st.session_state["pf_prev_score"] = prev.scorecard["overall"] if prev is not None else None
            st.session_state["pf_account"] = acc = new
            st.session_state["pf_ver"] += 1
            st.session_state["pf_how"] = how
            run_now = True
    if st.session_state.get("pf_how"):
        st.caption("解析: " + st.session_state["pf_how"])

    seismo_box = st.container(key="panel_00")          # 内容在算完报告后再填

    with st.container(key="panel_01"):
        html(ui.panel_title("01", "持仓", "%d 只 rToken 可选 (%d 只能加杠杆); 买入价留空 = 当作刚开仓"
                            % (len(universe.SUPPORTED), len(universe.LEVERAGE))))
        h_left, h_right = st.columns([7, 3], gap="medium")
        with h_left:
            ver = st.session_state["pf_ver"]
            auto = st.checkbox("用 买价 × 数量 自动算名义价值 (取当前价)", value=True, key="pf_auto")
            prices = get_prices(tuple(sorted({p.symbol for p in acc.positions})))
            df = pd.DataFrame(holdings.position_rows(acc.positions, prices))
            edited = st.data_editor(
                df, num_rows="dynamic", width="stretch", key="pf_editor_%d" % ver, hide_index=True,
                column_config={
                    "标的": st.column_config.SelectboxColumn(options=sorted(SUPPORTED), required=True, width="small"),
                    "类型": st.column_config.SelectboxColumn(options=["杠杆", "现货"], required=True, width="small"),
                    "方向": st.column_config.SelectboxColumn(options=["做多", "做空"], required=True, width="small"),
                    "买入价": st.column_config.TextColumn(width="small", help="留空 = 当作刚开仓 (浮盈浮亏按 0 算)"),
                    # 代币化美股本来就是买零碎份额用的: Bitget 数量精度 4 位 = 最小 0.0001 股。
                    # step 给 1 会把 0.2、0.005 这种直接卡掉 (买不起整股正是 rToken 的意义)。
                    "数量": st.column_config.NumberColumn(min_value=0.0, step=0.0001, format="%g",
                                                         help="可以填小数, 最小 0.0001 股 (Bitget 数量精度 4 位)"),
                    "名义 USDT": st.column_config.NumberColumn(min_value=0.0, format="%.2f", disabled=auto,
                                                             help="勾了自动算时由 数量 × 当前价 得出; Bitget 单笔最小 10 USDT"),
                })
            u1, u2, u3 = st.columns(3)
            usdt = u1.number_input("USDT 保证金", min_value=0.0, value=float(acc.usdt), step=500.0, key="pf_usdt_%d" % ver)
            btc = u2.number_input("BTC (个)", min_value=0.0, value=float(acc.btc), step=0.01, format="%.4f",
                                  key="pf_btc_%d" % ver)
            eth = u3.number_input("ETH (个)", min_value=0.0, value=float(acc.eth), step=0.1, format="%.3f",
                                  key="pf_eth_%d" % ver)
            s1, s2 = st.columns(2)
            s1.slider("维持保证金率 %", 0.5, 5.0, 1.0, 0.5, key="mm_pct", help="你设定的假设值, 不是 Bitget 官方数值。越高越早触及强平。")
            s2.slider("BTC/ETH 折算率 %", 50, 100, 95, 5, key="haircut_pct",
                      help="BTC、ETH 作保证金时按多少比例计入权益 (两者用同一个值, 这是简化)。你设定的假设值, 不是 Bitget 官方数值。")
            mm, haircut = mm_haircut()

            rows = edited.to_dict("records")
            # 手动加的行要补取当前价: prices 只覆盖了进来时就有的持仓, 新打的标的取不到价,
            # 名义价值算不出来, 那一行连同你填的买入价数量都会被丢掉 (09-17 Ivan 实测)
            new_syms = tuple(sorted({str(r.get("标的") or "").strip().upper() for r in rows} & SUPPORTED - set(prices)))
            if new_syms:
                prices = {**prices, **get_prices(new_syms)}
            positions, skipped = holdings.parse_rows(rows, prices, auto, SUPPORTED)
            for s in skipped:
                st.warning("这一行没有计入: " + s)
            cur = pf.Account(positions, usdt, btc, mm, haircut, eth, haircut)
            err = pf.validate_account(cur)
            if err:
                st.warning(err)
            clicked = st.button("运行组合压力测试", type="primary", key="pf_run", width="stretch")

        with h_right:
            if not err:
                pxs = get_crypto_prices()
                coll = [(n, v, c) for n, v, c in (
                    ("USDT", cur.usdt, ui.TEXT),
                    ("BTC", cur.btc * pxs.get("BTC", 0) * cur.btc_haircut, ui.ORANGE),
                    ("ETH", cur.eth * pxs.get("ETH", 0) * cur.eth_haircut, "#7fb0e0"),
                    ("现货", pf.spot_value(cur), "#7fd67a")) if v > 0]
                shades = ["#ece7dc", "#b9b2a2", "#8a8474", "#5f5a4e"]     # 多头用同色系不同深浅, 条形图才分得出来
                items, k = [], 0
                for p in cur.positions:
                    if p.kind == "spot":
                        c = "#7fb0e0"
                    elif p.side == "short":
                        c = ui.ORANGE
                    else:
                        c = shades[k % len(shades)]; k += 1
                    items.append((p.symbol, p.notional, c))
                equity = pf.equity0(cur, pxs.get("BTC", 0), pxs.get("ETH", 0))
                gross = pf.gross_notional(cur)
                html(ui.account_overview(items, coll, (gross / equity) if equity > 0 else None,
                                         equity, pf.unrealized_pnl(cur), gross))

    # ?demo=1: 打开页面就自动跑一次示例组合 (给评委 / 录屏用); 只在本会话还没有报告时触发
    demo_autorun = st.query_params.get("demo") == "1" and "pf_report" not in st.session_state
    if (clicked or run_now or demo_autorun) and not err:
        log = get_log()
        with st.spinner("取 5 年行情并重演中..."):
            panel = get_panel(tuple(sorted({p.symbol for p in cur.positions})), log)
            st.session_state["pf_account"] = cur
            st.session_state["pf_report"] = pcard.build_report(cur, panel, log=log, use_llm=False)
    rep = st.session_state.get("pf_report")

    with seismo_box:
        if rep is None:
            html(ui.panel_title("00", "把这个组合放回过去 5 年的每一天"))
            st.caption("点「运行组合压力测试」后显示: 每个交易日的账户亏损, 以及强平线在哪里。")
        else:
            html(ui.panel_title("00", "把这个组合放回过去 5 年的每一天",
                                "%s → %s · %d 个交易日 · 每根线 = 当天账户亏损 (各资产最差价同时出现)"
                                % (rep.daily_dates[0], rep.daily_dates[-1], len(rep.daily_dates))))
            html(ui.seismograph(rep.daily_dates, rep.daily_loss, rep.liq_loss))

    c2, c3 = st.columns([5, 3], gap="medium")
    with c2:
        with st.container(key="panel_02"):
            # 全是现货的账户不会被强平, 尺子量的是亏掉多少本金
            pf_spot_only = rep is not None and bool(rep.account["positions"]) and all(
                p.get("kind") == "spot" for p in rep.account["positions"])
            html(ui.panel_title("02", "亏损尺" if pf_spot_only else "强平距离尺",
                                "每个情景亏掉多少本金" if pf_spot_only else "每个情景用掉多少"))
            if rep is None:
                st.caption("运行后显示安全总分。")
            else:
                sc = rep.scorecard
                html(ui.score_block(sc["overall"], sc["weakest_label"], score.grade(sc["overall"])))
                if st.session_state.get("pf_prev_score") is not None:
                    html(ui.compare_line(st.session_state["pf_prev_score"], sc["overall"]))
                html(ui.ruler(sc["items"], "本金亏光" if pf_spot_only else "强平"))
                html(ui.evidence_line(sc["evidence"]))
                st.write("")
                html(ui.bullets(rep.narrative, sc["overall"]))
                st.caption("结论生成方式: %s · 规则: %s" % (rep.narrative_by, sc["rule"]))
                if llm.available_provider() and rep.narrative_by == "template" and st.button(
                        "让大模型用人话总结 (1–2 分钟, 写完逐个核对数字)", key="pf_narrate"):
                    if budget.take():
                        with st.spinner("大模型写结论中..."):
                            pcard.narrate(rep, log=get_log())
                        st.rerun()
                    else:
                        st.warning("大模型今日额度已用完 (%s)。上面的分点结论由模板生成, 数字完全一样。" % budget.status_text())

    with c3:
        with st.container(key="panel_03"):
            html(ui.panel_title("03", "调整方案", "重新打分"))
            if rep is None:
                st.caption("运行后显示。")
            else:
                for s in rep.suggestions:
                    html(ui.suggestion_card(s))
                    if s["account"] is None:
                        continue
                    b1, b2 = st.columns([2, 3])
                    if b1.button("套用", key="apply_" + s["key"], width="stretch"):
                        d = s["account"]
                        st.session_state["pf_account"] = pf.account_from_dict(d)
                        st.session_state["pf_ver"] += 1
                        st.session_state["pf_how"] = "套用方案: " + s["text"]
                        st.session_state["pf_prev_score"] = rep.scorecard["overall"]
                        st.session_state.pop("pf_report", None)
                        st.rerun()
                    with b2.popover("去 Bitget", width="stretch"):
                        st.caption("只打开 Bitget 交易页面, 不会自动下单; 数量要你自己在页面上输入。"
                                   "网址 %s 实测可用。" % links.VERIFIED_AT)
                        for k, step in enumerate(s["steps"]):
                            st.markdown("**%s**" % step["text"])
                            l1, l2 = st.columns(2)
                            if step["spot_url"]:
                                l1.link_button("rToken 现货" if step["kind"] == "position" else "BTC 现货", step["spot_url"],
                                               width="stretch")
                            if step["futures_url"]:
                                l2.link_button("永续合约", step["futures_url"], width="stretch")
                            if step["note"]:
                                st.caption(step["note"])

    if rep is not None:
        with st.container(key="panel_04"):
            html(ui.panel_title("04", "地震图上最深的 5 根", "各资产最差价同时出现 · 偏保守"))
            html(ui.worst_table(rep.worst_days))
            st.caption("表中历史日期发生了什么事件未核实, 本工具不写原因。")
            with st.expander("更多情景: 连续持有最差 · 预设假设情景 · 最差 10 天完整表"):
                x1, x2 = st.columns(2)
                x1.markdown("**连续持有最差 (收盘价)**")
                x1.dataframe(pd.DataFrame(rep.multi_days), width="stretch", hide_index=True)
                x2.markdown("**预设假设情景** (情景参数是假设, 不是预测)")
                x2.dataframe(pd.DataFrame(rep.presets), width="stretch", hide_index=True)
                st.dataframe(pd.DataFrame(rep.worst_days), width="stretch", hide_index=True)
            with st.expander("事实表与来源 (每个数字是哪次数据调用算出来的)"):
                render_facts(rep.facts)
            with st.expander("提示与规则"):
                for w in rep.warnings:
                    st.write("- " + w)
            with st.expander("本次数据调用记录 (原始)"):
                st.json(rep.provenance)

# ================================================================ 单笔
with tab_trade:
    def follow_up(text: str, last: TradeIdea | None) -> TradeIdea | None:
        """追问: 没提标的时沿用上一个想法, 只改用户这次提到的杠杆 / 方向 / 场景。"""
        if last is None:
            return None
        d = dataclasses.asdict(last)
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:x|X|×|倍|times?|-?fold)", text)
        if m:
            d["leverage"] = float(m.group(1))
        if re.search(r"做空|空单|short", text.lower()):
            d["side"] = "short"
        elif re.search(r"做多|多单|long", text.lower()):
            d["side"] = "long"
        if re.search(r"周末|weekend", text.lower()):
            d["scenario"] = "weekend"
        elif re.search(r"隔夜|财报|(?<!收)盘前|盘后|overnight|earnings", text.lower()):
            d["scenario"] = "overnight"
        if d == dataclasses.asdict(last) or validate(d):
            return None
        d["raw_text"], d["parsed_by"], d["note"] = text, "follow-up", "沿用上一个想法: %s" % last.raw_text
        return TradeIdea(**d)

    cols = st.columns(len(TRADE_EXAMPLES))
    for i, ex in enumerate(TRADE_EXAMPLES):
        if cols[i].button(ex, width="stretch", key="tr_ex_%d" % i):
            st.session_state["query"] = ex
    t_in, t_btn = st.columns([7, 1], vertical_alignment="bottom")
    text = t_in.text_input("用一句话描述你的交易想法 (也可以追问, 例如「如果降到 2 倍呢」)", key="query")
    st.caption("单笔模式要用 rToken 的真实小时线算周末重锚, 本地只缓存了 %s 这 %d 只; "
               "其他标的请用「组合」模式 (用正股 5 年日线重演, 全部 %d 只都能测)。"
               % ("、".join(sorted(universe.CACHED)), len(universe.CACHED), len(universe.SUPPORTED)))

    if t_btn.button("单笔压力测试", type="primary", width="stretch") and text.strip():
        log = get_log()
        idea, provs, how = parse(text)
        for p in provs:
            log.append(p)
        if idea is None:
            idea = follow_up(text, st.session_state.get("last_idea"))
            how = "追问: 沿用上一个想法" if idea else how
        if idea is None:
            st.error(how)
        else:
            st.session_state["last_idea"] = idea
            with st.spinner("取数与压力测试中..."):
                c = cardmod.build_card(idea, maint_margin=mm_haircut()[0], log=log, use_llm=False)
            st.session_state["card"] = (c, how)

    if "card" in st.session_state:
        c, how = st.session_state["card"]
        st.caption("解析: %s · %s" % ({k: c.idea[k] for k in ("symbol", "side", "leverage", "scenario")}, how))
        k1, k2 = st.columns([5, 4], gap="medium")
        with k1:
            with st.container(key="panel_t1"):
                # 标题只取「标的 方向 杠杆 · 场景」; 旧的「强平风险 高/中/低」不再显示, 以免和安全分的档位说法打架
                parts = c.headline.split(" · ")
                html(ui.panel_title("01", parts[0], " · ".join(x for x in parts[1:] if not x.startswith("强平风险"))))
                if c.scorecard:
                    sc = c.scorecard
                    html(ui.score_block(sc["overall"], sc["weakest_label"], score.grade(sc["overall"])))
                    html(ui.ruler(sc["items"]))
                    html(ui.evidence_line(sc["evidence"]))
                sym = c.idea["symbol"]
                l1, l2 = st.columns(2)
                l1.link_button("去 Bitget · r%sUSDT 现货" % sym, links.spot_url(sym), width="stretch")
                l2.link_button("去 Bitget · %sUSDT 永续" % sym, links.futures_url(sym), width="stretch")
        with k2:
            with st.container(key="panel_t2"):
                html(ui.panel_title("02", "结论"))
                html(ui.bullets(c.narrative, c.scorecard["overall"] if c.scorecard else None))
                st.caption("结论生成方式: %s" % c.narrative_by)
                if llm.available_provider() and c.narrative_by == "template" and st.button(
                        "让大模型用人话总结 (1–2 分钟, 写完逐个核对数字)", key="tr_narrate"):
                    if budget.take():
                        with st.spinner("大模型写结论中..."):
                            cardmod.narrate(c, log=get_log())
                        st.rerun()
                    else:
                        st.warning("大模型今日额度已用完 (%s)。上面的分点结论由模板生成, 数字完全一样。" % budget.status_text())
                st.info(c.tier_text)
        with st.container(key="panel_t3"):
            html(ui.panel_title("03", "事实表与来源"))
            render_facts(c.facts)
            with st.expander("提示与风险规则"):
                for w in c.warnings:
                    st.write("- " + w)
                st.write("**规则**: " + cardmod.RISK_RULE)
            with st.expander("本次数据调用记录 (原始)"):
                st.json(c.provenance)

# ================================================================ 雷达
with tab_radar:
    acc_r: pf.Account = st.session_state.get("pf_account")
    syms = sorted({p.symbol for p in acc_r.positions}) if acc_r else []
    with st.container(key="panel_r1"):
        html(ui.panel_title("05", "市场风险雷达", "只放和你持仓直接相关、来源可追溯的数据"))
        st.caption("不做新闻 / 政策解读: 那类内容无法逐句溯源, 而且 Bitget 自己的 AI 已经在做。"
                   "同一个数字尽量用多个来源交叉核对, 对不上会标黄。")
        c1, c2 = st.columns([1, 5], vertical_alignment="bottom")
        go = c1.button("取数 (约 8 秒)", type="primary", key="radar_run", width="stretch")
        if c2.button("重新取数 (清缓存)", key="radar_refresh"):
            get_radar.clear()
            go = True
        if go:
            with st.spinner("取市场数据中 (Yahoo · Deribit · CoinGecko · alternative.me · Nasdaq)..."):
                st.session_state["radar"] = get_radar(tuple(syms), get_log())
        rad = st.session_state.get("radar")
        if rad is None:
            st.caption("点「取数」显示: VIX · BTC/ETH 波动率指数 DVOL · 加密恐惧贪婪 · 加密总市值 · 价格交叉核对 · 你持仓标的的下次财报")
        else:
            cards = rad["metrics"]
            for i in range(0, len(cards), 3):
                cols = st.columns(3)
                for col, m in zip(cols, cards[i:i + 3]):
                    with col:
                        html(ui.metric_card(m))
                        with st.popover("来源", width="stretch"):
                            for s in m["sources"]:
                                st.code(s, language=None)
            st.write("")
            html(ui.panel_title("06", "你持仓标的的下次财报", "财报是隔夜跳空最常见的来源"))
            html(ui.earnings_table(rad["earnings"]))
            st.caption("Nasdaq 财报日历实测只覆盖约未来 50 天; 更远的按该标的历史财报间隔推算, 表里标「估算」。")
            failed = [p for p in rad["provenance"] if not p["ok"]]
            st.caption("本次共 %d 次数据调用, 失败 %d 次%s" % (len(rad["provenance"]), len(failed),
                                                          (": " + ", ".join(sorted({p["source"] for p in failed}))) if failed else ""))
            with st.expander("本次数据调用记录 (原始)"):
                st.json(rad["provenance"])

# ---------------------------------------------------------------- 数据源详情 (页尾)
with st.expander("数据源详情 · Bitget Skill 全部工具检测"):
    st.caption("算分用的数据: 美股与 BTC 日线来自 Yahoo Finance; rToken 小时线来自 Bitget 公共接口 (缓存)。"
               "Bitget 官方研究 Skill 本工具只用 BTC 波幅 (ATR), 其余工具不参与计分。")
    if st.button("开始检测 (约 25 秒)", key="probe_all_btn"):
        st.session_state["probe_all_result"] = mcp_health()
    h = st.session_state.get("probe_all_result")
    if h is not None:
        if h["server"] is None:
            st.write("连接失败: %s" % h["error"])
        else:
            rows = []
            for p in h["probes"]:
                ep = p["endpoint"]
                if p["ok"]:
                    note = "在用: " + SKILL_FALLBACK[ep] if ep in USED_SKILL_TOOLS else SKILL_FALLBACK.get(ep, "")
                else:
                    note = "我们改用: " + SKILL_FALLBACK.get(ep, "本工具不使用这类数据")
                rows.append({"Skill 工具": ep, "状态": "OK" if p["ok"] else "失败",
                             "耗时": "%d ms" % p["latency_ms"], "说明": note,
                             "对方服务器报的错": (p.get("error") or "")[:60]})
            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
            st.caption("失败的都是**对方服务器**在取上游数据时报的错 (alt_me_error / ConnectTimeout / all feeds errored), "
                       "2026-09-14 与 09-17 两次、两台机器结果一致。同样这几个上游源, 本工具直连全部正常 —— "
                       "所以雷达里的恐惧贪婪、加密总市值、美股价格照常有数据, 只是不经过 Skill。")


# ---------------------------------------------------------------- 顶栏 (最后再更新一次: 反映本次取数结果与 Skill 检测)
html(ui.header(*header_status(check_skill=True)), header_slot)
