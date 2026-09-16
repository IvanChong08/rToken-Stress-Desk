"""调用日志并发写入测试: 多个进程同时追加, 哈希链必须完整。  python -X utf8 tests/test_calllog_concurrency.py

背景 (2026-09-16): 两个网页实例 + 自动化测试同时写 data/calls_app.jsonl, 第 170 行的 prev 指向第 152 行 -> 链断。
原因是 CallLog 只在内存里记上一条哈希。修复后每次写入都在文件锁内重新读文件最后一行。
"""
import multiprocessing as mp
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from desk.provenance import CallLog, Provenance, verify_log  # noqa: E402

WORKERS, PER_WORKER = 4, 100


def worker(path: str, wid: int):
    log = CallLog(path)   # 每个进程各自一个实例, 模拟多个网页进程
    for i in range(PER_WORKER):
        log.append(Provenance("test", "worker-%d" % wid, {"i": i}, time.time(), 0, True))


if __name__ == "__main__":
    fails = 0
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "calls.jsonl")
        # 先由一个「旧实例」写一条, 之后其他进程写入, 再用这个旧实例继续写 -> 旧代码会在这里断链
        stale = CallLog(path)
        stale.append(Provenance("test", "stale", {}, time.time(), 0, True))
        procs = [mp.Process(target=worker, args=(path, w)) for w in range(WORKERS)]
        t0 = time.time()
        for p in procs:
            p.start()
        for p in procs:
            p.join()
        stale.append(Provenance("test", "stale-again", {}, time.time(), 0, True))
        ok, n = verify_log(path)
        want = WORKERS * PER_WORKER + 2
        print("%d 个进程各写 %d 条 + 旧实例前后各 1 条: %.1f 秒" % (WORKERS, PER_WORKER, time.time() - t0))
        for name, got, exp in [("哈希链完整", ok, True), ("总行数", n, want)]:
            good = got == exp
            fails += not good
            print("%s  %-10s got=%s want=%s" % ("PASS" if good else "FAIL", name, got, exp))
    print("\n%s" % ("全部通过" if not fails else "%d 项失败" % fails))
    sys.exit(1 if fails else 0)
