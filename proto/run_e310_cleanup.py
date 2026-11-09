"""E-310 清理生产库里已有的近义重复（用新阈值）

E-307 发现 12 组近义重复。现在阈值已标定（0.80），
可以安全地找出并处理它们。

策略：
  · 用新阈值找近重复对
  · 合并：保留信息更完整的那条（更长的），标记短的为被取代
  · 不物理删除（R2）
  · 先备份
"""
import sys
import os
import json
import warnings
import itertools

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, MEM)

import numpy as np
from store_v2 import MemoryStore
import backup as bk
import cli_mcp

DB = os.path.join(MEM, "dsh_mem.db")

print("=" * 76)
print("E-310 清理生产库近义重复")
print("=" * 76)

# ---------- 备份 ----------
print("\n① 备份")
b = bk.backup_db(DB)
print(f"   ok={b.get('ok')} {os.path.basename(b.get('path') or '')}")
if not b.get("ok"):
    print("   备份失败，中止")
    raise SystemExit(1)

st = MemoryStore(DB)

# ---------- 找近重复 ----------
print("\n② 用阈值 0.80 找近重复对")

st._load_vectors(include_invalid=False)
vec = st._vec
ids = st._vec_ids
norms = np.linalg.norm(vec, axis=1)
norms[norms == 0] = 1.0
vn = vec / norms[:, None]
sim = vn @ vn.T
np.fill_diagonal(sim, 0)

THRESH = 0.80
pairs = []
for i in range(len(ids)):
    for j in range(i + 1, len(ids)):
        s = float(sim[i, j])
        if s >= THRESH:
            pairs.append((ids[i], ids[j], s))

pairs.sort(key=lambda x: -x[2])
print(f"   找到 {len(pairs)} 对（cos >= {THRESH}）")

# 取文本
def get_text(eid):
    row = st.con.execute(
        "SELECT raw_text FROM episodes WHERE ep_id=?", (eid,)).fetchone()
    return row[0] if row else ""

# 过滤掉 L0/L1/L2（不可合并）
import hook as _hook
protected = set()
for p in _hook.protected_of(st):
    if p["level"] in ("L0", "L1", "L2"):
        protected.add(p["ep_id"])

safe_pairs = [(a, b, s) for a, b, s in pairs
              if a not in protected and b not in protected]
print(f"   排除受保护后: {len(safe_pairs)} 对")

print("\n   详情：")
for a, b, s in safe_pairs[:15]:
    ta, tb = get_text(a), get_text(b)
    print(f"\n   cos={s:.3f}")
    print(f"     A[{a[:8]}] ({len(ta)}字) {ta[:70]}")
    print(f"     B[{b[:8]}] ({len(tb)}字) {tb[:70]}")

# ---------- 执行合并 ----------
print("\n③ 执行合并（保留更长的，失效更短的）")

actions = []
for a, b, s in safe_pairs:
    ta, tb = get_text(a), get_text(b)
    if not ta or not tb:
        continue
    # 保留更长的（信息更完整）
    if len(ta) >= len(tb):
        keep, drop = a, b
    else:
        keep, drop = b, a
    actions.append({"op": "merge", "keep_id": keep, "drop_id": drop})

print(f"   计划合并 {len(actions)} 对")

if actions:
    r = cli_mcp.cmd_sleep_apply(st, {
        "actions": actions,
        "advance_cursor": False,
    })
    print(f"   ok={r.get('ok')}")
    res = r.get("result", {})
    print(f"   merged: {len(res.get('merged', []))}")
    print(f"   invalidated: {len(res.get('invalidated', []))}")
    print(f"   skipped_hooks: {res.get('skipped_hooks', [])}")
else:
    print("   无需合并")

# ---------- 结果 ----------
print("\n④ 结果")
after = st.stats()
print(f"   有效: {after['valid']}（原 {len(ids)}）")
print(f"   失效: {after['invalid']}")

# 验证：再次扫描还有多少近重复
st._load_vectors(include_invalid=False)
vec2 = st._vec
norms2 = np.linalg.norm(vec2, axis=1)
norms2[norms2 == 0] = 1.0
vn2 = vec2 / norms2[:, None]
sim2 = vn2 @ vn2.T
np.fill_diagonal(sim2, 0)

remaining = 0
for i in range(len(st._vec_ids)):
    for j in range(i + 1, len(st._vec_ids)):
        if float(sim2[i, j]) >= THRESH:
            remaining += 1

print(f"\n   剩余近重复对: {remaining}（原 {len(pairs)}）")

st.con.close()

# ---------- 结论 ----------
print("\n" + "=" * 76)
print("结论")
print("=" * 76)

print(f"""
  清理前: {len(ids)} 条有效，{len(pairs)} 对近重复
  清理后: {after['valid']} 条有效，{remaining} 对近重复
  合并:   {len(actions)} 对

  说明：
    · 合并保留信息更完整的那条（更长的）
    · 短的标记为失效 + superseded_by（R2 不物理删除）
    · 受保护记忆（L0/L1/L2）未参与合并
""")

out = {
    "experiment": "E-310",
    "before_valid": len(ids),
    "before_pairs": len(pairs),
    "after_valid": after["valid"],
    "after_pairs": remaining,
    "merged": len(actions),
}
p = os.path.join(_HERE, "results", "e310_cleanup.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e310_cleanup.json")
