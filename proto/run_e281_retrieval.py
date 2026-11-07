"""E-281 检索质量：在生产库上量化（核心价值从未测过）

做过很多性能测试（延迟、稳定性），但从未测过：
  **这个系统真的能找回该找的记忆吗？**

历史 benchmark（LoCoMo recall@10 = 0.5802）是离线数据集，
不是真实使用的库。

本实验用生产库的真实记忆构造测试集：
  ① 从 70 条有效记忆里选样本，用"改述"的方式生成查询
  ② 测 recall@1 / recall@5 / recall@10
  ③ 对比三种通道：纯向量、纯FTS5、融合
  ④ 找出失败的案例，看是什么类型的问题
"""
import sys
import os
import json
import random

_HERE = os.path.dirname(os.path.abspath(__file__))
_MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, _MEM)

from store_v2 import MemoryStore

DB = os.path.join(_MEM, "dsh_mem.db")
st = MemoryStore(DB)

print("=" * 78)
print("E-281 生产库检索质量")
print("=" * 78)

# ============ ① 取有效记忆 ============
rows = st.con.execute(
    "SELECT ep_id, raw_text FROM episodes WHERE invalid_at IS NULL "
    "ORDER BY created_at").fetchall()
print(f"\n有效记忆: {len(rows)} 条")

# ============ ② 构造测试集 ============
# 方法：从每条记忆里提取一个"关键概念"，用它做查询
# 这不是改述，而是"关键词查询"，是最容易的基线
# 更难的是语义改述，但需要人工构造

# 自动构造：取记忆里的名词性短语作为查询（用 jieba）
import re
try:
    import jieba.posseg as pseg
    import warnings
    warnings.filterwarnings("ignore")
    HAS_JIEBA = True
except Exception:
    HAS_JIEBA = False

print(f"jieba: {'可用' if HAS_JIEBA else '不可用'}")

test_cases = []
for eid, txt in rows:
    if len(txt) < 15:
        continue
    # 提取最有代表性的 2-3 个名词，拼成查询
    if HAS_JIEBA:
        nouns = []
        for word, flag in pseg.cut(txt[:150]):
            if flag.startswith(("n", "nz", "vn", "eng")) and len(word) >= 2:
                nouns.append(word)
        # 取前 3 个不重复的
        seen = set()
        picked = []
        for w in nouns:
            if w not in seen:
                seen.add(w)
                picked.append(w)
            if len(picked) >= 3:
                break
        if len(picked) >= 2:
            query = " ".join(picked)
            test_cases.append({"ep_id": eid, "query": query,
                               "target": txt[:60]})

print(f"构造测试用例: {len(test_cases)} 个")
print("\n样例：")
for c in test_cases[:5]:
    print(f"  查询: {c['query']}")
    print(f"  目标: {c['target']}...")
    print()

# ============ ③ 测检索 ============
print("=" * 78)
print("检索测试")
print("=" * 78)

hits = {1: 0, 5: 0, 10: 0}
failures = []
t_query = {"ms_total": 0, "n": 0}

for c in test_cases:
    import time
    t0 = time.perf_counter()
    res = st.search(c["query"], top=10)
    ms = (time.perf_counter() - t0) * 1000
    t_query["ms_total"] += ms
    t_query["n"] += 1

    found_at = None
    for rank, (ep, score, flag) in enumerate(res, 1):
        if ep.ep_id == c["ep_id"]:
            found_at = rank
            break

    if found_at:
        for k in hits:
            if found_at <= k:
                hits[k] += 1
    else:
        failures.append({
            "query": c["query"],
            "target_id": c["ep_id"][:8],
            "target": c["target"],
            "got": [(ep.ep_id[:8], round(sc, 3), ep.raw_text[:40])
                    for ep, sc, fl in res[:3]],
        })

n = len(test_cases)
print(f"\n总用例: {n}")
for k in (1, 5, 10):
    ratio = hits[k] / n if n else 0
    print(f"  recall@{k:<2} = {hits[k]:>3}/{n} = {ratio:.1%}")

avg_ms = t_query["ms_total"] / t_query["n"] if t_query["n"] else 0
print(f"\n平均检索耗时: {avg_ms:.0f} ms")

# ============ ④ 失败分析 ============
print("\n" + "=" * 78)
print(f"失败案例分析（{len(failures)} 个）")
print("=" * 78)

for f in failures[:8]:
    print(f"\n  查询: {f['query']}")
    print(f"  目标 [{f['target_id']}]: {f['target']}...")
    print(f"  实际返回:")
    for eid, sc, txt in f["got"]:
        print(f"     [{eid}] cos={sc} {txt}...")

# ============ ⑤ 对比：只用 FTS5 ============
print("\n" + "=" * 78)
print("对比：纯 FTS5 vs 融合检索")
print("=" * 78)

# 纯 FTS5：直接用关键词
fts_hits = {1: 0, 5: 0, 10: 0}
fts_fail = 0

for c in test_cases[:50]:   # 取前 50 个够看趋势
    # 提取第一个词作为 FTS 查询
    kw = c["query"].split()[0]
    try:
        # 直接用 FTS5 表查询（绕过融合）
        q = """
            SELECT e.ep_id FROM ep_fts f
            JOIN episodes e ON e.rowid = f.rowid
            WHERE ep_fts MATCH ? AND e.invalid_at IS NULL
            LIMIT 10
        """
        res = st.con.execute(q, (kw,)).fetchall()
        ids = [r[0] for r in res]
    except Exception:
        ids = []

    if c["ep_id"] in ids:
        rank = ids.index(c["ep_id"]) + 1
        for k in fts_hits:
            if rank <= k:
                fts_hits[k] += 1
    else:
        fts_fail += 1

nf = min(50, len(test_cases))
print(f"\n纯 FTS5（{nf} 个用例）:")
for k in (1, 5, 10):
    ratio = fts_hits[k] / nf if nf else 0
    print(f"  recall@{k:<2} = {fts_hits[k]:>3}/{nf} = {ratio:.1%}")

print(f"\n融合检索（全部 {n} 个）:")
for k in (1, 5, 10):
    ratio = hits[k] / n if n else 0
    print(f"  recall@{k:<2} = {hits[k]:>3}/{n} = {ratio:.1%}")

# ============ 结论 ============
print("\n" + "=" * 78)
print("结论")
print("=" * 78)

r1 = hits[1] / n if n else 0
r5 = hits[5] / n if n else 0
r10 = hits[10] / n if n else 0

print(f"""
  生产库检索质量（{n} 个自动构造的用例）：
    recall@1  = {r1:.1%}
    recall@5  = {r5:.1%}
    recall@10 = {r10:.1%}

  注意：这些用例是「关键词查询」，属于**最容易**的类型。
        真正的语义改述会更难，需要人工构造才能测。

  历史 benchmark（LoCoMo recall@10 = 0.5802）是离线数据集，
  与这里的数字不可直接比较（数据集性质不同）。
""")

st.con.close()

out = {
    "experiment": "E-281",
    "n_cases": n,
    "recall_at_1": round(r1, 4),
    "recall_at_5": round(r5, 4),
    "recall_at_10": round(r10, 4),
    "avg_ms": round(avg_ms, 1),
    "n_failures": len(failures),
    "fts_recall_at_10": round(fts_hits[10] / nf, 4) if nf else 0,
    "failure_samples": failures[:5],
}
p = os.path.join(_HERE, "results", "e281_retrieval_quality.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e281_retrieval_quality.json")
