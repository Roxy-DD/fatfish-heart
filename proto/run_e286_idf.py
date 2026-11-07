"""E-286 IDF 是否值得用？需要看它在融合检索里的效果

E-285 结果：
  IDF 加权 @1: 42.9% → 45.7% (+2.8pp)
  IDF 加权 @10: 82.9% → 81.4% (-1.5pp)  ← 退步
  逐用例: 变好 20 / 变差 11

  这不是纯增益，需要更严格验证：
    ① 在融合检索里测（不只向量通道）
    ② 看是否是统计噪声（70 个样本够吗）
    ③ 用 bootstrap 估计置信区间
"""
import sys
import os
import json
import warnings
import math
import random
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
print("E-286 IDF 的严格验证（含置信区间）")
print("=" * 78)

# ============ 准备 ============
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

st._load_vectors(include_invalid=False)
vec = st._vec
ids = st._vec_ids
norms = np.linalg.norm(vec, axis=1)
norms[norms == 0] = 1.0
vec_n = vec / norms[:, None]

# IDF
doc_terms = []
for eid, txt in rows:
    terms = set(w for w, f in pseg.cut(txt) if len(w) >= 2)
    doc_terms.append(terms)
df = Counter()
for terms in doc_terms:
    for t in terms:
        df[t] += 1
N_DOCS = len(doc_terms)


def idf(w):
    return math.log((N_DOCS + 1) / (df.get(w, 0) + 1)) + 1.0


# ============ 两种向量评分 ============
def vec_baseline(query, topk=10):
    qv = emb.encode(query)[0]
    qv = qv / (np.linalg.norm(qv) or 1)
    sims = vec_n @ qv
    idx = np.argsort(-sims)[:topk]
    return [ids[i] for i in idx]


def vec_idf(query, topk=10):
    words = [w for w in query.split() if w]
    if not words:
        return vec_baseline(query, topk)
    acc = None
    for w in words:
        wv = emb.encode(w)[0]
        wv = wv / (np.linalg.norm(wv) or 1)
        wt = idf(w)
        acc = wv * wt if acc is None else acc + wv * wt
    if acc is None:
        return vec_baseline(query, topk)
    acc = acc / (np.linalg.norm(acc) or 1)
    sims = vec_n @ acc
    idx = np.argsort(-sims)[:topk]
    return [ids[i] for i in idx]


# ============ 计算每个用例的 rank ============
def get_ranks(fn):
    out = []
    for c in test_cases:
        got = fn(c["query"], 10)
        out.append(got.index(c["ep_id"]) + 1 if c["ep_id"] in got else None)
    return out


ranks_base = get_ranks(vec_baseline)
ranks_idf = get_ranks(vec_idf)


def recall_at(ranks, k):
    return sum(1 for r in ranks if r is not None and r <= k) / len(ranks)


print("\n" + "=" * 70)
print("① 基础对比")
print("=" * 70)
for k in (1, 3, 5, 10):
    b = recall_at(ranks_base, k)
    i = recall_at(ranks_idf, k)
    print(f"  recall@{k:<2}: 基线 {b:>6.1%}  IDF {i:>6.1%}  "
          f"({(i-b)*100:+.1f}pp)")

# ============ bootstrap 置信区间 ============
print("\n" + "=" * 70)
print("② Bootstrap 置信区间（1000 次重采样，看差异是否显著）")
print("=" * 70)

rng = random.Random(42)
deltas_1 = []
deltas_10 = []

for _ in range(1000):
    idx = [rng.randrange(n) for _ in range(n)]
    rb = [ranks_base[i] for i in idx]
    ri = [ranks_idf[i] for i in idx]
    d1 = recall_at(ri, 1) - recall_at(rb, 1)
    d10 = recall_at(ri, 10) - recall_at(rb, 10)
    deltas_1.append(d1)
    deltas_10.append(d10)

deltas_1.sort()
deltas_10.sort()


def ci(vals, lo=0.025, hi=0.975):
    return vals[int(len(vals) * lo)], vals[int(len(vals) * hi)]


for name, deltas in [("recall@1", deltas_1), ("recall@10", deltas_10)]:
    lo, hi = ci(deltas)
    mean = sum(deltas) / len(deltas)
    sig = "✅ 显著（区间不含 0）" if (lo > 0 or hi < 0) else "❌ 不显著（区间含 0）"
    print(f"\n  {name} 差异: 均值 {mean*100:+.1f}pp")
    print(f"     95% CI: [{lo*100:+.1f}pp, {hi*100:+.1f}pp]")
    print(f"     {sig}")

# ============ 变好/变差的分布 ============
print("\n" + "=" * 70)
print("③ 变好 vs 变差（是否方向一致）")
print("=" * 70)

better = worse = same = 0
for b, i in zip(ranks_base, ranks_idf):
    if b is None and i is not None:
        better += 1
    elif b is not None and i is None:
        worse += 1
    elif b is not None and i is not None:
        if i < b:
            better += 1
        elif i > b:
            worse += 1
        else:
            same += 1
    else:
        same += 1

print(f"  变好 {better} / 变差 {worse} / 不变 {same}")
print(f"  净变化: {better - worse:+d}")

# ============ 结论 ============
print("\n" + "=" * 78)
print("结论")
print("=" * 78)

lo1, hi1 = ci(deltas_1)
lo10, hi10 = ci(deltas_10)

sig_at1 = lo1 > 0
sig_at10_neg = hi10 < 0

print(f"""
  IDF 加权效果：
    recall@1:  {recall_at(ranks_base,1):.1%} → {recall_at(ranks_idf,1):.1%}
               95% CI [{lo1*100:+.1f}, {hi1*100:+.1f}]pp
    recall@10: {recall_at(ranks_base,10):.1%} → {recall_at(ranks_idf,10):.1%}
               95% CI [{lo10*100:+.1f}, {hi10*100:+.1f}]pp
    逐用例净变化: {better - worse:+d}
""")

if sig_at1 and not sig_at10_neg and better > worse:
    verdict = "adopt"
    print("  ✅ @1 显著提升且 @10 未显著退化，可以考虑采用")
elif sig_at1 and sig_at10_neg:
    verdict = "tradeoff"
    print("  🟡 @1 提升但 @10 退化，是权衡而非纯增益")
else:
    verdict = "no_adopt"
    print("  ❌ 差异不显著或方向不一致，不值得增加复杂度")

print(f"""
  样本量说明：{n} 个用例，bootstrap 已考虑样本波动。
  若置信区间跨越 0，说明提升可能是随机波动。
""")

st.con.close()

out = {
    "experiment": "E-286",
    "n_cases": n,
    "baseline": {k: round(recall_at(ranks_base, k), 4) for k in (1, 3, 5, 10)},
    "idf": {k: round(recall_at(ranks_idf, k), 4) for k in (1, 3, 5, 10)},
    "ci_at1": [round(lo1, 4), round(hi1, 4)],
    "ci_at10": [round(lo10, 4), round(hi10, 4)],
    "better": better, "worse": worse,
    "verdict": verdict,
}
p = os.path.join(_HERE, "results", "e286_idf_strict.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e286_idf_strict.json")
