"""
大模型用量闸门: 公开部署后陌生人乱点也不会把黑客松额度一次烧光。

为什么可以这样做: 本工具的数字全部由确定性代码算出, 大模型只负责
「听懂复杂句子」和「把结论写成人话」, 两者都有规则 / 模板兜底。
额度用完 -> 功能降级, 不是不能用。

计数写在 data/llm_usage.json (按 UTC 日期), 用与调用日志相同的文件锁, 多进程安全。
上限用环境变量 LLM_DAILY_CAP 覆盖 (默认 120 次/天)。
"""
from __future__ import annotations

import datetime as dt
import json
import os

from .provenance import _file_lock

DEFAULT_CAP = 120


def _path() -> str:
    return os.environ.get("LLM_USAGE_PATH") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "llm_usage.json")


def cap() -> int:
    try:
        return max(0, int(os.environ.get("LLM_DAILY_CAP", DEFAULT_CAP)))
    except ValueError:
        return DEFAULT_CAP


def _read(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def used_today() -> int:
    d = _read(_path())
    return int(d.get("count", 0)) if d.get("date") == str(dt.date.today()) else 0


def remaining() -> int:
    return max(0, cap() - used_today())


def take(n: int = 1) -> bool:
    """占用 n 次额度; 不够就返回 False (调用方应退回规则 / 模板)。"""
    path = _path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    today = str(dt.date.today())
    with _file_lock(path):
        d = _read(path)
        count = int(d.get("count", 0)) if d.get("date") == today else 0
        if count + n > cap():
            return False
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"date": today, "count": count + n}, f)
    return True


def status_text() -> str:
    return "大模型今日额度 %d / %d 次" % (used_today(), cap())
