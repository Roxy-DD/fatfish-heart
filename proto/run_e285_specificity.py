"""E-285 验证：向量评分缺乏特异性加权，实际影响多大？

E-282 发现：scores[eid] = float(sims[i]) * 2.0 —— 纯余弦，无 IDF/特异性加权。
E-282 还发现泛用词向量彼此相似（用户-系统 0.511）。

但"发现一个机制缺失"不等于"它造成实际问题"。
必须先量化：加特异性加权能不能提升 recall？

方案：
  A. 现状（纯余弦）
  B. IDF 加权：罕见词权重高
  C. 特异性惩罚：与太多记忆相似的条目降权
  D. 长度归一化

用生产库 70 个用例实测。
"""
import sys
import os
import json
import warnings
import math
from collections import Counter

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
_MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, _MEM)

import numpy as np
from store_v2 import MemoryStore
from store_v1 import Embedder
import jieba.posseg as pseg

DB = os.path.join(_MEM, "dsh_mem.db")
st = MemoryStore(DB)
emb = Embedder.get()

print("=" * 78)
print("E-285 特异性加权能否提升检索？")
print("=" * 78)

# ============ 构造测试集 ============
rows = st.con.execute(
    "SELECT ep_id, raw_text FROM episodes WHERE invalid_at IS NULL").fetchall()

test_cases = []
for eid, txt in rows:
    if len(txt) < 20:
        continue
    nouns = []
    for word, flag in pseg.cut(txt[:150]):
        if flag.startswith(("n", "nz", "vn", "eng")) and len(word) >= 2:
            nouns.append(word)
    seen = set()
    picked = []
    for w in nouns:
        if w not in seen:
            seen.add(w)
            picked.append(w)
        if len(picked) >= 2:
            break
    if len(picked) >= 2:
        test_cases.append({"ep_id": eid, "query": " ".join(picked),
                           "words": picked})

n = len(test_cases)
print(f"\n测试用例: {n} 个")

# ============ 准备向量矩阵 ============
st._load_vectors(include_invalid=False)
vec = st._vec          # (N, 512)
ids = st._vec_ids      # N 个 ep_id
id2idx = {eid: i for i, eid in enumerate(ids)}
print(f"向量矩阵: {vec.shape}")

norms = np.linalg.norm(vec, axis=1)
norms[norms == 0] = 1.0
vec_n = vec / norms[:, None]

# ============ 计算词频（用于 IDF）============
print("\n统计词频（用于 IDF）…")
doc_terms = []
for eid, txt in rows:
    terms = set()
    for word, flag in pseg.cut(txt):
        if len(word) >= 2:
            terms.add(word)
    doc_terms.append(terms)

df_counter = Counter()
for terms in doc_terms:
    for t in terms:
        df_counter[t] += 1

N_DOCS = len(doc_terms)
def idf(w):
    df = df_counter.get(w, 0)
    return math.log((N_DOCS + 1) / (df + 1)) + 1.0


# ============ 四种评分方案 ============
def scheme_baseline(query, topk=10):
    """A. 纯余弦"""
    qv = emb.encode(query)[0]
    qv = qv / (np.linalg.norm(qv) or 1)
    sims = vec_n @ qv
    idx = np.argsort(-sims)[:topk]
    return [ids[i] for i in idx]


def scheme_idf(query, topk=10):
    """B. IDF 加权：按词的罕见程度加权各词的向量"""
    words = [w for w in query.split() if w]
    if not words:
        return scheme_baseline(query, topk)
    # 各词向量按 IDF 加权求和
    acc = None
    total_w = 0.0
    for w in words:
        wv = emb.encode(w)[0]
        wn = np.linalg.norm(wv) or 1
        wv = wv / wn
        weight = idf(w)
        acc = wv * weight if acc is None else acc + wv * weight
        total_w += weight
    if acc is None:
        return scheme_baseline(query, topk)
    acc = acc / (np.linalg.norm(acc) or 1)
    sims = vec_n @ acc
    idx = np.argsort(-sims)[:topk]
    return [ids[i] for i in idx]


# 预计算"特异性惩罚"：每条记忆与全库的平均相似度
def compute_specificity(vec_n, sample=200):
    """每条记忆与随机样本的平均相似度（越高越"泛"）"""
    n_all = len(vec_n)
    rng = np.random.default_rng(42)
    if n_all <= sample:
        sub = vec_n
    else:
        pick = rng.choice(n_all, sample, replace=False)
        sub = vec_n[pick]
    # 分块算，避免内存爆
    means = np.zeros(n_all)
    CHUNK = 128
    for i in range(0, n_all, CHUNK):
        block = vec_n[i:i + CHUNK]
        sims = block @ sub.T          # (chunk, sample)
        means[i:i + CHUNK] = sims.mean(axis=1)
    return means


print("计算各记忆的特异性（与全库平均相似度）…")
spec = compute_specificity(vec_n)
print(f"  特异性分布: 最小 {spec.min():.3f}, 中位 {np.median(spec):.3f}, "
      f"最大 {spec.max():.3f}")

# 归一化惩罚：越泛（均值高）惩罚越大
spec_min, spec_max = spec.min(), spec.max()
if spec_max > spec_min:
    penalty = 1.0 - (spec - spec_min) / (spec_max - spec_min)   # 1=特有, 0=极泛
else:
    penalty = np.ones_like(spec)
penalty = 0.5 + 0.5 * penalty    # 限制在 [0.5, 1.0]，避免过度惩罚


def scheme_specificity(query, topk=10):
    """C. 特异性惩罚"""
    qv = emb.encode(query)[0]
    qv = qv / (np.linalg.norm(qv) or 1)
    sims = vec_n @ qv
    adjusted = sims * penalty
    idx = np.argsort(-adjusted)[:topk]
    return [ids[i] for i in idx]


def scheme_combined(query, topk=10):
    """D. IDF + 特异性"""
    words = [w for w in query.split() if w]
    if not words:
        return scheme_baseline(query, topk)
    acc = None
    for w in words:
        wv = emb.encode(w)[0]
        wv = wv / (np.linalg.norm(wv) or 1)
        weight = idf(w)
        acc = wv * weight if acc is None else acc + wv * weight
    if acc is None:
        return scheme_baseline(query, topk)
    acc = acc / (np.linalg.norm(acc) or 1)
    sims = vec_n @ acc
    adjusted = sims * penalty
    idx = np.argsort(-adjusted)[:topk]
    return [ids[i] for i in idx]


# ============ 评测 ============
schemes = [
    ("A. 现状（纯余弦）", scheme_baseline),
    ("B. IDF 加权", scheme_idf),
    ("C. 特异性惩罚", scheme_specificity),
    ("D. IDF + 特异性", scheme_combined),
]

print("\n" + "=" * 70)
print("评测结果（只测向量通道，不含 FTS）")
print("=" * 70)
print(f"{'方案':<24} {'recall@1':>10} {'recall@5':>10} {'recall@10':>10}")
print("-" * 70)

results = {}
per_case = {}
for name, fn in schemes:
    hits = {1: 0, 5: 0, 10: 0}
    ranks = []
    for c in test_cases:
        got = fn(c["query"], 10)
        if c["ep_id"] in got:
            r = got.index(c["ep_id"]) + 1
            ranks.append(r)
            for k in hits:
                if r <= k:
                    hits[k] += 1
        else:
            ranks.append(None)
    results[name] = {k: hits[k] / n for k in hits}
    per_case[name] = ranks
    print(f"{name:<24} {hits[1]/n:>9.1%} {hits[5]/n:>9.1%} "
          f"{hits[10]/n:>9.1%}")

# ============ 逐用例对比（vs 基线）============
print("\n" + "=" * 70)
print("逐用例对比（vs 现状）")
print("=" * 70)

base_ranks = per_case["A. 现状（纯余弦）"]
for name, _ in schemes[1:]:
    ranks = per_case[name]
    better = worse = same = 0
    for b, r in zip(base_ranks, ranks):
        if b is None and r is not None:
            better += 1
        elif b is not None and r is None:
            worse += 1
        elif b is not None and r is not None:
            if r < b:
                better += 1
            elif r > b:
                worse += 1
            else:
                same += 1
        else:
            same += 1
    print(f"  {name:<24} 变好 {better:>2} / 变差 {worse:>2} / 不变 {same:>2}")

# ============ 结论 ============
print("\n" + "=" * 78)
print("结论")
print("=" * 78)

best_name = max(results.items(), key=lambda x: x[1][10])[0]
base = results["A. 现状（纯余弦）"]

print(f"""
  最优方案: {best_name}
    recall@1  = {results[best_name][1]:.1%}（现状 {base[1]:.1%}）
    recall@5  = {results[best_name][5]:.1%}（现状 {base[5]:.1%}）
    recall@10 = {results[best_name][10]:.1%}（现状 {base[10]:.1%}）
""")

d1 = results[best_name][1] - base[1]
d10 = results[best_name][10] - base[10]

if d10 > 0.02 and d1 > 0:
    verdict = "improve"
    print(f"  ✅ 显著提升，值得改（@1 {d1*100:+.1f}pp, @10 {d10*100:+.1f}pp）")
elif d1 > 0.02 and d10 >= 0:
    verdict = "improve_top"
    print(f"  🟡 @1 有提升（{d1*100:+.1f}pp）但 @10 不变，可考虑")
else:
    verdict = "no_gain"
    print("  ❌ 没有实质提升，不值得增加复杂度")

st.con.close()

out = {
    "experiment": "E-285",
    "n_cases": n,
    "schemes": {k: {kk: round(vv, 4) for kk, vv in v.items()}
                for k, v in results.items()},
    "best": best_name,
    "delta_1": round(d1, 4),
    "delta_10": round(d10, 4),
    "verdict": verdict,
}
p = os.path.join(_HERE, "results", "e285_specificity.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 results/e285_specificity.json")
