"""E-315 诊断：为什么合并后的记忆用自身内容也查不回？

E-314 发现两个问题：
  ① 「如何避免记忆重复」查不到去重相关记忆
  ② [02c11d5c] 用自己合并内容里的句子查询，找不回自己

假设：
  A. 合并后记忆变长、多主题，向量被"平均化"
     → 与任何单一主题都不够像
  B. 合并后没有重新计算向量（用了旧的）
  C. 向量索引没更新

验证：直接测这条记忆的向量与自身各段落的相似度。
"""
import sys
import os
import json
import warnings

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, MEM)

import numpy as np
from store_v2 import MemoryStore
from store_v1 import Embedder

DB = os.path.join(MEM, "dsh_mem.db")
st = MemoryStore(DB)
emb = Embedder.get()

print("=" * 76)
print("E-315 诊断合并后记忆的自检索能力")
print("=" * 76)

# ============ ① 取那条查不回自己的记忆 ============
print("\n① 目标记忆")

target = st.con.execute(
    "SELECT ep_id, raw_text FROM episodes WHERE invalid_at IS NULL "
    "AND ep_id LIKE '02c11d5c%'").fetchone()

if not target:
    print("   找不到该记忆，改用第一条带合并标记的")
    target = st.con.execute(
        "SELECT ep_id, raw_text FROM episodes WHERE invalid_at IS NULL "
        "AND raw_text LIKE '%｜合并:%' LIMIT 1").fetchone()

eid, txt = target
print(f"   [{eid[:8]}] {len(txt)} 字")
print(f"   内容: {txt[:150]}")

# ============ ② 检查它的向量是否最新 ============
print("\n② 向量是否与文本匹配")

st._load_vectors(include_invalid=False)
if eid not in st._vec_ids:
    print("   ❌ 该记忆没有向量！")
    has_vec = False
else:
    idx = st._vec_ids.index(eid)
    cur_vec = st._vec[idx]
    # 重新编码当前文本
    fresh = emb.encode(txt)[0]
    # 对比
    cur_n = cur_vec / (np.linalg.norm(cur_vec) or 1)
    fresh_n = fresh / (np.linalg.norm(fresh) or 1)
    sim = float(cur_n @ fresh_n)
    print(f"   存储向量 vs 重新编码: cos={sim:.4f}")
    if sim > 0.999:
        print("   ✅ 向量是最新的")
    elif sim > 0.95:
        print(f"   🟡 接近但不完全相同（可能差在一次修订）")
    else:
        print(f"   ❌ 向量明显过期（存储的是旧文本的向量）")
    has_vec = True

# ============ ③ 分段测试：各段落能否自检 ============
print("\n③ 分段自检：各部分与整条记忆的相似度")

parts = [p.strip() for p in txt.split("｜合并:") if p.strip()]
print(f"   共 {len(parts)} 段")

st._load_vectors(include_invalid=False)
idx_all = st._vec_ids.index(eid) if eid in st._vec_ids else None

for i, p in enumerate(parts[:6]):
    pv = emb.encode(p)[0]
    pn = pv / (np.linalg.norm(pv) or 1)
    if idx_all is not None:
        vn = st._vec[idx_all] / (np.linalg.norm(st._vec[idx_all]) or 1)
        sim = float(pn @ vn)
    else:
        sim = 0.0
    print(f"   [段{i}] {len(p)}字 cos={sim:.3f}  {p[:55]}")

# ============ ④ 对比：同长度的单主题记忆 ============
print("\n④ 对比：单主题记忆的自检相似度")

single = st.con.execute(
    "SELECT ep_id, raw_text FROM episodes WHERE invalid_at IS NULL "
    "AND raw_text NOT LIKE '%｜合并:%' AND length(raw_text) BETWEEN 100 AND 300 "
    "LIMIT 3").fetchall()

st._load_vectors(include_invalid=False)
for sid, stxt in single:
    if sid not in st._vec_ids:
        continue
    si = st._vec_ids.index(sid)
    sv = emb.encode(stxt)[0]
    svn = sv / (np.linalg.norm(sv) or 1)
    vn = st._vec[si] / (np.linalg.norm(st._vec[si]) or 1)
    print(f"   [{sid[:8]}] {len(stxt)}字 cos={float(svn @ vn):.4f}  "
          f"{stxt[:45]}")

# ============ ⑤ 检索测试：用段落查询能否找回 ============
print("\n⑤ 用各段落查询，看能否找回这条记忆")

for i, p in enumerate(parts[:4]):
    res = st.search(p[:30], top=5)
    rank = None
    for r, (ep, sc, fl) in enumerate(res, 1):
        if ep.ep_id == eid:
            rank = r
            break
    mark = "✅" if rank else "❌"
    print(f"   {mark} [段{i}] 查询 '{p[:25]}' → "
          + (f"排名 {rank}" if rank else "未命中"))

# ============ ⑥ 结论 ============
print("\n" + "=" * 76)
print("结论")
print("=" * 76)

print(f"""
  待查项：
    · 向量是否最新: {'是' if has_vec else '否'}
    · 段落数: {len(parts)}

  可能原因：
    如果向量是最新的，但用自身段落也查不回 →
      说明**多主题记忆的向量被平均化**，与任何单一子主题都不够接近。

  这是合并的固有代价：
    一条记忆包含 N 个主题时，它的向量是 N 个方向的平均，
    与任一方向的相似度都低于单主题记忆。

  处理方向：
    A. 接受（合并后仍能通过其他关键词匹配找到）
    B. 合并时重新编码向量（确保用的是合并后的文本）
    C. 限制合并 —— 只在语义确实相同时合并（提高阈值）
    D. 合并后的记忆额外保留所有段落的向量（复杂）
""")

st.con.close()
