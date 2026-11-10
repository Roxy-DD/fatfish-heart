"""E-404 确认那 64 条「含测试字样」是不是污染

E-403 发现 audit_log 里有 64 条 msg 含"测试"字样。
但要区分：
  A. 我的实验残留（该清理）
  B. 回归测试脚本自己写入的审计记录（正常，不该清）

不能看到"测试"就删——那会破坏审计完整性。
"""
import sys
import os
import json
import warnings
import sqlite3
from collections import Counter

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
DB = os.path.join(MEM, "dsh_mem.db")

print("=" * 78)
print("E-404 确认 audit_log 里 64 条测试字样的性质")
print("=" * 78)

con = sqlite3.connect(DB)

rows = con.execute(
    "SELECT seq, at, kind, sid, msg FROM audit_log "
    "WHERE msg LIKE '%测试%' OR msg LIKE '%__e%' "
    "ORDER BY seq").fetchall()

print(f"\n① 共 {len(rows)} 条\n")

# 按 kind 分布
kinds = Counter(r[2] for r in rows)
print("   按 kind:")
for k, n in kinds.most_common():
    print(f"     {k}: {n}")

# 按 sid 分布
print("\n   按 sid:")
sids = Counter(str(r[3] or "(空)")[:28] for r in rows)
for s, n in sids.most_common():
    print(f"     {s}: {n}")

# 抽样看内容
print("\n② 抽样 20 条看内容性质")
for seq, at, kind, sid, msg in rows[:20]:
    print(f"   seq={seq:<5} [{kind}] {str(msg)[:60]}")

# ---------- 判定逻辑 ----------
print("\n③ 判定")

# 关键：这些 msg 是不是回归测试写入的？
# 回归测试用临时 DB（work/test_db/），不该写生产库
# 但如果测试脚本没隔离，就会写进来

# 看它们的 created_at 时间是否集中在某次实验
import time
times = [r[1] for r in rows if r[1]]
if times:
    t0 = min(times)
    t1 = max(times)
    print(f"   时间跨度: "
          f"{time.strftime('%m-%d %H:%M', time.localtime(t0))} ~ "
          f"{time.strftime('%m-%d %H:%M', time.localtime(t1))}")
    span_min = (t1 - t0) / 60
    print(f"   ({span_min:.0f} 分钟)")

# 分类：真正的实验残留 vs 正常审计
# 特征：正常审计的 msg 是"写入 xxx"、"检索 xxx"，
#       实验残留会有 __e、e399_test 这类标记

MARKER_ONLY = ["__e", "e399_test", "e391_test", "test_db"]
real_pollution = [r for r in rows
                  if any(m in str(r[4]) for m in MARKER_ONLY)]

print(f"\n   带实验标记（__e / e399_test 等）: {len(real_pollution)} 条")
for seq, at, kind, sid, msg in real_pollution:
    print(f"     seq={seq} [{kind}] {str(msg)[:60]}")

normal_test = [r for r in rows if r not in real_pollution]
print(f"\n   其余（可能正常，只是内容含'测试'）: {len(normal_test)} 条")
# 抽样
for seq, at, kind, sid, msg in normal_test[:10]:
    print(f"     seq={seq} [{kind}] {str(msg)[:55]}")

con.close()

print("\n" + "=" * 78)
print("结论")
print("=" * 78)

print(f"""
  带实验标记的真实污染: {len(real_pollution)} 条
  内容含"测试"但可能是正常审计: {len(normal_test)} 条

  {"✅ 只需清理前面那批" if real_pollution else "✅ 没有真污染"}
""")

out = {
    "experiment": "E-404",
    "n_total": len(rows),
    "n_marked": len(real_pollution),
    "n_normal": len(normal_test),
    "kinds": dict(kinds),
    "seqs_to_clean": [r[0] for r in real_pollution],
}
p = os.path.join(_HERE, "results", "e404_audit_nature.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e404_audit_nature.json")
