"""E-409 环境事件覆盖率审计：哪些真实因果没被记录

E-400 只接了 iterate.py 的回归结果。
但实际上我每天做的事远不止跑回归：
  · 写代码后 node -c 语法检查
  · 跑各种 run_e*.py 实验
  · git commit / tag
  · 文件读写
  · 内核调用（搜到几条记忆、耗时多少）

这些**都是真实因果**，但都没记录。

本审计：
  ① 统计当前环境事件覆盖了哪些操作
  ② 找出高频但未记录的操作
  ③ 判断哪些值得记录（不是越多越好，要有信息量）
"""
import sys
import os
import json
import time
import warnings
import sqlite3
from collections import Counter

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
DB = os.path.join(MEM, "dsh_mem.db")

print("=" * 78)
print("E-409 环境事件覆盖率审计")
print("=" * 78)

con = sqlite3.connect(DB)

# ---------- ① 当前记录了哪些 ----------
print("\n① 当前环境事件")

rows = con.execute(
    "SELECT kind, source, task, at FROM environment_events "
    "WHERE invalid_at IS NULL ORDER BY at").fetchall()

print(f"   共 {len(rows)} 条\n")
by_source = Counter(str(r[1])[:40] for r in rows)
print("   按 source:")
for s, n in by_source.most_common():
    print(f"     {n:>3}  {s}")

by_kind = Counter(r[0] for r in rows)
print(f"\n   按 kind: {dict(by_kind)}")

# ---------- ② 我这几天实际做了哪些操作（从 audit_log 看）----------
print("\n② 实际发生的操作（audit_log）")

audit = con.execute(
    "SELECT kind, COUNT(*) FROM audit_log GROUP BY kind").fetchall()
print("   audit_log 记录的操作类型:")
for k, n in sorted(audit, key=lambda x: -x[1]):
    print(f"     {k:<16} {n:>4}")

# ---------- ③ 从 debug 日志看真实执行 ----------
print("\n③ debug 日志里的真实执行")

LOG = os.path.join(MEM, "plugin-debug.jsonl")
kinds = Counter()
if os.path.exists(LOG):
    with open(LOG, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                e = json.loads(line)
                k = e.get("kind")
                if k:
                    kinds[k] += 1
            except Exception:
                pass

print(f"   debug 事件类型数: {len(kinds)}")
for k, n in kinds.most_common(20):
    print(f"     {k:<30} {n:>5}")

# ---------- ④ 缺口分析 ----------
print("\n④ 缺口分析：哪些真实因果没被记录")

GAPS = [
    ("内核调用", "每次 search/add 都是真实执行，有耗时和结果数",
     "kernel-ready / assemble 有记录，但没进 environment_events"),
    ("实验脚本执行", "run_e*.py 跑成功/失败",
     "只在 iterate.py 回归里记，单跑实验没记"),
    ("git 操作", "commit / tag 成功与否",
     "完全没有记录"),
    ("语法检查", "node -c / ast.parse 通过与否",
     "完全没有记录"),
    ("文件操作", "写文件成功与否",
     "完全没有记录"),
]

for name, desc, gap in GAPS:
    print(f"\n   {name}")
    print(f"     实际: {desc}")
    print(f"     缺口: {gap}")

# ---------- ⑤ 判断哪些值得记录 ----------
print("\n⑤ 哪些值得记录（按信息量判断）")

print("""
   判据：这个事件能否**打脸**某个假设？
         能打脸的才有记录价值。

   ★ 值得记录：
     · 实验脚本执行（成功/失败）→ 能验证"我的改动是否真的有效"
     · 语法检查失败 → 能验证"我以为对但机器说不对"
     · 内核调用耗时异常 → 能验证"性能是否退化"

   ★ 不值得记录：
     · 每次文件写入都得成功（几乎不会失败，零信息量）
     · 每次 git commit 都成功（同上）
     · 每次 search 都返回结果（除非 0 结果，那有意义）

   ★ 所以正确的记录策略是：
     **记异常，不记常态。**
     成功是默认，失败才有信息。
     但也要记"成功"作为分母（否则算不出成功率）。

   ★ 折中：
     · 记录所有执行结果（成功+失败），但**只对失败做详细记录**
     · 成功只记一行（kind + 计数）
     · 这样有分母，又不会淹没在常态里
""")

con.close()

# ---------- 结论 ----------
print("\n" + "=" * 78)
print("结论")
print("=" * 78)

print(f"""
  当前环境事件: {len(rows)} 条
  覆盖的操作: 只有 iterate.py 的回归结果

  最大缺口: **单跑的实验脚本没有记录**
    这些天跑了 run_e382~e408 至少 20 个实验脚本，
    每个都执行了、有结果，但一条环境事件都没落。

  为什么这是缺口：
    环境事件的意义是「哪些结构性决策真的扛住了环境」。
    如果一个实验跑了但没记录，
    那就无法回答"这个结论有多少次真实验证支撑"。
""")

out = {
    "experiment": "E-409",
    "n_events": len(rows),
    "by_kind": dict(by_kind),
    "by_source": dict(by_source),
    "debug_kinds": len(kinds),
    "top_debug": dict(kinds.most_common(10)),
}
p = os.path.join(_HERE, "results", "e409_coverage.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e409_coverage.json")
