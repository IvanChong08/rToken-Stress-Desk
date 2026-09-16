"""大模型用量闸门的离线测试。  python -X utf8 tests/test_budget.py

为什么要有闸门: 公开部署后陌生人乱点会把黑客松的 Qwen 额度烧光。
额度用完时工具必须还能用 (数字本来就不靠大模型), 只是退回规则解析 + 模板结论。
"""
import datetime as dt
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

fails = 0


def check(name, got, want):
    global fails
    ok = got == want
    fails += not ok
    print("%s  %-40s got=%s%s" % ("PASS" if ok else "FAIL", name, got, "" if ok else "  want=%s" % (want,)))


with tempfile.TemporaryDirectory() as d:
    path = os.path.join(d, "usage.json")
    os.environ["LLM_USAGE_PATH"] = path
    os.environ["LLM_DAILY_CAP"] = "3"
    from desk import budget  # noqa: E402
    from desk.portfolio import Account, Position  # noqa: E402
    from desk import portfolio_idea as pi  # noqa: E402

    check("上限来自环境变量", budget.cap(), 3)
    check("一开始没用过", (budget.used_today(), budget.remaining()), (0, 3))
    check("前 3 次放行", [budget.take() for _ in range(3)], [True, True, True])
    check("第 4 次拒绝", budget.take(), False)
    check("用完后剩 0", (budget.used_today(), budget.remaining()), (3, 0))
    check("状态文字", budget.status_text(), "大模型今日额度 3 / 3 次")

    # 换一天要重置
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"date": str(dt.date.today() - dt.timedelta(days=1)), "count": 3}, f)
    check("跨天重置", (budget.used_today(), budget.take()), (0, True))

    # 额度用完时: 规则能解析的照常可用, 解析不了的给明确说明 (不调用大模型)
    os.environ["LLM_DAILY_CAP"] = "0"
    base = Account([Position("NVDA", "long", 15000)], usdt=5000, btc=0.1)
    a, provs, how = pi.understand("5000U 做保证金, 做多 NVDA 1万", base, 80000, budget_check=budget.take)
    # 这句规则本来就能解析, 连额度检查都用不到 (provs 为空 = 没调用大模型)
    check("额度为 0: 规则仍能解析新组合", (a is not None, provs, a.usdt), (True, [], 5000.0))
    a, provs, how = pi.understand("帮我随便搞一个稳一点的组合", base, 80000, budget_check=budget.take)
    check("额度为 0: 看不懂时说明原因", (a, "额度已用完" in how), (None, True))

os.environ.pop("LLM_USAGE_PATH", None)
os.environ.pop("LLM_DAILY_CAP", None)
print("\n%s" % ("全部通过" if not fails else "%d 项失败" % fails))
sys.exit(1 if fails else 0)
