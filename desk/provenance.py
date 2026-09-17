"""
每个数字都带来源: 哪个数据源 / 哪个接口或工具 / 什么参数 / 什么时候取的 / 耗时多少 / 成功与否。

为什么: 实测 LLM 会自信地写错数字 (周报里「XRP 年初至今 +400%」实际 -41.4%),
所以本项目的规则是 —— 结论里出现的每个数字, 都必须能追溯到一次具体的数据调用。
调用记录写入只可追加的 JSONL, 每行带上一行的哈希, 事后改动任何一行都会断链。
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass
from typing import Any

GENESIS = "0" * 64


@dataclass
class Provenance:
    source: str                 # 'yahoo' | 'bitget' | 'mcp'
    endpoint: str               # URL, 或 MCP 的 '<tool>.<action>'
    params: dict[str, Any]
    fetched_at: float           # unix 秒
    latency_ms: int
    ok: bool
    error: str | None = None
    n_records: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    def label(self) -> str:
        """给界面「核实抽屉」用的一行说明。"""
        t = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(self.fetched_at))
        status = "ok" if self.ok else "FAILED: %s" % (self.error or "")
        return "%s | %s | %d ms | %s | %s" % (self.source, self.endpoint, self.latency_ms, t, status)


@contextlib.contextmanager
def _file_lock(path: str):
    """跨进程互斥锁 (Windows: msvcrt; 其他: fcntl)。锁文件是 <日志>.lock。"""
    f = open(path + ".lock", "a+")
    try:
        if os.name == "nt":
            import msvcrt
            while True:
                try:
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.02)
        else:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX)
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(f, fcntl.LOCK_UN)
        except OSError:
            pass
        f.close()


def _last_line(path: str) -> str | None:
    """从文件末尾往回读, 取最后一个非空行 (日志变大后也不用整个读一遍)。"""
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        pos, buf = f.tell(), b""
        while pos > 0:
            step = min(4096, pos)
            pos -= step
            f.seek(pos)
            buf = f.read(step) + buf
            lines = [ln for ln in buf.split(b"\n") if ln.strip()]
            if len(lines) >= 2 or (pos == 0 and lines):
                return lines[-1].decode("utf-8")
    return None


class CallLog:
    """
    只可追加、带哈希链的调用日志。

    2026-09-16 修正: 原先只在内存里记住上一条的哈希; 两个进程 (例如两个网页实例 + 测试) 同时写同一个文件时,
    彼此看不到对方写入的行, 链条就断了。现在每次写入都在文件锁内重新读取文件最后一行的哈希。
    """

    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

    def _last_hash(self) -> str:
        """
        最后一条的哈希。文件被写坏 (进程写到一半被杀 / 少字段) 时返回 GENESIS 开新段,
        **不抛异常** —— 以前这里一炸, append 和 verify_log 全炸, 而 verify_log 在页面顶层被调用,
        结果是整个应用白屏且无法自愈 (2026-09-18 对抗性审查实测)。
        开新段不会掩盖损坏: verify_log 走到那一行会发现 prev 对不上, 照样报「链损坏」。
        """
        if not os.path.exists(self.path):
            return GENESIS
        try:
            last = _last_line(self.path)
            return json.loads(last)["hash"] if last else GENESIS
        except (ValueError, KeyError, OSError):
            return GENESIS

    def append(self, prov: Provenance) -> str:
        with _file_lock(self.path):
            rec = {"prev": self._last_hash(), **prov.to_dict()}
            rec["hash"] = _digest(rec)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
        return rec["hash"]


def _digest(rec: dict) -> str:
    return hashlib.sha256(json.dumps(rec, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def verify_log(path: str) -> tuple[bool, int]:
    """
    重算整条链, 返回 (是否完整, 已验证的行数)。
    解析不了的行 = 链损坏, 返回 (False, n) —— 不能抛异常: 调用方在页面顶层, 一抛就白屏。
    ⚠️ 这个函数能发现的是「中间某一行被改过」。它**发现不了**:
       ① 尾部整段被删 (前面的链仍然自洽) ② 有人用 _digest 把下游哈希全部重算
    所以它防的是误改和意外损坏, 不是防篡改 —— 文案不要写成 append-only 不可篡改。
    """
    prev, n = GENESIS, 0
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                rec = json.loads(line)
                h = rec.pop("hash")
                if rec["prev"] != prev or _digest(rec) != h:
                    return False, n
                prev, n = h, n + 1
    except (ValueError, KeyError, OSError):
        return False, n
    return True, n
