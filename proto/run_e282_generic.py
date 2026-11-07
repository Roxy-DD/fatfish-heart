"""E-282 诊断：为什么泛用词查询找不回特定目标？

E-281 发现：
  recall@10 = 92.9%，但失败的 5 个用例有共同模式：
    查询含"用户/系统/记忆"这类泛用词
    → 返回的全是泛泛的同类记忆
    → 特定目标被淹没

假设：向量相似度被泛用词主导，缺少"特异性"惩罚。
"""
import sys
import os
import json

_HERE = os.path.dirname(os.path.abspath(__file__))
_MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, _MEM)

from store_v2 import MemoryStore
from store_v1 import Embedder

DB = os.path.join(_MEM, "dsh_mem.db")
st = MemoryStore(DB)
emb = Embedder.get()

print("=" * 76)
print("E-282 诊断泛用词问题")
print("=" * 76)

# ============ ① 复现：泛用词查询 ============
print("\n① 复现失败案例")

cases = [
    ("用户 记忆系统 定位", "用户对记忆系统的定位"),
    ("系统 基准 记忆", "系统健康基准"),
    ("用户 笔记 记忆", "用户对笔记与记忆体系的设计理念"),
]

for query, target_hint in cases:
    res = st.search(query, top=5)
    print(f"\n  查询: '{query}'")
    print(f"  目标含: '{target_hint}'")
    for rank, (ep, sc, fl) in enumerate(res, 1):
        is_target = target_hint in ep.raw_text
        mark = "★" if is_target else " "
        print(f"    {mark}{rank}. cos={sc:>6} {ep.raw_text[:45]}")

# ============ ② 分析：泛用词的向量特征 ============
print("\n② 泛用词的向量特征")

import numpy as np

words = ["用户", "系统", "记忆", "基准", "定位", "笔记"]
vecs = {}
for w in words:
    v = emb.encode(w)[0]
    vecs[w] = v / (np.linalg.norm(v) or 1)

print(f"\n  {'词':<8} {'与其他词的平均余弦'}")
for w in words:
    sims = [float(vecs[w] @ vecs[o]) for o in words if o != w]
    avg = sum(sims) / len(sims)
    print(f"  {w:<8} {avg:.3f}")

# 泛用词是否彼此相似？
generic = ["用户", "系统", "记忆"]
print(f"\n  泛用词之间: ", end="")
pairs = []
for i in range(len(generic)):
    for j in range(i + 1, len(generic)):
        s = float(vecs[generic[i]] @ vecs[generic[j]])
        pairs.append(f"{generic[i]}-{generic[j]}={s:.3f}")
print(", ".join(pairs))

# ============ ③ 检查：是否有 IDF 加权 ============
print("\n③ 当前检索是否有 IDF/特异性加权")

# 检查 store_v1.search 的评分逻辑
import inspect
src = inspect.getsource(st.search)
# 找评分相关
import re
score_lines = [ln for ln in src.split("\n")
               if any(k in ln for k in ["score", "sim", "cos", "idf", "freq", "weight"])]
print(f"   search 里的评分相关代码 {len(score_lines)} 行")
for ln in score_lines[:15]:
    print(f"     {ln.strip()[:75]}")

# ============ ④ 检查：FTS5 为什么只有 8% ============
print("\n④ FTS5 为什么只有 8% recall@10")

# 测一个具体查询
test_q = "记忆系统"
q = """
    SELECT e.ep_id, e.raw_text FROM ep_fts f
    JOIN episodes e ON e.rowid = f.rowid
    WHERE ep_fts MATCH ? AND e.invalid_at IS NULL
    LIMIT 5
"""
try:
    res = st.con.execute(q, (test_q,)).fetchall()
    print(f"\n  FTS5 查 '{test_q}': {len(res)} 条")
    for eid, txt in res[:3]:
        print(f"     {txt[:50]}")
except Exception as e:
    print(f"  FTS5 查询失败: {e}")

# 中文 FTS5 是否有分词问题？
print("\n  检查 FTS5 分词器配置:")
row = st.con.execute(
    "SELECT sql FROM sqlite_master WHERE name='ep_fts'").fetchone()
if row:
    print(f"     {row[0][:200]}")

# 测单字查询
for kw in ["记", "忆", "记忆", "系统"]:
    try:
        r = st.con.execute(
            "SELECT COUNT(*) FROM ep_fts WHERE ep_fts MATCH ?", (kw,)).fetchone()
        print(f"     '{kw}' → {r[0]} 条命中")
    except Exception as e:
        print(f"     '{kw}' → 错误: {str(e)[:40]}")

st.con.close()

print("\n" + "=" * 76)
print("分析结论")
print("=" * 76)
print("""
  如果泛用词的向量彼此高度相似（> 0.5），
  且检索没有 IDF 加权，那么：
    → "用户 记忆系统 定位" 这类查询的向量
      会偏向"记忆系统"这个高频概念的中心
    → 无法区分"记忆系统的定位" vs "记忆系统的能力"

  修复方向：
    a) 加 IDF 加权（罕见词权重高）
    b) 加特异性惩罚（与过多记忆相似的条目降权）
    c) 用 FTS5 做精确词匹配补充
""")

out = {"experiment": "E-282", "cases": len(cases)}
p = os.path.join(_HERE, "results", "e282_generic_words.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e282_generic_words.json")
