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

st.set_page_config(page_title="rToken Stress Desk", page_icon="🧯", layout="wide", initial_sidebar_state="auto")
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


def set_query(key: str, text: str, run: bool = False) -> None:
    """把示例句子填进输入框。必须当 on_click 回调用 —— 回调在下一轮渲染前执行,
    这时改「已经被 text_input 占用的 key」才是合法的 (09-17 云端实测炸过一次)。"""
    st.session_state[key] = text
    if run:
        st.session_state["pf_pending_run"] = True


def account_key(a: pf.Account) -> tuple:
    """账户指纹, 用来判断表格改没改过。四舍五入是必须的 ——
    名义价值 = 数量 × 当前价, 而当前价每 10 分钟刷新一次, 不取整会被浮点噪声反复触发重算。"""
    return (tuple(sorted((p.symbol, p.side, p.kind, round(p.notional, 2), round(p.entry or 0, 4))
                         for p in a.positions)),
            round(a.usdt, 2), round(a.btc, 6), round(a.eth, 6), a.maint_margin, a.btc_haircut)


def mm_haircut() -> tuple[float, float]:
    return st.session_state.get("mm_pct", 1.0) / 100, st.session_state.get("haircut_pct", 95) / 100


def sidebar_status(check_skill: bool) -> list[tuple]:
    """侧栏的运行状态行 (ok, 名称, 右侧说明)。check_skill=False 时跳过 Skill 网络检测, 先显示「检测中」。"""
    last = st.session_state.get("pf_report")
    yahoo_ok = None if last is None else all(p.get("ok") for p in last.provenance if p.get("source") == "yahoo")
    asof = rtoken_cache_asof()
    skill = (skill_in_use_ok(), "可用" if skill_in_use_ok() else "对方服务器故障") if check_skill else ("pending", "检测中")
    provider, left = llm.available_provider(), budget.remaining()
    log_ok, log_text = None, "暂无记录"
    if os.path.exists(LOG_PATH):
        ok, n = verify_log(LOG_PATH)
        log_ok, log_text = ok, "%d 条 · %s" % (n, "链完整" if ok else "链损坏")
    return [
        (yahoo_ok, "美股 / BTC 行情", "Yahoo"),
        (bool(asof), "rToken 行情", ("截至 %s" % asof) if asof else "缓存读取失败"),
        (skill[0], "Bitget Skill", skill[1]),
        (True if (provider and left) else (False if provider else None), "大模型",
         ("%s 剩 %d 次" % (provider.upper(), left)) if provider else "未配置"),
        (log_ok, "调用日志", log_text),
    ]


# ---------------------------------------------------------------- 侧栏 (假设参数 + 运行状态)
with st.sidebar:
    html(ui.brand())
    html(ui.side_head("假设参数"))
    st.slider("维持保证金率 %", 0.5, 5.0, 1.0, 0.5, key="mm_pct",
              help="账户权益低于「名义仓位 × 这个比例」就被强平。你设定的假设值, 不是 Bitget 官方数值。")
    st.slider("BTC / ETH 折算率 %", 50, 100, 95, 5, key="haircut_pct",
              help="BTC、ETH 作保证金时按多少比例计入权益 (两者共用一个值, 这是简化)。你设定的假设值, 不是 Bitget 官方数值。")
    html(ui.assume_note("两项都是你设定的假设值, 不是 Bitget 官方数值。改动后要重新运行。"))
    html(ui.side_head("运行状态"))
    status_slot = st.empty()
    html(ui.status_rows(sidebar_status(check_skill=False)), status_slot)
    st.caption("支持 %s 只 rToken · 其中 %s 只能加杠杆" % (format(len(universe.SUPPORTED), ","), len(universe.LEVERAGE)))

tab_pf, tab_trade, tab_radar = st.tabs(["组合压力测试", "单笔想法", "市场雷达"])

# ================================================================ 组合
with tab_pf:
    if "pf_account" not in st.session_state:
        st.session_state["pf_account"] = pidea.parse_rules(PF_EXAMPLES[0])
        st.session_state["pf_ver"] = 0
    # 空状态卡片的「用示例试一下」: 上一轮挂的待办, 在这里 (渲染开始前) 执行
    if st.session_state.pop("pf_pending_run", False):
        st.session_state["pf_account"] = pidea.parse_rules(PF_EXAMPLES[0])
        st.session_state["pf_ver"] += 1
        st.session_state["pf_how"] = "由规则解析为新组合 (示例)"
        _log = get_log()
        _a = st.session_state["pf_account"]
        try:
            with st.spinner("取 5 年行情并重演中..."):
                _panel = get_panel(tuple(sorted({p.symbol for p in _a.positions})), _log)
                st.session_state["pf_report"] = pcard.build_report(_a, _panel, log=_log, use_llm=False)
        except Exception as e:      # noqa: BLE001
            st.error("取行情失败: %s。等十几秒再点一次「运行压力测试」。" % str(e)[:120])

    acc: pf.Account = st.session_state["pf_account"]
    rep_before = st.session_state.get("pf_report")

    # 新手引导: 没跑过、也没手动关掉时才显示
    if rep_before is None and not st.session_state.get("guide_off"):
        with st.container(key="guide"):
            g_left, g_right = st.columns([13, 2], vertical_alignment="center")
            with g_left:
                html(ui.guide_strip())
            if g_right.button("收起", key="dismiss_guide", width="stretch"):
                st.session_state["guide_off"] = True

    run_now = False
    with st.container(key="card_hold"):
        html(ui.card_head("你的持仓", "一句话描述, 或直接改下面的表格 · 中英文都行"))
        c_in, c_btn = st.columns([8, 2], vertical_alignment="bottom")
        pq = c_in.text_input("用一句话描述持仓, 或者追问修改", key="pf_query", placeholder=PF_EXAMPLES[0],
                             label_visibility="collapsed")
        go = c_btn.button("运行压力测试", type="primary", key="pf_run", width="stretch")

        # chip 必须走 on_click 回调: 输入框 (key="pf_query") 已经在上面渲染过了,
        # 在同一轮里直接写这个 key 会被 Streamlit 拒绝 (StreamlitAPIException)。
        # 回调是在下一轮渲染「之前」跑的, 那时改这个 key 才合法。
        chips = st.columns(len(PF_EXAMPLES) + len(PF_FOLLOWUPS))
        for i, ex in enumerate(PF_EXAMPLES):
            chips[i].button("示例 %d" % (i + 1), help=ex, width="stretch", key="pf_ex_%d" % i,
                            on_click=set_query, args=("pf_query", ex))
        for j, q in enumerate(PF_FOLLOWUPS):
            chips[len(PF_EXAMPLES) + j].button(q, width="stretch", key="pf_fu_%d" % j,
                                               on_click=set_query, args=("pf_query", q))

        # 一句话有内容、且和上次解析过的不一样时才解析, 再用解析后的组合去跑 (两步合成一个按钮)。
        # 必须比对「上次解析过的那句」: 否则输入框里残留的旧句子会在每次点运行时把你手改的表格冲掉。
        if go and pq.strip() and pq.strip() != st.session_state.get("pf_parsed_text"):
            st.session_state["pf_parsed_text"] = pq.strip()
            log = get_log()
            mm0, hc0 = mm_haircut()
            panel_for_price = get_panel(tuple(sorted({p.symbol for p in acc.positions})), log)
            new, how = pidea.edit(pq, acc, panel_for_price.btc_price, panel_for_price.eth_price)   # 规则优先 (快)
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
                st.session_state["pf_prev_score"] = rep_before.scorecard["overall"] if rep_before is not None else None
                st.session_state["pf_account"] = acc = new
                st.session_state["pf_ver"] += 1
                st.session_state["pf_how"] = how
                run_now = True
        if st.session_state.get("pf_how"):
            st.caption("解析: " + st.session_state["pf_how"])

        ver = st.session_state["pf_ver"]
        auto = st.checkbox("用 数量 × 当前价 自动算名义价值", value=True, key="pf_auto")
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
        mm, haircut = mm_haircut()

        rows = edited.to_dict("records")
        # 手动加的行要补取当前价: prices 只覆盖了进来时就有的持仓, 新打的标的取不到价,
        # 名义价值算不出来, 那一行连同你填的买入价数量都会被丢掉 (09-17 Ivan 实测)
        new_syms = tuple(sorted({str(r.get("标的") or "").strip().upper() for r in rows} & SUPPORTED - set(prices)))
        if new_syms:
            prices = {**prices, **get_prices(new_syms)}
        positions, skipped = holdings.parse_rows(rows, prices, auto, SUPPORTED)
        for sk in skipped:
            st.warning("这一行没有计入: " + sk)
        cur = pf.Account(positions, usdt, btc, mm, haircut, eth, haircut)
        err = pf.validate_account(cur)
        if err:
            st.warning(err)
        else:
            pxs = get_crypto_prices()
            equity = pf.equity0(cur, pxs.get("BTC", 0), pxs.get("ETH", 0))
            gross = pf.gross_notional(cur)
            upnl = pf.unrealized_pnl(cur)
            coll = "%s USDT" % format(round(cur.usdt), ",")
            if cur.btc:
                coll += " + %.4g BTC" % cur.btc
            if cur.eth:
                coll += " + %.4g ETH" % cur.eth
            html(ui.summary_line([
                ("保证金", coll),
                ("账户权益", format(round(equity), ",")),
                ("总仓位", format(round(gross), ",")),
                ("有效杠杆", ("%.2f×" % (gross / equity)) if equity > 0 else "—"),
                ("未实现盈亏", "%s%s" % ("+" if upnl > 0 else ("−" if upnl < 0 else ""), format(round(abs(upnl)), ","))),
            ]))
            # 改完表格的人手就停在表格这儿, 上面那个按钮要滚回去才够得着 -> 这里再放一个
            _rep0 = st.session_state.get("pf_report")
            stale = _rep0 is not None and account_key(cur) != account_key(pf.account_from_dict(_rep0.account))
            note_slot = st.empty()          # 先占位: 点了按钮就要重跑, 那就别再说「结果是旧的」
            go_bottom = st.button("运行压力测试" if not stale else "用改过的持仓重新运行",
                                  type="primary", key="pf_run_bottom", width="stretch")
            if go_bottom:
                go = True
            if stale and not go:
                html(ui.stale_note("表格改过了, 下面的分数还是上一次的结果"), note_slot)

    # ?demo=1: 打开页面就自动跑一次示例组合 (给评委 / 录屏用); 只在本会话还没有报告时触发
    demo_autorun = st.query_params.get("demo") == "1" and "pf_report" not in st.session_state
    # ⚠️ 这里不能「一改就自动重算」: 表格每提交一格都会重跑, 用户还没打完就被冲掉 (09-17 实测)。
    # 只用 stale 判断「结果是不是上一次的」, 显示提示 + 一个就近的按钮, 由用户决定什么时候跑。
    if (go or run_now or demo_autorun) and not err:
        log = get_log()
        try:
            with st.spinner("取 5 年行情并重演中..."):
                panel = get_panel(tuple(sorted({p.symbol for p in cur.positions})), log)
                st.session_state["pf_account"] = cur
                st.session_state["pf_report"] = pcard.build_report(cur, panel, log=log, use_llm=False)
        except Exception as e:      # noqa: BLE001  取数失败要给人话, 不能甩一个 Streamlit 报错页
            st.error("取行情失败, 没能跑完压力测试: %s。数据源是 Yahoo Finance, 偶尔会限流 —— "
                     "等十几秒再点一次「运行压力测试」通常就好了。" % str(e)[:120])
    rep = st.session_state.get("pf_report")

    # ---- 还没有结果: 用「你想做什么」代替空图表 ----
    if rep is None:
        with st.container(key="card_empty"):
            html(ui.card_head("你想做什么?", "三种用法, 选一个开始"))
            e1, e2, e3 = st.columns(3, gap="medium")
            with e1:
                with st.container(key="ecard_1"):
                    html(ui.empty_card("◧", "我有一个组合", "多只 rToken + 保证金, 想知道什么行情会让我爆仓"))
                    st.button("用示例试一下", key="empty_demo", type="primary", width="stretch",
                              on_click=set_query, args=("pf_query", PF_EXAMPLES[0]), kwargs={"run": True})
            with e2:
                with st.container(key="ecard_2"):
                    html(ui.empty_card("◈", "我只想测一笔", "「3 倍做多 NVDA 过周末」会怎样 —— 周末重锚跳空 + 5 年隔夜尾部"))
                    st.caption("↑ 点上面的「单笔想法」标签")
            with e3:
                with st.container(key="ecard_3"):
                    html(ui.empty_card("◎", "先看市场温度", "VIX、BTC 波动率、恐惧贪婪、你持仓标的的下次财报日"))
                    st.caption("↑ 点上面的「市场雷达」标签")

    if rep is not None:
        sc = rep.scorecard
        # 全是现货的账户不会被强平, 尺子量的是亏掉多少本金
        pf_spot_only = bool(rep.account["positions"]) and all(p.get("kind") == "spot" for p in rep.account["positions"])
        worst_loss = max([100 * max(rep.daily_loss)] + [r["账户亏损 %"] for r in rep.multi_days[:1]])
        liq_hit = any(i["liquidated"] for i in sc["items"])
        if pf_spot_only:
            tail = "现货不会被强平, 但这笔亏损照样发生。"
        elif liq_hit:
            tail = "—— 已经触及强平线。"
        else:
            tail = "那时离强平还剩 %d%% 的缓冲。" % round(sc["overall"])
        verdict = "历史上最坏的情况会让你亏掉 <b>%.1f%%</b> 的本金, %s" % (worst_loss, tail)
        rule_short = ("分数 = 100 − 用掉的强平距离; 触及强平记 0 分。60 分以上算稳健, 30 分以下算危险。"
                      if not pf_spot_only else "全是现货: 分数 = 100 − 亏掉的本金比例。60 分以上算稳健。")

        with st.container(key="card_score"):
            html(ui.card_head("安全分", "5 年 %d 个交易日重演 · 总分取三项里最低的一项" % len(rep.daily_dates)))
            html(ui.score_grid(sc["overall"], score.grade(sc["overall"]), sc["weakest_label"],
                               sc["items"], verdict, rule_short))
            if st.session_state.get("pf_prev_score") is not None:
                html(ui.compare_line(st.session_state["pf_prev_score"], sc["overall"]))
            # 分点结论的第一条是「安全总分」, 和上面的四格分数卡重复 -> 这里不再重复一遍
            _lines = [l for l in rep.narrative.splitlines() if "**安全总分**" not in l]
            html(ui.bullets("\n".join(_lines), sc["overall"]))
            b_l, b_r = st.columns([3, 2])
            with b_l:
                if llm.available_provider() and rep.narrative_by == "template" and st.button(
                        "让大模型用人话总结 (1–2 分钟, 写完逐个核对数字)", key="pf_narrate", width="stretch"):
                    if budget.take():
                        with st.spinner("大模型写结论中..."):
                            pcard.narrate(rep, log=get_log())
                        st.rerun()
                    else:
                        st.warning("大模型今日额度已用完 (%s)。上面的分点结论由模板生成, 数字完全一样。" % budget.status_text())
            b_r.caption("结论生成方式: %s" % rep.narrative_by)
            with st.expander(("亏损尺" if pf_spot_only else "强平距离尺") + " · 打分规则原文"):
                html(ui.ruler(sc["items"], "本金亏光" if pf_spot_only else "强平"))
                st.caption("规则: " + sc["rule"])

        with st.container(key="card_plans"):
            html(ui.card_head("调整方案", "每个方案都用同一套历史重新打分"))
            cols = st.columns(max(len(rep.suggestions), 1), gap="medium")
            for col, s in zip(cols, rep.suggestions):
                with col:
                    html(ui.suggestion_card(s))
                    if s["account"] is None:
                        continue
                    b1, b2 = st.columns(2)
                    # 不在这里 st.rerun(): 按钮点击本来就会触发一次重跑, 多喊一次会让
                    # 带版本号的控件键 (pf_usdt_N ...) 在 AppTest 里对不上状态
                    if b1.button("套用", key="apply_" + s["key"], type="primary", width="stretch"):
                        st.session_state["pf_account"] = pf.account_from_dict(s["account"])
                        st.session_state["pf_ver"] += 1
                        st.session_state["pf_how"] = "套用方案: " + s["text"]
                        st.session_state["pf_prev_score"] = sc["overall"]
                        st.session_state.pop("pf_report", None)
                    with b2.popover("去 Bitget", width="stretch"):
                        st.caption("只打开 Bitget 交易页面, 不会自动下单; 数量要你自己在页面上输入。"
                                   "网址 %s 实测可用。" % links.VERIFIED_AT)
                        for step in s["steps"]:
                            st.markdown("**%s**" % step["text"])
                            l1, l2 = st.columns(2)
                            if step["spot_url"]:
                                l1.link_button("rToken 现货" if step["kind"] == "position" else "BTC 现货",
                                               step["spot_url"], width="stretch")
                            if step["futures_url"]:
                                l2.link_button("永续合约", step["futures_url"], width="stretch")
                            if step["note"]:
                                st.caption(step["note"])

        with st.container(key="card_quake"):
            html(ui.card_head("放回过去 5 年的每一天",
                              "%s → %s · 每根线 = 当天账户亏损 (各资产最差价同时出现)"
                              % (rep.daily_dates[0], rep.daily_dates[-1])))
            html(ui.seismograph(rep.daily_dates, rep.daily_loss, rep.liq_loss))
            with st.expander("地震图上最深的 5 根 · 更多情景"):
                html(ui.worst_table(rep.worst_days))
                st.caption("表中历史日期发生了什么事件未核实, 本工具不写原因。")
                x1, x2 = st.columns(2)
                x1.markdown("**连续持有最差 (收盘价)**")
                x1.dataframe(pd.DataFrame(rep.multi_days), width="stretch", hide_index=True)
                x2.markdown("**预设假设情景** (情景参数是假设, 不是预测)")
                x2.dataframe(pd.DataFrame(rep.presets), width="stretch", hide_index=True)
                st.dataframe(pd.DataFrame(rep.worst_days), width="stretch", hide_index=True)

        html(ui.evidence_bar(sc["evidence"]))
        f1, f2, f3 = st.columns(3)
        with f1.expander("事实表与来源 (%d 条)" % len(rep.facts)):
            render_facts(rep.facts)
        with f2.expander("提示与规则"):
            for w in rep.warnings:
                st.write("- " + w)
        with f3.expander("本次数据调用记录 (%d 次)" % len(rep.provenance)):
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
        cols[i].button(ex, width="stretch", key="tr_ex_%d" % i, on_click=set_query, args=("query", ex))
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
            st.caption("点「取数」显示: 波动率 (VIX · BTC/ETH DVOL · 期权看跌看涨比) · 情绪与规模 · "
                       "价格多来源交叉核对 · 持仓标的的下次财报与内部人交易")
        else:
            by_key = {m["key"]: m for m in rad["metrics"]}

            def num(key, split_unit=""):
                """把 Metric 的 display 拆成 数字 + 单位, 分开排才不会折行。"""
                m = by_key.get(key) or {}
                d = m.get("display", "—")
                head = d.split(" · ")[0].split(" (")[0]
                if split_unit and split_unit in head:
                    head = head.replace(split_unit, "").strip()
                return head, m

            def pct_of(m):
                """从 display 里取出百分位 (只用来画细条, 数字本身仍来自 Metric)。"""
                mm = re.search(r"百分位\s*(\d+(?:\.\d+)?)%", (m or {}).get("display", ""))
                return float(mm.group(1)) if mm else None

            # ---- 波动率 ----
            html(ui.sec_head("波动率", "· 越高说明市场越紧张"))
            vix_v, vix_m = num("vix")
            btc_v, btc_m = num("dvol_btc")
            eth_v, eth_m = num("dvol_eth")
            pc_v, pc_m = num("pc_btc")
            html(ui.tiles([
                {"label": "VIX 美股波动率", "value": vix_v, "pct": pct_of(vix_m),
                 "note": (vix_m.get("display", "").split(" · ")[1:] or [""])[0], "ok": vix_m.get("status") == "ok"},
                {"label": "BTC 波动率 DVOL", "value": btc_v, "pct": pct_of(btc_m),
                 "note": (btc_m.get("display", "").split(" · ")[1:] or [""])[0] + " · 你的保证金就是 BTC",
                 "ok": btc_m.get("status") == "ok"},
                {"label": "ETH 波动率 DVOL", "value": eth_v, "pct": pct_of(eth_m),
                 "note": (eth_m.get("display", "").split(" · ")[1:] or [""])[0] + " · Deribit 官方",
                 "ok": eth_m.get("status") == "ok"},
                {"label": "BTC 期权 看跌/看涨", "value": pc_v, "pct": None,
                 "note": pc_m.get("note", ""), "ok": pc_m.get("status") == "ok"},
            ]))

            # ---- 情绪与规模 ----
            html(ui.sec_head("情绪与规模"))
            fng_m = by_key.get("fng", {})
            fng_head = fng_m.get("display", "—").split(" · ")[0]
            fng_num = fng_head.split(" ")[0]
            ctx_m = by_key.get("cg_global", {})
            ctx_d = ctx_m.get("display", "—")
            mcap = ctx_d.split(" 万亿美元")[0] if "万亿美元" in ctx_d else ctx_d.split(" · ")[0]
            dom = re.search(r"BTC 占比\s*([\d.]+)%", ctx_d)
            html(ui.tiles([
                {"label": "加密恐惧贪婪", "value": fng_num, "unit": fng_head.replace(fng_num, "").strip(" ()"),
                 "pct": float(fng_num) if fng_num.replace(".", "").isdigit() else None,
                 "note": (fng_m.get("display", "").split(" · ")[1:] or [""])[0] + " · 只有这一个免费来源, 无法交叉核对",
                 "ok": fng_m.get("status") == "ok"},
                {"label": "加密总市值", "value": mcap, "unit": "万亿美元", "pct": None,
                 "note": (re.search(r"\(24h [^)]+\)", ctx_d).group(0) if re.search(r"\(24h [^)]+\)", ctx_d) else "") + " · CoinGecko",
                 "ok": ctx_m.get("status") == "ok"},
                {"label": "BTC 占比", "value": dom.group(1) if dom else "—", "unit": "%", "pct": None,
                 "note": "占比越高说明资金越集中在 BTC", "ok": ctx_m.get("status") == "ok"},
            ]))

            # ---- 价格交叉核对 (六张同构卡片 -> 一张表) ----
            html(ui.sec_head("价格交叉核对", "· 差异超过阈值会标黄"))
            rows, px_metrics = [], [m for m in rad["metrics"] if m["key"].endswith("_px") or "价格" in m["label"] or "现价" in m["label"]]
            for m in px_metrics:
                parts = [x.strip() for x in m["display"].split(" · ")]
                diff = next((x for x in parts if x.startswith(("差异", "最大差异"))), "—")
                rows.append({"symbol": m["label"].split(" ")[0], "sources_text": [x for x in parts if not x.startswith(("差异", "最大差异"))],
                             "diff": diff.replace("最大差异", "").replace("差异", "").strip(), "warn": m["status"] != "ok"})
            if rows:
                html(ui.crosscheck_table(rows))
                st.caption("Yahoo 是收盘价, 另两家是实时价 —— 盘后本来就有差。差异只用来发现「某一家明显不对」, 不是套利信号。")

            # ---- 持仓标的: 财报 + 内部人 ----
            html(ui.sec_head("你持仓标的", "· 财报和内部人卖出都是隔夜跳空的常见来源"))
            st.markdown("**下次财报**")
            html(ui.earnings_table(rad["earnings"]))
            st.caption("Nasdaq 财报日历实测只覆盖约未来 50 天; 更远的按历史财报间隔推算, 标「估算」。")
            st.write("")
            st.markdown("**内部人交易 (SEC Form 4)**")
            if rad.get("insider"):
                html(ui.insider_table(rad["insider"]))
                st.caption("只把 P (公开市场买入) 和 S (公开市场卖出) 算成买卖; 授予、行权、代扣税不算 —— "
                           "高管「卖出」里很大一部分其实是行权和代扣税。金额只统计已解析的那几份, 表里标了覆盖范围。")
            else:
                st.caption("持仓里没有个股 (ETF 没有内部人)。")

            with st.expander("每个数字的来源 (%d 次调用)" % len(rad["provenance"])):
                for m in rad["metrics"]:
                    st.markdown("**%s** — %s" % (m["label"], m["display"]))
                    for src in m["sources"]:
                        st.code(src, language=None)
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


# ------------------------------------------------ 侧栏状态最后再刷一次 (这时才做 Skill 网络检测, 不拖慢首屏)
html(ui.status_rows(sidebar_status(check_skill=True)), status_slot)
