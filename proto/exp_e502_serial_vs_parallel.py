"""E-502 串行 vs 并行：量化时间浪费（FISH-19）

用户批评（2026-11-17）：
  「你一直在等测试结果，而不接着做别的任务，也就是不会做时间和任务规划」
  「测试的活应该安排到后面做，并且是周期性的」
  「你应该把它做到后台，可以做成多后台，前台先做别的」

这个实验不复述批评，而是量化：
  1. 真实的长任务有多长（跑几个典型命令测耗时）
  2. 串行 vs 并行差多少（用实测数据算）
  3. 并行到底能省多少（数字，不是感觉）

不预设结论。如果测出来并行没优势，就照实写。
"""
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

print("=" * 72)
print("E-502 串行 vs 并行：量化时间浪费")
print("=" * 72)

# ---------- 任务清单（真实会做的活）----------
TASKS = [
    ("回归子集A", [sys.executable, "iterate.py", "check", "E-502A"], 1800),
    ("回归子集B", [sys.executable, "iterate.py", "check", "E-502B"], 1800),
    ("doctor自检", [sys.executable, "doctor.py"], 300),
]

env = {**os.environ, "PYTHONIOENCODING": "utf-8",
       "KMP_DUPLICATE_LIB_OK": "TRUE", "FFH_NO_WRITE": "1"}


def run_one(cmd, timeout):
    t0 = time.time()
    try:
        subprocess.run(cmd, cwd=HERE, capture_output=True, timeout=timeout, env=env)
    except Exception:
        pass
    return time.time() - t0


print("\n【1】逐个测量单个任务耗时（串行执行）")
serial_times = []
for name, cmd, to in TASKS:
    t = run_one(cmd, to)
    serial_times.append(t)
    print(f"  {name:<12} {t:6.2f}s")

serial_total = sum(serial_times)
print(f"  串行总计: {serial_total:.2f}s")

# ---------- 并行：同时起 N 个 ----------
print("\n【2】并行执行同样任务")
t0 = time.time()
procs = []
for name, cmd, to in TASKS:
    p = subprocess.Popen(cmd, cwd=HERE, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, env=env)
    procs.append((name, p, to))
for name, p, to in procs:
    try:
        p.wait(timeout=to)
    except Exception:
        p.kill()
parallel_total = time.time() - t0
print(f"  并行总计: {parallel_total:.2f}s")

# ---------- 结论 ----------
speedup = serial_total / parallel_total if parallel_total > 0 else 0
saved = serial_total - parallel_total

print("\n" + "=" * 72)
print("结果")
print("=" * 72)
print(f"  串行: {serial_total:6.2f}s")
print(f"  并行: {parallel_total:6.2f}s")
print(f"  节省: {saved:6.2f}s  ({speedup:.2f}x)")

if speedup > 1.3:
    verdict = f"并行有效，提速 {speedup:.2f}x"
    print(f"\n  ✅ {verdict}")
    print(f"     3 个任务并行比串行省 {saved:.1f}s")
    print(f"     若每天做 10 批这样的任务，节省 {saved*10/60:.1f} 分钟")
else:
    verdict = f"并行收益有限（{speedup:.2f}x），可能是任务本身太短或 IO 争抢"
    print(f"\n  ⚠ {verdict}")

# ---------- 关键约束 ----------
print(f"\n【3】并行不是无脑并行 —— 实测约束")
print(f"  · 写同一个 SQLite 会锁（E-498 F10 已测：并发写需 busy_timeout）")
print(f"  · 回归测试互相干扰（都写 results/*.json）")
print(f"  · Python 进程各占内存（BGE 模型约 400MB/进程）")

out = {
    "experiment": "E-502",
    "serial_total_s": round(serial_total, 2),
    "parallel_total_s": round(parallel_total, 2),
    "speedup": round(speedup, 2),
    "saved_s": round(saved, 2),
    "per_task": [{"name": n, "s": round(t, 2)}
                 for (n, _, _), t in zip(TASKS, serial_times)],
    "verdict": verdict,
}
p = os.path.join(HERE, "results", "e502_serial_vs_parallel.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 {p}")
