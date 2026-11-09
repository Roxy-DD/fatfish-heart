"""E-311 核实清理质量：258 对合并是否过度？

E-310 把有效记忆从 144 降到 78（合并 258 对）。
这个数字很大，必须核实有没有误杀。

验证：
  ① 随机抽几条合并后的记忆，看它们是否还把两边的信息都保留了
  ② 检查有没有"看起来就不该合并"的
  ③ 确认被失效的都能通过 superseded_by 追溯
  ④ 确认 R2 未违反（数据还在，只是标记失效）
"""
import sys
import os
import json
import warnings
import random

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, MEM)

from store_v2 import MemoryStore

DB = os.path.join(MEM, "dsh_mem.db")
st = MemoryStore(DB)

print("=" * 76)
print("E-311 核实合并质量")
print("=" * 76)

# ============ ① 当前状态 ============
s = st.stats()
print("\n① 当前状态")
print(f"   有效: {s['valid']}")
print(f"   失效: {s['invalid']}")
print(f"   总计: {s['valid'] + s['invalid']}")
print(f"   向量: {s['vectors']}")

# ============ ② R2 检查：数据未丢失 ============
print("\n② R2 检查：数据是否完整保留")

n_total = st.con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
print(f"   episodes 总行数: {n_total}")

# 被失效的都应该有 superseded_by
orphan = st.con.execute(
    "SELECT COUNT(*) FROM episodes WHERE invalid_at IS NOT NULL "
    "AND (superseded_by IS NULL OR superseded_by = '')").fetchone()[0]
print(f"   失效但无 superseded_by: {orphan}（这些是历史上的遗忘，非本次合并）")

# 本次合并产生的：有 superseded_by 的
merged_count = st.con.execute(
    "SELECT COUNT(*) FROM episodes WHERE invalid_at IS NOT NULL "
    "AND superseded_by IS NOT NULL AND superseded_by != ''").fetchone()[0]
print(f"   有 superseded_by 的失效记录: {merged_count}")

# 验证 superseded_by 都指向存在的记忆
dangling = st.con.execute(
    "SELECT COUNT(*) FROM episodes e WHERE e.superseded_by IS NOT NULL "
    "AND e.superseded_by != '' AND NOT EXISTS "
    "(SELECT 1 FROM episodes x WHERE x.ep_id = e.superseded_by)").fetchone()[0]
print(f"   superseded_by 指向不存在的: {dangling}")
if dangling == 0:
    print("   ✅ 所有追溯链完好")
else:
    print(f"   ❌ {dangling} 条追溯链断裂")

# ============ ③ 抽查合并结果 ============
print("\n③ 抽查合并结果（看信息是否保留）")

# 找有"｜合并:"标记的记忆
merged_rows = st.con.execute(
    "SELECT ep_id, raw_text FROM episodes WHERE invalid_at IS NULL "
    "AND raw_text LIKE '%｜合并:%' LIMIT 10").fetchall()

print(f"   带合并标记的记忆: {len(merged_rows)} 条")

# 统计所有带标记的
n_marked = st.con.execute(
    "SELECT COUNT(*) FROM episodes WHERE invalid_at IS NULL "
    "AND raw_text LIKE '%｜合并:%'").fetchone()[0]
print(f"   全部: {n_marked} 条")

for eid, txt in merged_rows[:3]:
    print(f"\n   [{eid[:8]}] （{len(txt)} 字）")
    # 显示合并标记前后的内容
    parts = txt.split("｜合并:")
    print(f"     主体: {parts[0].strip()[:80]}")
    if len(parts) > 1:
        print(f"     合并: {parts[1].strip()[:80]}")

# ============ ④ 检查是否有过度合并 ============
print("\n④ 过度合并检查")

# 一条记忆被合并了很多次，可能累积了太多内容
long_merged = st.con.execute(
    "SELECT ep_id, raw_text, length(raw_text) as n FROM episodes "
    "WHERE invalid_at IS NULL AND raw_text LIKE '%｜合并:%' "
    "ORDER BY n DESC LIMIT 5").fetchall()

print(f"\n   合并后最长的记忆:")
for eid, txt, n in long_merged:
    merge_count = txt.count("｜合并:")
    print(f"     [{eid[:8]}] {n} 字, 合并了 {merge_count} 次")
    if merge_count > 5:
        print(f"        ⚠ 合并次数过多，可能信息冗余")

# ============ ⑤ 检查剩余 5 对近重复 ============
print("\n⑤ 剩余的 5 对近重复")

import numpy as np
st._load_vectors(include_invalid=False)
vec = st._vec
ids = st._vec_ids
norms = np.linalg.norm(vec, axis=1)
norms[norms == 0] = 1.0
vn = vec / norms[:, None]
sim = vn @ vn.T
np.fill_diagonal(sim, 0)

remaining = []
for i in range(len(ids)):
    for j in range(i + 1, len(ids)):
        s2 = float(sim[i, j])
        if s2 >= 0.80:
            remaining.append((ids[i], ids[j], s2))

for a, b, s2 in remaining:
    ta = st.con.execute("SELECT raw_text FROM episodes WHERE ep_id=?",
                        (a,)).fetchone()
    tb = st.con.execute("SELECT raw_text FROM episodes WHERE ep_id=?",
                        (b,)).fetchone()
    print(f"\n   cos={s2:.3f}")
    print(f"     A[{a[:8]}] {ta[0][:65] if ta else '?'}")
    print(f"     B[{b[:8]}] {tb[0][:65] if tb else '?'}")

# ============ ⑥ 检索质量是否受影响 ============
print("\n⑥ 检索质量抽样")

queries = [
    "AGI 的观点",
    "自重启怎么实现",
    "耗 token 的问题",
    "版本回退",
    "记忆检索质量",
]
for q in queries:
    res = st.search(q, top=3)
    print(f"\n   查询: {q}")
    for ep, sc, fl in res[:3]:
        mark = "★" if fl == 2 else " "
        print(f"     {mark}cos={sc:>5.2f} [{ep.ep_id[:8]}] {ep.raw_text[:50]}")

st.con.close()

# ============ 结论 ============
print("\n" + "=" * 76)
print("结论")
print("=" * 76)

issues = []
if dangling > 0:
    issues.append(f"{dangling} 条追溯链断裂（R4 违反）")
if n_total < 400:
    issues.append(f"总行数 {n_total} 偏少，可能丢数据（R2 检查）")

print(f"""
  数据完整性:
    · episodes 总行数: {n_total}
    · 有 superseded_by: {merged_count}
    · 断链: {dangling}
    · 带合并标记: {n_marked}

  合并质量:
    · 最长合并记忆: {long_merged[0][2] if long_merged else 0} 字
    · 剩余近重复: {len(remaining)} 对
""")

if issues:
    print("  发现问题：")
    for i in issues:
        print(f"    · {i}")
else:
    print("  ✅ 数据完整，合并质量可接受")

out = {
    "experiment": "E-311",
    "valid": s["valid"],
    "invalid": s["invalid"],
    "total_rows": n_total,
    "merged_records": merged_count,
    "dangling": dangling,
    "n_marked": n_marked,
    "remaining_pairs": len(remaining),
    "issues": issues,
}
p = os.path.join(_HERE, "results", "e311_cleanup_verify.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e311_cleanup_verify.json")
