"""E-271 查清 21 次慢检索的原因

E-270 结论：
  冷启动 7189ms（模型加载，一次性）
  热启动 10ms
  但 152 次 assemble 里有 21 次 > 1000ms（14%）

这 14% 不能全归因于冷启动（会话只有几次重启）。
必须查清：慢的时候到底是什么情况。
"""
import sys
import os
import json
import io

_HERE = os.path.dirname(os.path.abspath(__file__))
_MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, _MEM)

debug_log = os.path.join(_MEM, "plugin-debug.jsonl")

print("=" * 76)
print("E-271 慢检索归因")
print("=" * 76)

records = []
with io.open(debug_log, "r", encoding="utf-8", errors="replace") as f:
    for line in f:
        try:
            e = json.loads(line)
            if e.get("kind") == "assemble":
                records.append(e)
        except Exception:
            continue

# 找慢的
slow = [r for r in records
        if isinstance(r.get("msSearch"), (int, float)) and r["msSearch"] > 1000]

print(f"\n总 assemble: {len(records)}")
print(f"慢检索(>1000ms): {len(slow)}")

print("\n所有慢检索的详情：")
print(f"{'时间':<24} {'msSearch':>10} {'mode':<10} {'query':<30}")
for r in slow:
    t = r.get("t", "")[:19]
    ms = r.get("msSearch")
    mode = r.get("mode", "?")
    q = str(r.get("query", ""))[:28]
    print(f"{t:<24} {ms:>10} {mode:<10} {q:<30}")

# 按 mode 分类
from collections import Counter
modes = Counter(r.get("mode", "?") for r in slow)
print(f"\n慢检索按 mode: {dict(modes)}")

# 检查是否集中在某些时间段（例如进程刚启动）
print("\n时间分布（慢 vs 总数）：")
by_hour = {}
for r in records:
    t = r.get("t", "")[:13]
    by_hour.setdefault(t, {"total": 0, "slow": 0})
    by_hour[t]["total"] += 1
    ms = r.get("msSearch")
    if isinstance(ms, (int, float)) and ms > 1000:
        by_hour[t]["slow"] += 1

for h in sorted(by_hour)[-12:]:
    v = by_hour[h]
    pct = v["slow"] / v["total"] * 100 if v["total"] else 0
    flag = " ⚠" if pct > 30 else ""
    print(f"  {h}  {v['slow']:>2}/{v['total']:>2}  ({pct:>5.1f}%){flag}")

# ============ 关键分析 ============
print("\n" + "=" * 76)
print("归因分析")
print("=" * 76)

# 判断：慢的是否都发生在会话第一次
print("""
  关键问题：慢检索是否集中在"会话首次"？

  如果是 → 内核预热没生效，或预热与真实检索条件不同
  如果否   → 有其他原因（数据量、GPU 争用、锁等待）
""")

# 看每个小时第一条是否慢
print("每个时段的第一条记录：")
seen_hours = set()
for r in records:
    h = r.get("t", "")[:13]
    if h in seen_hours:
        continue
    seen_hours.add(h)
    ms = r.get("msSearch")
    flag = "很慢" if isinstance(ms, (int, float)) and ms > 1000 else "正常"
    print(f"  {h}  第一条 = {ms}ms  ({flag})")

# GPU 争用检查：慢的是不是成簇出现
print("\n慢检索是否成簇（连续出现）：")
prev_slow = False
clusters = 0
for r in records:
    ms = r.get("msSearch")
    is_slow = isinstance(ms, (int, float)) and ms > 1000
    if is_slow and not prev_slow:
        clusters += 1
    prev_slow = is_slow
print(f"  慢检索簇数: {clusters}（{len(slow)} 次慢 / {clusters} 簇）")
if clusters > 0:
    avg_per_cluster = len(slow) / clusters
    print(f"  平均每簇 {avg_per_cluster:.1f} 次")
    if avg_per_cluster > 2:
        print("  → 成簇出现，可能是资源争用（GPU/CPU）或进程重启后的连续冷启动")
    else:
        print("  → 分散出现，可能是零星的资源争用")

out = {
    "experiment": "E-271",
    "total": len(records),
    "slow_count": len(slow),
    "slow_pct": round(len(slow) / len(records) * 100, 1) if records else 0,
    "slow_by_mode": dict(modes),
    "clusters": clusters,
    "slow_details": [{"t": r.get("t"), "ms": r.get("msSearch"),
                      "mode": r.get("mode")} for r in slow],
}
p = os.path.join(_HERE, "results", "e271_slow_attrib.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 results/e271_slow_attrib.json")
