"""E-283 对比 FTS5 分词方案：unicode61 vs trigram vs 混合

E-282 发现 FTS5 中文完全失效：
  '记忆系统' → 0 条（默认 unicode61 把中文整句当一个 token）

候选方案：
  A. 默认 unicode61（现状）      —— 中文失效
  B. trigram                    —— 3 字起效，2 字查不到
  C. 双表：unicode61 + trigram  —— 覆盖长短查询
  D. 写入时预分词（jieba 切好存另一列）—— 最彻底但要改 schema

用真实库数据实测各方案的 recall。
"""
import sys
import os
import json
import sqlite3
import warnings

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
_MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, _MEM)

from store_v2 import MemoryStore

DB = os.path.join(_MEM, "dsh_mem.db")
st = MemoryStore(DB)

print("=" * 78)
print("E-283 FTS5 分词方案对比")
print("=" * 78)

rows = st.con.execute(
    "SELECT ep_id, raw_text FROM episodes WHERE invalid_at IS NULL").fetchall()
print(f"\n有效记忆: {len(rows)} 条")

# ============ 构造测试集 ============
import jieba.posseg as pseg

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
        if len(picked) >= 3:
            break
    if len(picked) >= 2:
        test_cases.append({"ep_id": eid, "query": " ".join(picked[:2]),
                           "words": picked[:2]})

print(f"测试用例: {len(test_cases)} 个\n")

# ============ 建三张测试表 ============
con = st.con
mem = sqlite3.connect(":memory:")
mem.executescript("""
    CREATE TABLE docs(rowid INTEGER PRIMARY KEY, ep_id TEXT, raw_text TEXT);
    CREATE VIRTUAL TABLE fts_uni USING fts5(raw_text, content='docs', content_rowid='rowid');
    CREATE VIRTUAL TABLE fts_tri USING fts5(raw_text, content='docs', content_rowid='rowid', tokenize='trigram');
""")

for i, (eid, txt) in enumerate(rows, 1):
    mem.execute("INSERT INTO docs VALUES (?,?,?)", (i, eid, txt))
mem.execute("INSERT INTO fts_uni(fts_uni) VALUES('rebuild')")
mem.execute("INSERT INTO fts_tri(fts_tri) VALUES('rebuild')")
mem.commit()
print("测试表已建立（docs / fts_uni / fts_tri）\n")

# ============ 测试各方案 ============
def eval_scheme(name, query_fn, topk=10):
    hits = {1: 0, 5: 0, 10: 0}
    n = 0
    for c in test_cases:
        ids = query_fn(c)
        n += 1
        if c["ep_id"] in ids:
            rank = ids.index(c["ep_id"]) + 1
            for k in hits:
                if rank <= k:
                    hits[k] += 1
    return {k: hits[k] / n if n else 0 for k in hits}, n


# A. unicode61
def q_uni(c):
    try:
        r = mem.execute(
            "SELECT d.ep_id FROM fts_uni f JOIN docs d ON d.rowid=f.rowid "
            "WHERE fts_uni MATCH ? LIMIT 10", (c["query"],)).fetchall()
        return [x[0] for x in r]
    except Exception:
        return []


# B. trigram
def q_tri(c):
    try:
        r = mem.execute(
            "SELECT d.ep_id FROM fts_tri f JOIN docs d ON d.rowid=f.rowid "
            "WHERE fts_tri MATCH ? LIMIT 10", (c["query"],)).fetchall()
        return [x[0] for x in r]
    except Exception:
        return []


# C. 混合：trigram OR unicode
def q_hybrid(c):
    ids = q_tri(c)
    if len(ids) < 10:
        for x in q_uni(c):
            if x not in ids:
                ids.append(x)
    return ids[:10]


# D. trigram + 单词回退（2字词用前缀匹配）
def q_tri_fallback(c):
    ids = q_tri(c)
    if len(ids) < 5:
        # 用每个词单独查（trigram 支持 3+ 字符，2 字会失败）
        for w in c["words"]:
            if len(w) >= 3:
                try:
                    r = mem.execute(
                        "SELECT d.ep_id FROM fts_tri f JOIN docs d ON d.rowid=f.rowid "
                        "WHERE fts_tri MATCH ? LIMIT 5", (w,)).fetchall()
                    for x in r:
                        if x[0] not in ids:
                            ids.append(x[0])
                except Exception:
                    pass
    return ids[:10]


print("=" * 66)
print("方案对比（纯 FTS，不含向量）")
print("=" * 66)
print(f"{'方案':<28} {'recall@1':>10} {'recall@5':>10} {'recall@10':>10}")
print("-" * 66)

schemes = [
    ("A. unicode61（现状）", q_uni),
    ("B. trigram", q_tri),
    ("C. trigram + unicode 混合", q_hybrid),
    ("D. trigram + 单词回退", q_tri_fallback),
]

results = {}
for name, fn in schemes:
    r, n = eval_scheme(name, fn)
    results[name] = r
    print(f"{name:<28} {r[1]:>9.1%} {r[5]:>9.1%} {r[10]:>9.1%}")

# ============ 融合检索基线 ============
print("\n" + "=" * 66)
print("对比：现有融合检索（向量+FTS+其他）")
print("=" * 66)
fused = {1: 0, 5: 0, 10: 0}
for c in test_cases:
    res = st.search(" ".join(c["words"]), top=10)
    ids = [ep.ep_id for ep, sc, fl in res]
    if c["ep_id"] in ids:
        rank = ids.index(c["ep_id"]) + 1
        for k in fused:
            if rank <= k:
                fused[k] += 1
n = len(test_cases)
print(f"{'融合检索':<28} {fused[1]/n:>9.1%} {fused[5]/n:>9.1%} {fused[10]/n:>9.1%}")

# ============ 结论 ============
print("\n" + "=" * 78)
print("结论")
print("=" * 78)

best = max(results.items(), key=lambda x: x[1][10])
print(f"""
  最优纯 FTS 方案: {best[0]}
    recall@10 = {best[1][10]:.1%}

  现状（unicode61）: {results['A. unicode61（现状）'][10]:.1%}
  → 提升空间: {(best[1][10] - results['A. unicode61（现状）'][10]) * 100:.1f} 个百分点

  融合检索: {fused[10]/n:.1%}
""")

if best[1][10] > results["A. unicode61（现状）"][10] + 0.1:
    print("  → FTS 通道确实有显著改进空间，值得改")
else:
    print("  → FTS 改进空间有限，向量通道已覆盖大部分")

mem.close()
st.con.close()

out = {
    "experiment": "E-283",
    "n_cases": n,
    "schemes": {k: {kk: round(vv, 4) for kk, vv in v.items()}
                for k, v in results.items()},
    "fused": {k: round(v / n, 4) for k, v in fused.items()},
    "best": best[0],
}
p = os.path.join(_HERE, "results", "e283_tokenizer.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e283_tokenizer.json")
