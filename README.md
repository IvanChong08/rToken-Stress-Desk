# rToken Stress Desk 🧯

**开仓前的 rToken 压力测试台：用 5 年真实行情检验你的杠杆持仓。每个数字都有来源，大模型不产生任何数字，最终由你决定。**

*Pre-trade stress testing for Bitget rToken (tokenized US stocks): replay 5 years of real market data on your leveraged portfolio. Every number is traceable to a logged data call; the LLM never produces numbers.*

> Bitget AI Hackathon Genesis S2 · 赛道三 AI Trading Desk · 子题「决策压力测试」
> 数据截至 2026-09-16（行情实时取，README 里的示例数字会随行情变化）

---

## 30 秒跑起来

```bash
git clone https://github.com/IvanChong08/rToken-Stress-Desk.git
cd rToken-Stress-Desk
pip install -r requirements.txt
python -m streamlit run app.py
```

浏览器打开 `http://localhost:8501`，或者用 `http://localhost:8501/?demo=1` 直接看带结果的示例组合。

需要 **Python 3.10+** 和联网（行情来自 Yahoo Finance、Deribit、CoinGecko、Nasdaq、Bitget 公共接口）。
**不配任何 API key 也能完整使用**：不配时用规则解析 + 模板结论，所有计算不受影响。

---

## 为什么做这个

1. **rToken 7×24 交易，美股不是。** 周末的 rToken 价格来自做市商报价和用户挂单，美股开盘时重新锚定到正股（Bitget 学院）。**杠杆仓位在周末就可能被强平，哪怕周一价格又回来了。**
2. **散户按感觉定仓位**，手上没有历史基准。
3. **AI 工具会自信地说错数字。** 我们自己的记录：某次 AI 写的周报称「XRP 年初至今 +400%」，API 实际是 −41.4%；两段式（采集 + 核实）流水线两次都把恐惧贪婪指数说成 49，API 值是 27。
4. **现有的 AI 助手把代币化美股「按美股逻辑」解读**，看不到 rToken 特有的周末与抵押品风险。

所以这个工具让 AI 负责听懂问题、写人话，**每个数字都必须来自一次有记录的数据调用**。

---

## 三个模式

### 📊 组合压力测试（跨资产统一账户）
多只 rToken 仓位（杠杆或现货）+ USDT / BTC / ETH 抵押品。全仓下**强平只看总权益与总维持保证金，单笔杠杆不决定风险，名义仓位才决定**。

**支持 1601 只标的**——Bitget 上架的全部 rToken 中，Yahoo 有 5 年日线的那些；其中 **203 只有股票永续合约、可以加杠杆**，其余只能按现货持有（填了杠杆会被拦下来并告诉你改成现货）。清单由 `tools/fetch_rtoken_universe.py` 生成，见「标的清单怎么来的」。

- **中英文都能问**：「0.1 BTC 和 5000 USDT 做保证金，做多 NVDA 15000U」和 `long NVDA 15000, short SPY 5000, 0.1 BTC as margin` 都走规则解析，瞬间完成、不消耗大模型额度；追问同理（「把 MSTR 砍半呢」/ `halve MSTR`）
- **仓位可以是零碎份额**：Bitget rToken 数量精度 4 位 = 最小 0.0001 股，单笔最小 10 USDT（少数 20）——买不起整股正是代币化美股的意义，表格不限制整数
- **5 年逐日重演**（约 1,250 个交易日）：两种口径——「各资产最差价同时出现」（保守上限）和「收盘价」（乐观下限）
- **连续多日回撤**：每个起点持有 1–5 个交易日的最差权益
- **预设假设情景**：如「美股同步 −10% 且 BTC/ETH −20%」（情景参数是假设，不是预测）
- **反向压力测试**：最差那天放大几倍会强平；所有仓位同步不利变动多少会强平（加密抵押品跟跌 / 不动两种）
- **可执行调整方案**：同比例缩仓到 60 分、加密保证金换 USDT、砍半最拖累的仓位——每个方案都用同一套历史重新打分，并给出「每只标的从多少调到多少」+ 对应的 Bitget 交易页面链接

示例组合（0.1 BTC + 5,000 USDT 保证金，多 NVDA 15,000 / MSTR 8,000 / COIN 5,000，空 SPY 5,000，有效杠杆 2.7 倍）：

| 情景 | 账户亏损 | 是否强平 |
|---|---|---|
| 5 年最差单日 2024-08-05（保守口径） | −57.9%（其中 BTC 抵押品缩水 1,442 USDT） | 否 |
| **连续 5 日最差（2022-05-04 起）** | **−94.5%** | 否，但只剩约 800 USDT |
| 同步下跌多少会强平 | 加密抵押品跟跌 29.7% / 不动 36.2% | — |

### 🎯 单笔想法
一句话描述，例如「周五收盘前我想 3 倍做多 NVDA rToken 过周末」。**只覆盖 11 只**（NVDA / TSLA / AAPL / MSFT / AMZN / GOOGL / META / SPY / QQQ / COIN / MSTR）——周末重锚要用 rToken 的真实小时线，本地只缓存了这几只；其他标的用组合模式测（正股日线重演，不需要小时线）。

- **周末重锚跳空**：rToken 真实周末交易（2026-06 起）的每个周五收盘 → 周一开盘
- **5 年隔夜尾部**：正股最差单日（前收 → 当日极值）、≥10% 开盘跳空次数、按你的杠杆算强平命中
- **抵押品参考**：BTC 日均真实波幅（经 Bitget 官方研究 Skill 的 MCP 后端）

### 📡 市场风险雷达
只放和持仓风险直接相关、且来源可追溯的数据；**同一个数字尽量多来源交叉核对，对不上标黄**。

| 指标 | 来源 |
|---|---|
| VIX（美股波动率）+ 5 年百分位 | Yahoo（Cboe 指数） |
| BTC / ETH 波动率指数 DVOL + 百分位 | Deribit 官方 |
| 加密恐惧贪婪指数 | alternative.me（只有这一个免费来源，界面标明） |
| 加密总市值 / BTC 占比 | CoinGecko |
| BTC / ETH 现价 | **CoinGecko + Deribit + Yahoo 三个来源** |
| 美股价格 | **Nasdaq + Yahoo 两个来源** |
| 持仓标的下次财报日 | Nasdaq 财报日历（约未来 50 天）；更远按历史财报间隔推算并标「估算」 |

**不做新闻、政策解读、资金流**：无法逐句溯源，且与交易所自带的 AI 助手重复。

---

## 标的清单怎么来的

`data/rtoken_universe.json`（快照 2026-09-16）由三步生成，全部可复现：

1. **Bitget 现货**：`/api/v2/spot/public/symbols` 里 `baseCoin` 以 `r` 开头的全部上架交易对 —— 1653 只（本机连不上 Bitget，这步在 AWS 上跑）
2. **Yahoo 核对**：逐只查 5 年日线，**1601 只有**、52 只没有（多为新上市或已退市），没有的不收录 —— 取不到行情就没法压力测试
3. **能不能加杠杆**：Bitget 的合约里股票永续和币永续同名同构，API 没有字段能分开，而**币股同名很常见**——`BCHUSDT` 是比特币现金，但 Bitget 同时上了智利银行（BCH）的 rToken；`SUI`、`T`、`F`、`W`、`NMR` 都一样。所以用**价格判**：合约最新价 vs 正股实时价，差 ≤10% 判为股票永续（203 只），差 ≥40% 判为同名的币（43 只），**中间地带不下结论**（3 只：CL、NMR、VVV），一律按「不能加杠杆」处理

⚠️ 清单按**代码**直接对应 Yahoo，没有逐只核对公司名——理论上存在 Bitget 的 rXYZ 与 Yahoo 的 XYZ 不是同一家公司的可能。

---

## 安全分怎么算

```
每项得分 = 100 × (1 − 这个情景的亏损 ÷ 开仓时离强平的距离)      截断到 0–100
情景中触及强平                                              记 0 分
总分 = 各项中最低的一项                                      短板决定风险，不取平均
```

- 组合看三项：单日极端行情 / 连续 5 日下跌 / 预设双重打击情景
- 单笔看两项：5 年单日 / 周末持仓
- 分界：<30 危险，30–59 警惕，≥60 稳健
- **公式公开，任何人都能自己复算**；「离强平的距离」= 初始权益 − 初始维持保证金

另设 **证据可信度**（不影响分数）：样本量、数据源调用是否成功、数据新鲜度、用了哪些用户假设参数。

---

## 自然语言：规则优先，大模型只出指令

**规则直接处理（瞬间完成，不调用大模型）**
`5000U 做保证金, 做多 NVDA 1万` · `把 MSTR 砍半` · `把 MSTR 换成 AAPL` · `NVDA 改成 1万` · `去掉 COIN` ·
`BTC / ETH 全部换成 USDT` · `保证金再加 3000u` · `用 0.1 BTC 等额买入七巨头`（「七巨头」按公认定义展开为 AAPL/AMZN/GOOGL/META/MSFT/NVDA/TSLA）

**交给大模型（它只输出指令，计算仍由代码执行）**
`一半 NVDA 换成 SPY 空单` · `MSTR 减三成` · `平掉 COIN 再把 MSTR 砍半` · `再加 5000 TSLA 多单`

**三道防线**
1. **指令里的金额和比例必须出现在你的原话里**（「一半」=0.5、「1.5万」=15000、「减三成」=0.7），否则整条拒绝执行
2. **结论里的数字逐个核对**：大模型写的文字里只要出现事实表以外的数字，整段弃用改回模板
3. **不懂就反问，不猜**：「3大巨头」没有公认定义 → 问你是哪几只；「0.05 保证金」没写单位 → 问是 BTC 还是 USDT

---

## 每个数字都有来源

- 每次数据调用都记录：数据源、接口、参数、时间、耗时、成功与否、记录条数
- 写入**只可追加的哈希链日志**（`data/calls_*.jsonl`），每行带上一行的哈希；改动任何一行都会断链，界面上能一键校验
- 日志写入带**跨进程文件锁**：多个网页实例同时写也不会断链（有专门的并发测试）
- 界面上每个数字旁边都有「来源」按钮，点开就是那次调用的原始记录

---

## 大模型的角色（以及额度用完会怎样）

| 做什么 | 谁做 |
|---|---|
| 所有计算：重演、打分、强平距离、调整方案、雷达、交叉核对 | **代码** |
| 听懂常见说法 | **规则** |
| 听懂复杂说法 → 输出修改指令 | Qwen `qwen3.8-max` |
| 把事实表写成人话 | Qwen（写完逐个核对数字） |
| 写这个项目的代码 | Claude Code |

**额度用完不影响压力测试**：自动退回规则解析 + 模板结论，数字完全一样。内置每日用量闸门（默认 120 次/天，`LLM_DAILY_CAP` 可调），顶栏显示今日剩余次数。

```bash
# 可选
export BITGET_QWEN_API_KEY=...   # 或 QWEN_API_KEY / GEMINI_API_KEY
export LLM_DAILY_CAP=120
```

---

## 研究发现（实测，非推测）

1. **周末 rToken 价格对周一开盘几乎没有预测力。** 11 只标的 × 12 个周末（2026-06-05 ~ 09-11）逐小时检验：周五 16:00 → 周日 19:00（纽约）期间，用 rToken 价格预测周一开盘**比直接用周五收盘价还差**（R² −0.01 ~ −0.27，只有 33–50% 的周末更准）。预测力在**周日 20:00 突然出现**（R² 0.52），到开盘前 1 小时升到 0.92。周一盘前 rToken 与 Yahoo 正股盘前价中位差 0.05%（SPY）~ 0.39%（MSTR）——**rToken 在跟着正股走，不是在领先发现价格**。
   → 含义：周末的 rToken 波动更像噪音，**却照样可能触发 7×24 强平**。（脚本见 `research/price_discovery/`）
2. **单日扛得住不代表多日扛得住。** 示例组合 5 年最差单日亏 57.9%，但连续 5 日最差亏 94.5%。
3. **抵押品与仓位会同时挨打。** 2024-08-05：11 只标的平均盘中较前收 −11.7%，同日 BTC −20.0%、ETH −28.9%。

---

## 数据源状态（2026-09-16 实测）

| 来源 | 用途 | 状态 |
|---|---|---|
| Yahoo Finance | 美股 / BTC / ETH / VIX 日线 | ✅ |
| Bitget 公共接口 | rToken 小时线（仓库内缓存 JSON，带来源记录） | ✅ 需在能连 api.bitget.com 的机器上刷新 |
| Deribit | DVOL 波动率指数、指数价格 | ✅ |
| CoinGecko | 加密价格、总市值 | ✅ |
| alternative.me | 恐惧贪婪指数 | ✅ |
| Nasdaq | 财报日历、报价交叉核对 | ✅（日历只覆盖约未来 50 天） |
| `bitget-signal` MCP 后端 | BTC 波幅（ATR） | ⚠️ 19 个工具里实测只有 2 个稳定；失败情况在界面上如实显示 |
| Stooq / StockAnalysis / FRED / CoinDesk | — | ❌ 拦截程序访问 / 超时 / 需 API key，因此不用 |

---

## 局限（必须知道）

- **周末样本只有 12 个**（rToken 真实周末交易 2026-06 才开始），按我们的分级只能当「案例」看，不是统计结论
- **维持保证金率、BTC/ETH 抵押折算率是你填的假设**，不是 Bitget 官方数值；分档维持保证金未建模
- **未计手续费、资金费、滑点、强平罚金**
- 「各资产最差价同时出现」偏保守，收盘价口径偏乐观，**真实情况在两者之间**
- BTC-USD / ETH-USD 是 UTC 日线，美股是纽约交易日，**同日对齐是近似**
- 预设情景里 BTC 和 ETH 按同幅变动，是简化
- 历史日期发生了什么事件**未核实**，本工具不写原因
- **标的清单是快照**（2026-09-16），Bitget 上下架后不会自动更新；重跑 `tools/fetch_rtoken_universe.py` 才会变
- **标的与 Yahoo 的对应按代码直接匹配**，未逐只核对公司名
- **历史不代表未来。本工具不是投资建议。**

---

## 测试

```bash
python tests/test_portfolio_engine.py       # 组合引擎（合成行情 + 手算期望值）
python tests/test_score.py                  # 安全分
python tests/test_understand.py             # 修改指令执行器（编造的数值必须被拒绝）
python tests/test_portfolio_idea.py         # 组合解析与追问
python tests/test_idea_rules.py             # 单笔解析
python tests/test_links.py                  # 调整步骤与 Bitget 链接
python tests/test_verifier.py               # 大模型数字核对器
python tests/test_calllog_concurrency.py    # 哈希链并发写入
python tests/test_budget.py                 # 大模型用量闸门
python tests/test_universe.py               # 标的清单（三个集合的关系、小写英文词不当代码）
python tests/test_holdings.py               # 持仓表格解析（买入价 / 数量 / 现货）
python tests/test_spot_only.py              # 全现货零保证金账户
python tests/test_app.py                    # Streamlit 全流程（无浏览器）
```

所有期望值都是手算后写进测试的，不是拿被测代码生成的。

---

## 目录

```
app.py                  网页（组合 / 单笔 / 雷达 三个标签页）
desk/portfolio.py       账户模型与 5 年逐日重演引擎
desk/score.py           安全分与证据可信度
desk/suggest.py         调整方案搜索（二分）
desk/portfolio_card.py  组合研究任务：事实表 + 情景表 + 结论
desk/card.py            单笔研究任务
desk/radar.py           市场风险雷达（多来源交叉核对）
desk/portfolio_idea.py  自然语言：规则解析 + 大模型指令执行
desk/links.py           调整步骤 → Bitget 交易页面链接
desk/provenance.py      哈希链调用日志（文件锁）
desk/budget.py          大模型每日用量闸门
desk/ui.py              界面样式与 HTML 片段
data/cache/bitget/      rToken 小时线缓存（线上直接读）
research/               一次性研究脚本（周末价格发现）
tools/                  在能连 Bitget 的机器上刷新缓存
```

---

## License

MIT，见 [LICENSE](LICENSE)。**非投资建议；历史数据不保证未来结果。**
