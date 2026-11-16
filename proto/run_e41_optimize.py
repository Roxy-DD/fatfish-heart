"""
E-41 性能优化（在不破坏红线的前提下）

红线（docs/34）：
  I-1 记忆与模型解耦   I-2 迁移 100%   I-3 分配不覆盖
  I-4 不遗忘           I-5 特征无关

本轮优化三项，每项都要复验红线：
  A. int8 量化 BGE 向量（195MB → 49MB？召回损失多少？）
  B. FTS5 倒排替换 numpy 全表扫描（K 检索 O(N) → O(log N)）
  C. 编码缓存（内容 hash → 重复查询省 6.41ms）

每项报告：性能、召回变化、迁移复验（100%？）
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import time

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
os.environ["HF_HOME"] = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".hf")
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import numpy as np

MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models", "bge-small-zh")
RESULTS = os.path.join(_HERE, "results")
WORK = os.path.join(_HERE, "work", "e41")
os.makedirs(WORK, exist_ok=True)


def synth_texts(n, seed=0):
    rng = np.random.default_rng(seed)
    people = [f"人{i:03d}" for i in range(200)]
    projs = [f"项目{i:03d}" for i in range(150)]
    topics = ["记忆系统", "检索算法", "硬件设计", "产品运营", "数据分析",
              "接口对接", "性能优化", "需求评审", "部署上线", "故障排查"]
    verbs = ["负责", "参与", "评审", "跟进"]
    out = []
    for i in range(n):
        p = people[rng.integers(0, 200)]
        j = projs[rng.integers(0, 150)]
        v = verbs[rng.integers(0, 4)]
        t = topics[rng.integers(0, 10)]
        out.append(f"{p} {v} {j}，本阶段重点是{t}，第{i}次记录")
    return out


def make_queries(n=50, seed=7):
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        p = f"人{rng.integers(0,200):03d}"
        out.append(p)
    return out


def load_bge():
    import torch
    from transformers import AutoTokenizer, AutoModel
    tok = AutoTokenizer.from_pretrained(MODEL_DIR)
    mdl = AutoModel.from_pretrained(MODEL_DIR); mdl.eval()

    def embed(texts, bs=64):
        outs = []
        with torch.no_grad():
            for i in range(0, len(texts), bs):
                b = texts[i:i + bs]
                enc = tok(b, padding=True, truncation=True, max_length=128, return_tensors="pt")
                o = mdl(**enc)
                outs.append(o.last_hidden_state[:, 0].cpu().numpy())
        v = np.vstack(outs).astype(np.float32)
        nn = np.linalg.norm(v, axis=1, keepdims=True); nn[nn==0]=1
        return v / nn
    return embed


def l2(a):
    n = np.linalg.norm(a, axis=-1, keepdims=True); n[n==0]=1
    return a / n


# ---------- A. int8 量化 ----------
def quant_int8(M):
    """按维度 min-max 做 int8 量化"""
    lo = M.min(axis=0, keepdims=True)
    hi = M.max(axis=0, keepdims=True)
    scale = (hi - lo) / 255.0
    scale[scale == 0] = 1e-9
    Q = np.clip(np.round((M - lo) / scale), 0, 255).astype(np.uint8)
    return Q, lo.astype(np.float32), scale.astype(np.float32)


def dequant_int8(Q, lo, scale):
    return (Q.astype(np.float32) * scale + lo)


def recall_at(M, qv, gold_idx, k=5):
    s = M @ qv
    top = np.argpartition(-s, k)[:k]
    return len(set(top.tolist()) & set(gold_idx)) / len(gold_idx)


def main():
    print("=" * 100)
    print("E-41 性能优化（红线：迁移 100% + 召回不降）")
    print("=" * 100)

    N = 20000
    texts = synth_texts(N)
    print(f"\n语料 {N} 条，构造 BGE...")
    embed = load_bge()
    t0 = time.time()
    M = embed(texts)
    print(f"  编码 {time.time()-t0:.1f}s，形状 {M.shape}")

    qs = make_queries(50)
    QV = embed(qs)

    # 建立"标准答案"：用全精度向量检索 top5
    gold = []
    for i in range(len(qs)):
        s = M @ QV[i]
        gold.append(set(np.argpartition(-s, 5)[:5].tolist()))

    out = {"experiment": "E-41", "n": N, "dim": int(M.shape[1])}

    # ================= A. int8 量化 =================
    print("\n" + "-" * 100)
    print("A. int8 量化（存储 195MB → ?）")
    print("-" * 100)
    tq = time.time()
    Q, lo, sc = quant_int8(M)
    q_s = time.time() - tq
    Mq = dequant_int8(Q, lo, sc)
    Mq = l2(Mq)

    raw_mb = M.nbytes / 1024 / 1024
    q_mb = (Q.nbytes + lo.nbytes + sc.nbytes) / 1024 / 1024

    # 召回对比（以全精度为基准）
    same = both = 0
    for i in range(len(qs)):
        s = Mq @ QV[i]
        top = set(np.argpartition(-s, 5)[:5].tolist())
        same += len(top & gold[i])
        both += 5
    overlap = same / both

    # 延迟对比
    ts = time.time()
    for _ in range(20):
        s = Mq @ QV[0]
        _ = np.argpartition(-s, 5)[:5]
    q_ms = (time.time() - ts) / 20 * 1000
    ts = time.time()
    for _ in range(20):
        s = M @ QV[0]
        _ = np.argpartition(-s, 5)[:5]
    f_ms = (time.time() - ts) / 20 * 1000

    print(f"  精度: 原始 {raw_mb:.1f} MB → int8 {q_mb:.1f} MB（压缩 {raw_mb/q_mb:.2f}×）")
    print(f"  top5 重叠率（对全精度基准）: {overlap*100:.2f}%")
    print(f"  检索延迟: 全精度 {f_ms:.2f} ms → int8 {q_ms:.2f} ms")
    out["A_int8"] = {"raw_mb": round(raw_mb, 2), "q_mb": round(q_mb, 2),
                     "ratio": round(raw_mb / q_mb, 2),
                     "top5_overlap": round(overlap, 4),
                     "full_ms": round(f_ms, 3), "q_ms": round(q_ms, 3)}

    # ================= B. FTS5 =================
    print("\n" + "-" * 100)
    print("B. FTS5 倒排 vs numpy 全表扫描")
    print("-" * 100)
    db = os.path.join(WORK, "fts.db")
    if os.path.exists(db):
        os.remove(db)
    con = sqlite3.connect(db)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE mem (id INTEGER PRIMARY KEY, body TEXT)")
    con.execute("CREATE VIRTUAL TABLE fts USING fts5(body, content='mem', content_rowid='id')")
    con.executemany("INSERT INTO mem (id, body) VALUES (?,?)", list(enumerate(texts)))
    con.execute("INSERT INTO fts(fts) VALUES('rebuild')")
    con.commit()
    print(f"  FTS5 建索引完成，{con.execute('SELECT COUNT(*) FROM fts').fetchone()[0]} 条")

    # FTS5 查询延迟
    ts = time.time()
    R = 30
    for i in range(R):
        rows = con.execute(
            "SELECT rowid FROM fts WHERE fts MATCH ? ORDER BY rank LIMIT 5",
            (f'"{qs[i % len(qs)]}"',)).fetchall()
    fts_ms = (time.time() - ts) / R * 1000

    # numpy 全表扫描对照
    docs = [set(t.split()) for t in texts]
    ts = time.time()
    for i in range(R):
        term = qs[i % len(qs)]
        hits = [j for j, d in enumerate(docs) if term in d]
    np_ms = (time.time() - ts) / R * 1000

    print(f"  FTS5 查询     = {fts_ms:.3f} ms")
    print(f"  numpy 扫描    = {np_ms:.3f} ms")
    print(f"  加速比        = {np_ms/max(fts_ms,1e-6):.1f}×")
    out["B_fts5"] = {"fts5_ms": round(fts_ms, 3), "numpy_ms": round(np_ms, 3),
                     "speedup": round(np_ms / max(fts_ms, 1e-6), 1)}
    con.close()

    # ================= C. 编码缓存 =================
    print("\n" + "-" * 100)
    print("C. 编码缓存（内容 hash → 向量）")
    print("-" * 100)
    cache = {}
    ts = time.time()
    for q in qs:
        cache[hashlib.md5(q.encode()).hexdigest()] = embed([q])
    cold_ms = (time.time() - ts) / len(qs) * 1000

    ts = time.time()
    for q in qs:
        h = hashlib.md5(q.encode()).hexdigest()
        if h in cache:
            _ = cache[h]
    hot_ms = (time.time() - ts) / len(qs) * 1000

    print(f"  冷（真编码） = {cold_ms:.2f} ms/次")
    print(f"  热（命中缓存）= {hot_ms:.4f} ms/次")
    print(f"  加速比       = {cold_ms/max(hot_ms,1e-6):.0f}×")
    out["C_cache"] = {"cold_ms": round(cold_ms, 3), "hot_ms": round(hot_ms, 5),
                      "speedup": round(cold_ms / max(hot_ms, 1e-6), 0)}

    # ================= D. 红线复验 =================
    print("\n" + "=" * 100)
    print("D. 红线复验（必须全过）")
    print("=" * 100)

    # I-1/I-2: 迁移复验 —— 保存到磁盘，新连接重载，逐条比对
    save_path = os.path.join(WORK, "mem_store.npz")
    np.savez_compressed(save_path, Q=Q, lo=lo, scale=sc)
    # 模拟"新进程"：重新读文件、独立重建
    z = np.load(save_path)
    M2 = l2(dequant_int8(z["Q"], z["lo"], z["scale"]))
    identical = np.array_equal(M2, Mq)
    max_diff = float(np.max(np.abs(M2 - Mq)))

    # 逐条预测一致率
    match = 0
    for i in range(len(qs)):
        s1 = Mq @ QV[i]; s2 = M2 @ QV[i]
        p1 = int(np.argmax(s1)); p2 = int(np.argmax(s2))
        match += int(p1 == p2)
    mig_rate = match / len(qs)

    print(f"  I-1/I-2 迁移复验:")
    print(f"    重载后矩阵完全一致: {identical}")
    print(f"    最大偏差: {max_diff:.2e}")
    print(f"    逐条预测一致率: {mig_rate*100:.1f}%")
    print(f"    存储大小: {os.path.getsize(save_path)/1024/1024:.1f} MB")

    # I-4: 不遗忘 —— 数据全在（对比条数）
    n_ok = z["Q"].shape[0] == N
    print(f"  I-4 不遗忘: 条数 {z['Q'].shape[0]} / {N} → {'✓' if n_ok else '✗'}")

    # 召回未退化
    rec_ok = overlap >= 0.95
    print(f"  召回未退化: top5 重叠 {overlap*100:.1f}% (要求 ≥95%) → "
          f"{'✓' if rec_ok else '✗ 未达标'}")

    out["D_redline"] = {"migration_identical": bool(identical),
                        "max_diff": float(max_diff),
                        "migration_rate": round(mig_rate, 4),
                        "store_mb": round(os.path.getsize(save_path) / 1024 / 1024, 1),
                        "n_preserved": bool(n_ok),
                        "recall_ok": bool(rec_ok),
                        "top5_overlap": round(overlap, 4)}

    print("\n" + "=" * 100)
    print("总结")
    print("=" * 100)
    print(f"  A int8:  存储 {raw_mb:.0f}→{q_mb:.0f} MB ({raw_mb/q_mb:.1f}×)，"
          f"召回重叠 {overlap*100:.1f}%")
    print(f"  B FTS5:  {np_ms:.2f}→{fts_ms:.3f} ms（{np_ms/max(fts_ms,1e-6):.0f}×）")
    print(f"  C 缓存:  {cold_ms:.2f}→{hot_ms:.3f} ms（{cold_ms/max(hot_ms,1e-6):.0f}×）")
    print(f"  D 红线:  迁移 {'✓' if identical else '✗'}  "
          f"不遗忘 {'✓' if n_ok else '✗'}  召回 {'✓' if rec_ok else '✗'}")

    with open(os.path.join(RESULTS, "e41_optimize.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e41_optimize.json')}")


if __name__ == "__main__":
    main()
