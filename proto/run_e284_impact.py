"""E-284 关键验证：改进 FTS 能否提升融合检索？

E-283 结论：
  FTS 单通道：2.9% → 25.7%（trigram 改进显著）
  融合检索：  90.0%

但这是两个独立指标。真正要回答的是：
  **把 trigram 接入融合，recall 会不会提升？**

如果不能提升，说明向量通道已经覆盖，改 FTS 只增加复杂度。
如果能提升，就值得改。

做法：复制生产库到临时库，改 FTS 表结构，跑同批测试对比。
"""
import sys
import os
import json
import shutil
import sqlite3
import warnings

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
_MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, _MEM)

from store_v2 import MemoryStore
import jieba.posseg as pseg

PROD_DB = os.path.join(_MEM, "dsh_mem.db")

print("=" * 78)
print("E-284 改进 FTS 能否提升融合检索？")
print("=" * 78)

# ============ 构造测试集（同 E-281/283）============
st_prod = MemoryStore(PROD_DB)
rows = st_prod.con.execute(
    "SELECT ep_id, raw_text FROM episodes WHERE invalid_at IS NULL").fetchall()
st_prod.con.close()

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
        test_cases.append({"ep_id": eid, "query": " ".join(picked)})

print(f"\n测试用例: {len(test_cases)} 个")

# ============ 基线：现有融合 ============
print("\n① 基线：现有融合检索（unicode61 FTS）")
st_base = MemoryStore(PROD_DB)
base_hits = {1: 0, 5: 0, 10: 0}
for c in test_cases:
    res = st_base.search(c["query"], top=10)
    ids = [ep.ep_id for ep, sc, fl in res]
    if c["ep_id"] in ids:
        rank = ids.index(c["ep_id"]) + 1
        for k in base_hits:
            if rank <= k:
                base_hits[k] += 1
n = len(test_cases)
print(f"   recall@1={base_hits[1]/n:.1%} "
      f"recall@5={base_hits[5]/n:.1%} "
      f"recall@10={base_hits[10]/n:.1%}")
st_base.con.close()

# ============ 实验组：trigram FTS ============
print("\n② 实验组：换成 trigram FTS")

TEST_DB = os.path.join(_HERE, "work", "test_db", "e284.db")
os.makedirs(os.path.dirname(TEST_DB), exist_ok=True)
for suf in ("", "-wal", "-shm"):
    p = TEST_DB + suf
    if os.path.exists(p):
        os.remove(p)

# 复制生产库
src = sqlite3.connect(PROD_DB)
dst = sqlite3.connect(TEST_DB)
with dst:
    src.backup(dst)
dst.close()
src.close()

# 替换 FTS 表为 trigram
con = sqlite3.connect(TEST_DB)
con.executescript("""
    DROP TABLE IF EXISTS ep_fts;
    CREATE VIRTUAL TABLE ep_fts USING fts5(
        raw_text, atmosphere, content='episodes', content_rowid='rowid',
        tokenize='trigram'
    );
    INSERT INTO ep_fts(ep_fts) VALUES('rebuild');
""")
con.commit()
n_fts = con.execute("SELECT COUNT(*) FROM ep_fts").fetchone()[0]
con.close()
print(f"   已重建 trigram FTS，{n_fts} 条")

# 跑测试
st_tri = MemoryStore(TEST_DB)
tri_hits = {1: 0, 5: 0, 10: 0}
better = 0
worse = 0
same = 0
detail = []

for c in test_cases:
    res = st_tri.search(c["query"], top=10)
    ids = [ep.ep_id for ep, sc, fl in res]
    in_tri = c["ep_id"] in ids
    if in_tri:
        rank = ids.index(c["ep_id"]) + 1
        for k in tri_hits:
            if rank <= k:
                tri_hits[k] += 1
        detail.append((c["ep_id"], rank))
    else:
        detail.append((c["ep_id"], None))

print(f"   recall@1={tri_hits[1]/n:.1%} "
      f"recall@5={tri_hits[5]/n:.1%} "
      f"recall@10={tri_hits[10]/n:.1%}")

# ============ 逐用例对比 ============
print("\n③ 逐用例对比")
st_base2 = MemoryStore(PROD_DB)
for c in test_cases:
    r_b = st_base2.search(c["query"], top=10)
    ids_b = [ep.ep_id for ep, sc, fl in r_b]
    rank_b = ids_b.index(c["ep_id"]) + 1 if c["ep_id"] in ids_b else None

    r_t = st_tri.search(c["query"], top=10)
    ids_t = [ep.ep_id for ep, sc, fl in r_t]
    rank_t = ids_t.index(c["ep_id"]) + 1 if c["ep_id"] in ids_t else None

    if rank_b is None and rank_t is not None:
        better += 1
    elif rank_b is not None and rank_t is None:
        worse += 1
    elif rank_b and rank_t and rank_t < rank_b:
        better += 1
    elif rank_b and rank_t and rank_t > rank_b:
        worse += 1
    else:
        same += 1

st_base2.con.close()
st_tri.con.close()

print(f"   变好: {better}")
print(f"   变差: {worse}")
print(f"   不变: {same}")

# ============ 结论 ============
print("\n" + "=" * 78)
print("结论")
print("=" * 78)

delta = tri_hits[10] / n - base_hits[10] / n
print(f"""
  recall@10:  {base_hits[10]/n:.1%} → {tri_hits[10]/n:.1%}  ({delta*100:+.1f}pp)
  recall@1:   {base_hits[1]/n:.1%} → {tri_hits[1]/n:.1%}
  recall@5:   {base_hits[5]/n:.1%} → {tri_hits[5]/n:.1%}

  逐用例: 变好 {better} / 变差 {worse} / 不变 {same}
""")

if delta > 0.02 and worse <= better:
    print("  ✅ 改进有效，值得改")
    verdict = "improve"
elif delta > 0:
    print("  🟡 略有提升但变差的也不少，需权衡")
    verdict = "marginal"
else:
    print("  ❌ 没有提升，不值得改")
    verdict = "no_improve"

# 清理
for suf in ("", "-wal", "-shm"):
    p = TEST_DB + suf
    if os.path.exists(p):
        try:
            os.remove(p)
        except Exception:
            pass

out = {
    "experiment": "E-284",
    "n_cases": n,
    "baseline": {k: round(v / n, 4) for k, v in base_hits.items()},
    "trigram": {k: round(v / n, 4) for k, v in tri_hits.items()},
    "better": better, "worse": worse, "same": same,
    "delta_10": round(delta, 4),
    "verdict": verdict,
}
p = os.path.join(_HERE, "results", "e284_fts_impact.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 results/e284_fts_impact.json")
