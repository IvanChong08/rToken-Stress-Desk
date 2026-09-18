"""
相似情景检索的敏感性实验: 换一套主观参数, Top-5 还是不是同一批日子?

    python -X utf8 research/analog_sensitivity.py

为什么要跑这个: 组权重 (30/20/25/25) 和「排除最近 10 个交易日」都是**产品设定**, 不是估出来的。
如果换一套合理的设定就得到完全不同的答案, 那这个排名不该被当成结论 —— 这种时候光加一句
免责声明没有用, 应该改设计。所以先跑, 再决定怎么说。

判据 (Top-5 重合数): 3-5 稳定 / 2 中度敏感 / 0-1 高度敏感。
结果写进 research/results_analog_sensitivity.txt (含运行时间 / commit / dirty 标记 /
**输入摘要与来源日期** —— 注意是摘要不是快照: 没有冻结完整特征矩阵, 所以能重新运行、
不能逐位复现)。README 里引用的数字来自那份文件。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from desk import analog as an  # noqa: E402
from desk import portfolio as pf  # noqa: E402

# README 里的示例组合, 保持一致才能对得上
ACCOUNT = pf.Account([pf.Position("NVDA", "long", 15000), pf.Position("MSTR", "long", 8000),
                      pf.Position("COIN", "long", 5000), pf.Position("SPY", "short", 5000)],
                     usdt=5000, btc=0.1, maint_margin=0.01, btc_haircut=0.95)

WEIGHT_SETS = {
    "当前设定 30/20/25/25": {"vol": 0.30, "mkt": 0.20, "coupling": 0.25, "pos": 0.25},
    "四组等权 25/25/25/25": {"vol": 0.25, "mkt": 0.25, "coupling": 0.25, "pos": 0.25},
    "波动偏高 45/15/20/20": {"vol": 0.45, "mkt": 0.15, "coupling": 0.20, "pos": 0.20},
}
EXCLUDE_SET = (5, 10, 20)


def verdict(n: int) -> str:
    return "稳定" if n >= 3 else ("中度敏感" if n == 2 else "高度敏感")


def main() -> int:
    out = []

    def say(line=""):
        print(line)
        out.append(line)

    import subprocess
    try:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True,
                                text=True, cwd=root).stdout.strip() or "(不在 git 仓库里)"
        # 只记 commit 证明不了「运行时的工作区和那个 commit 一致」—— 有未提交改动就必须标出来
        dirty = bool(subprocess.run(["git", "status", "--porcelain"], capture_output=True,
                                    text=True, cwd=root).stdout.strip())
    except Exception:
        commit, dirty = "(取不到)", None

    panel = pf.fetch_panel(sorted({p.symbol for p in ACCOUNT.positions}))
    vix, spy, dvol = an.fetch_context()[:3]

    base = an.find_analogs(ACCOUNT, panel, vix, spy, dvol)
    say("# 这份结果「可以重新运行」, 但**不能精确复现**:")
    say("# 行情数据会被上游修订, 查询日也随运行时点变化。要精确复现必须连同输入快照一起冻结。")
    say("运行时间 (UTC)   %s" % an.now_utc().strftime("%Y-%m-%d %H:%M:%S"))
    say("代码 commit      %s%s" % (commit, "  ⚠️ 工作区有未提交改动 (dirty)" if dirty else
                               ("  (工作区干净)" if dirty is False else "")))
    say("组合             %s" % ", ".join("%s %s %g" % (p.symbol, p.side, p.notional)
                                          for p in ACCOUNT.positions))
    say("保证金           USDT %g | BTC %g | 维持保证金率 %g | 折算率 %g"
        % (ACCOUNT.usdt, ACCOUNT.btc, ACCOUNT.maint_margin, ACCOUNT.btc_haircut))
    say("数据源最后一天   VIX %s | SPY %s | DVOL %s"
        % (max(vix.index) if vix is not None else "—", max(spy.index) if spy is not None else "—",
           max(dvol.index) if dvol is not None else "—"))
    say("日历边界         %s (美股 %s / 加密 %s)"
        % (base["as_of"]["calendar_cutoff"], base["as_of"]["us_calendar_limit"],
           base["as_of"]["crypto_calendar_limit"]))
    say("最后完整交易日   %s, 丢掉 %d 根未走完的日线"
        % (base["as_of"].get("us_last_complete_bar") or "—",
           base["as_of"].get("dropped_incomplete", 0)))
    say()
    say("查询日 %s | 核心版候选 %d 天 | 覆盖 %s"
        % (base["query_date"], base["n_candidates"], base["date_range"]))
    say("特征 %d 项: %s" % (len(base["features_used"]), ", ".join(base["features_used"])))
    say()

    say("=" * 74)
    say("一 · 组权重")
    say("=" * 74)
    tops = {}
    for name, w in WEIGHT_SETS.items():
        r = an.find_analogs(ACCOUNT, panel, vix, spy, dvol, weights=w)
        tops[name] = [x["date"] for x in r["analogs"]]
        say("%-22s %s" % (name, ", ".join(tops[name])))
    say()
    names = list(WEIGHT_SETS)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            k = len(set(tops[names[i]]) & set(tops[names[j]]))
            say("Top-5 重合 %-22s vs %-22s = %d -> %s" % (names[i], names[j], k, verdict(k)))
    common_w = sorted(set.intersection(*(set(v) for v in tops.values())))
    say("三套权重都进 Top-5: %s" % (", ".join(common_w) or "无"))
    say()

    say("=" * 74)
    say("二 · 排除最近 N 个交易日 (EXCLUDE_RECENT)")
    say("=" * 74)
    tope = {}
    for n in EXCLUDE_SET:
        r = an.find_analogs(ACCOUNT, panel, vix, spy, dvol, exclude_recent=n)
        tope[n] = [x["date"] for x in r["analogs"]]
        say("排除最近 %-2d 天   %s" % (n, ", ".join(tope[n])))
    say()
    ks = list(EXCLUDE_SET)
    for i in range(len(ks)):
        for j in range(i + 1, len(ks)):
            k = len(set(tope[ks[i]]) & set(tope[ks[j]]))
            say("Top-5 重合 排除%-2d vs 排除%-2d = %d -> %s" % (ks[i], ks[j], k, verdict(k)))
    common_e = sorted(set.intersection(*(set(v) for v in tope.values())))
    say("三个设定都进 Top-5: %s" % (", ".join(common_e) or "无"))
    say()

    say("=" * 74)
    say("结论")
    say("=" * 74)
    worst_w = min(len(set(tops[names[i]]) & set(tops[names[j]]))
                  for i in range(len(names)) for j in range(i + 1, len(names)))
    worst_e = min(len(set(tope[ks[i]]) & set(tope[ks[j]]))
                  for i in range(len(ks)) for j in range(i + 1, len(ks)))
    say("权重两两最小重合 %d -> %s" % (worst_w, verdict(worst_w)))
    say("排除窗口两两最小重合 %d -> %s" % (worst_e, verdict(worst_e)))
    if min(worst_w, worst_e) <= 1:
        say("!! 高度敏感: 单一排名不够可靠, 应改用特征组等权做默认, 并展示多套设定下都稳定的 robust cases。")
    else:
        say("三套设定下 Top-5 至少有 %d/5 重合, 核心案例相对稳定 —— 但仍有 %d/5 会随权重改变,"
            % (worst_w, 5 - worst_w))
        say("所以不能说「排名不依赖主观设定」, 只能说边缘名次对权重敏感、核心案例不敏感。")

    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results_analog_sensitivity.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    print("\n结果已写入 %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
