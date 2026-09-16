# rToken Stress Desk 🧯

**Pre-trade stress testing for Bitget rToken (tokenized US stocks). Every number has a source; the LLM never generates numbers.**

**开仓前的 rToken 压力测试台：用一句话描述你的杠杆想法，系统用历史数据检验它；每个数字都有来源，大模型不生成数字，最终由你决定。**

> Bitget AI Base Camp Hackathon S2 · Track 3 AI Trading Desk · Sub-theme: Decision Stress Testing
> Status: work in progress (data as of 2026-09-14)

---

## Why

- **rToken trades 24/7, the underlying US market does not.** Weekend rToken prices come from market-maker quotes and user orders; when Nasdaq/NYSE reopen, rToken prices re-anchor to the underlying, which can create a gap (Bitget Academy). A leveraged rToken position can also be liquidated *during the weekend*, even if the price recovers by Monday.
- **Retail leverage traders size positions by gut**, with no historical base rates at hand.
- **AI chat tools state wrong numbers confidently.** From our own logs: an LLM-written weekly report claimed "XRP +400% YTD" when the API showed −41.4%; a two-stage LLM pipeline (collect + verify) both reported the Fear & Greed Index as 49 when the API value was 27.

So the desk lets the AI understand the question and write the explanation, while **every number comes from a logged data call**.

## One complete research task

Input: *"周五收盘前我想 3 倍做多 NVDA rToken 过周末"* (3x long NVDA rToken over the weekend)

1. **Parse** the idea into symbol / side / leverage / scenario (LLM; falls back to a rule parser when no LLM is available).
2. **Weekend re-anchoring analysis** — every Friday close → Monday open since real weekend trading began (June 2026): weekend rToken drift, remaining re-anchoring gap at the open, worst adverse move during the weekend.
3. **Tail reference** — 5 years of underlying daily data: worst single-day adverse moves (previous close → intraday extreme) and how often they would breach an approximate liquidation line at your leverage.
4. **Collateral context** — BTC daily ATR via Bitget's official `bitget-signal` Skill backend (if you post BTC as margin).
5. **Rule-based verdict** — liquidation-risk level and a suggested leverage cap, derived from published rules, plus the equity loss the historical worst case would cause.
6. **Conclusion card** — each number has a "source" button showing the exact data call (endpoint, params, time, latency). The LLM narrative is checked: any number not in the fact table causes it to be discarded in favour of a template.

Follow-ups work: *"如果降到 2 倍呢"* reuses the previous idea and only changes the leverage.

## Findings so far (observed, data as of 2026-09-14)

**Weekend re-anchoring (12 real weekend-trading weekends, 2026-06-05 → 2026-09-11 — case-study tier, not statistics):**

| rToken | median \|weekend drift\| | median \|Monday re-anchoring gap\| | max \|re-anchoring gap\| | worst weekend adverse move (long) |
|---|---|---|---|---|
| NVDA | 1.02% | 0.17% | 0.48% | −3.52% |
| MSTR | 2.10% | 0.33% | 1.61% | −3.66% |
| COIN | 1.37% | 0.43% | 1.44% | −2.11% |
| SPY | 0.47% | 0.06% | 0.22% | −0.85% |

Weekend rToken prices already reflect most of the Friday → Monday move; the remaining gap at the open is small. **But 12 samples in a calm period do not prove safety.**

**5-year underlying tail (Yahoo daily, 2021-09 → 2026-09):**

| Stock | \|opening gap\| ≥10% | worst single-day adverse move (long) | approx. liquidation breaches at 3x / 5x / 10x |
|---|---|---|---|
| NVDA | 6 | −18.17% (2025-01-27) | 0 / 0 / 16 |
| MSTR | 13 | −29.28% (2024-08-05) | 0 / 12 / 95 |
| COIN | 14 | −31.29% (2022-05-11) | 0 / 8 / 87 |
| SPY | 0 | −7.16% (2025-04-10) | 0 / 0 / 0 |

Example: NVDA 3x never hit the approximate liquidation line in 5 years, **but the worst day would still have cost about 54.5% of margin** — "low liquidation risk" is not "safe".

## Data sources (zero-trust)

| Source | Used for | Status (2026-09-14) |
|---|---|---|
| Yahoo Finance chart API | Underlying daily OHLCV | ✅ ~0.2–0.7 s |
| Bitget public market API | rToken hourly candles (`RNVDAUSDT` …), US stock futures | ✅ from a server; cached as JSON with provenance |
| `bitget-signal` MCP backend (official installer URL) | Skill tools (BTC ATR, crypto price, health probes) | ⚠️ 19 tools listed; **2 of 7 health probes working** — failures shown live in the app |

Notes:
- The official skill prompts ask the AI to hide provider names and say only "data temporarily unavailable" when a tool fails. This desk instead **shows which tool failed and how long it took**.
- Bitget rToken candles before June 2026 appear to be backfilled; only weekends from June 2026 onward are used.

## Design rules

- Numbers only from logged data calls, or explicitly labelled **user assumptions** (e.g. maintenance margin rate — not an official Bitget figure).
- Risk level and leverage cap come from published rules, not from the LLM.
- LLM output is scanned for numbers; anything not in the fact table → narrative discarded.
- All data calls are written to an append-only, hash-chained JSONL log (`desk/provenance.py`).
- Sample-size tiers: ≥30 statistics · 10–29 case study · <10 tail reference only.

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

Optional LLM (OpenAI-compatible):

```bash
export LLM_PROVIDER=qwen      # or gemini
export QWEN_API_KEY=...       # or GEMINI_API_KEY=...
```

Refresh the rToken cache (run on a machine that can reach `api.bitget.com`):

```bash
python3 tools/fetch_bitget_cache.py
```

## Tests

```bash
python tests/test_idea_rules.py     # rule parser, offline
python tests/run_weekend_gap.py     # weekend re-anchoring analysis
python tests/run_overnight_gap.py   # 5-year tail reference
python tests/run_card.py            # end-to-end research task
python tests/test_app.py            # Streamlit AppTest (headless)
```

## Limitations

- Weekend sample is small (12 weekends) and covers a single, relatively short period.
- Liquidation line is approximate: user-assumed maintenance margin; no fees, funding, slippage, or collateral drawdown.
- Single-day adverse moves only; multi-day cumulative drawdown is not yet modelled.
- Supported symbols: NVDA, TSLA, AAPL, SPY, QQQ, COIN, MSTR, MSFT, AMZN, GOOGL, META.

## Roadmap

- [ ] Decision ledger: hash-chain each conclusion at the time it is made, score it automatically after the holding period (live only — no backfilled "predictions")
- [ ] Multi-day holding drawdown
- [ ] Collateral linkage (BTC margin + rToken position under the Cross-Asset Unified Account)
- [ ] LLM narrative (Qwen hackathon credits)

## License

MIT — see [LICENSE](LICENSE). Not financial advice; historical data does not guarantee future results.
