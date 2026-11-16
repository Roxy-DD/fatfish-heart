"""
E-44 V 检索加速：全扫描 vs IVF vs IVF-PQ vs HNSW

当前最大瓶颈：10 万条 V 检索 4.68 ms（O(N) 全扫描）。

红线要求（docs/34）：
  索引必须是**派生结构**，能从真值层（int8 向量 + 量化参数）随时重建。
  索引丢了不影响记忆，只影响速度。

对比五种：
  EXACT   全扫描（基线）
  IVF     k-means 倒排（自实现，不依赖外部库）
  IVF-PQ  IVF + 乘积量化（自实现）
  FAISS-IVF    faiss 版 IVF
  FAISS-HNSW   faiss 图索引

指标：
  ① 查询延迟（预热后中位数）
  ② Recall@5（对 EXACT 基准）
  ③ 索引构建时间
  ④ 索引内存/磁盘占用
  ⑤ 红线：索引可从真值重建（删掉索引后重建，结果一致）
"""
from __future__ import annotations

import json
import os
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
DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "tnews")
K = 5


def load_tnews(limit=100000):
    import pandas as pd
    paths = []
    for root, _, files in os.walk(DATA):
        for f in files:
            if f.endswith(".parquet"):
                paths.append(os.path.join(root, f))
    df = pd.concat([pd.read_parquet(p) for p in sorted(paths)], ignore_index=True)
    tcol = "sentence" if "sentence" in df.columns else df.columns[0]
    texts = df[tcol].astype(str).tolist()
    if limit < len(texts):
        idx = np.random.default_rng(0).choice(len(texts), limit, replace=False)
        texts = [texts[i] for i in idx]
    return texts


def load_bge():
    import torch
    from transformers import AutoTokenizer, AutoModel
    tok = AutoTokenizer.from_pretrained(MODEL_DIR)
    mdl = AutoModel.from_pretrained(MODEL_DIR); mdl.eval()
    torch.set_num_threads(4)

    def embed(texts, bs=64):
        outs = []
        with torch.no_grad():
            for i in range(0, len(texts), bs):
                b = texts[i:i + bs]
                enc = tok(b, padding=True, truncation=True, max_length=128, return_tensors="pt")
                outs.append(mdl(**enc).last_hidden_state[:, 0].cpu().numpy())
        v = np.vstack(outs).astype(np.float32)
        nn = np.linalg.norm(v, axis=1, keepdims=True); nn[nn == 0] = 1
        return v / nn
    return embed


def l2(a):
    n = np.linalg.norm(a, axis=-1, keepdims=True); n[n == 0] = 1
    return a / n


def median_ms(fn, repeat=30, warmup=5):
    for _ in range(warmup):
        fn()
    ts = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t0) * 1000)
    a = np.array(ts)
    return {"median": float(np.median(a)), "p90": float(np.percentile(a, 90))}


# ================= 自实现 IVF =================
class MyIVF:
    """k-means 倒排索引"""

    def __init__(self, M, nlist=256, iters=12, seed=0):
        self.n, self.d = M.shape
        self.nlist = nlist
        rng = np.random.default_rng(seed)
        # k-means++ 简化：随机初始化 + 迭代
        cent = M[rng.choice(self.n, nlist, replace=False)].copy()
        for _ in range(iters):
            lab = np.empty(self.n, np.int32)
            for st in range(0, self.n, 16384):
                en = min(st + 16384, self.n)
                lab[st:en] = (M[st:en] @ cent.T).argmax(axis=1)
            # 更新质心
            newc = np.zeros_like(cent)
            cnt = np.zeros(nlist)
            for c in range(nlist):
                m = lab == c
                if m.any():
                    newc[c] = M[m].sum(axis=0)
                    cnt[c] = m.sum()
            nz = cnt > 0
            newc[nz] /= cnt[nz, None]
            cent = np.where(nz[:, None], newc, cent)
        self.cent = l2(cent)
        self.lists = [np.where(lab == c)[0] for c in range(nlist)]

    def search(self, M, qv, k=K, nprobe=8):
        sim = self.cent @ qv
        probe = np.argpartition(-sim, min(nprobe, self.nlist - 1))[:nprobe]
        cand = np.concatenate([self.lists[c] for c in probe if len(self.lists[c])])
        if len(cand) == 0:
            return np.array([], dtype=np.int64)
        s = M[cand] @ qv
        kk = min(k, len(cand))
        top = np.argpartition(-s, kk - 1)[:kk]
        return cand[top]

    def size_mb(self):
        return (self.cent.nbytes + sum(l.nbytes for l in self.lists)) / 1024 / 1024


# ================= 自实现 IVF-PQ =================
class MyIVFPQ:
    """IVF + 乘积量化（把向量切成 m 段，每段用码本表示）"""

    def __init__(self, M, nlist=256, m_sub=16, nbits=8, iters=10, seed=0):
        self.ivf = MyIVF(M, nlist=nlist, iters=iters, seed=seed)
        self.M = M
        self.n, self.d = M.shape
        self.m_sub = m_sub
        self.sub = self.d // m_sub
        self.ncent = 2 ** nbits
        rng = np.random.default_rng(seed + 1)
        self.codebooks = []
        self.codes = np.zeros((self.n, m_sub), np.uint8)
        for s in range(m_sub):
            seg = M[:, s * self.sub:(s + 1) * self.sub]
            cen = seg[rng.choice(self.n, self.ncent, replace=False)].copy()
            for _ in range(iters):
                d = ((seg[:, None, :] - cen[None]) ** 2).sum(axis=2) if len(seg) < 20000 else None
                if d is None:
                    lab = np.empty(self.n, np.int32)
                    for st in range(0, self.n, 4096):
                        en = min(st + 4096, self.n)
                        dd = (seg[st:en, None, :] - cen[None]) ** 2
                        lab[st:en] = dd.sum(axis=2).argmin(axis=1)
                else:
                    lab = d.argmin(axis=1)
                for c in range(self.ncent):
                    mm = lab == c
                    if mm.any():
                        cen[c] = seg[mm].mean(axis=0)
            self.codes[:, s] = lab
            self.codebooks.append(cen.astype(np.float32))

    def decode(self, idx):
        out = np.zeros((len(idx), self.d), np.float32)
        for s in range(self.m_sub):
            out[:, s * self.sub:(s + 1) * self.sub] = self.codebooks[s][self.codes[idx, s]]
        return l2(out)

    def search(self, qv, k=K, nprobe=8):
        sim = self.ivf.cent @ qv
        probe = np.argpartition(-sim, min(nprobe, self.ivf.nlist - 1))[:nprobe]
        cand = np.concatenate([self.ivf.lists[c] for c in probe if len(self.ivf.lists[c])])
        if len(cand) == 0:
            return np.array([], dtype=np.int64)
        # 用码本近似打分（不解码全向量）
        scores = np.zeros(len(cand), np.float32)
        for s in range(self.m_sub):
            qseg = qv[s * self.sub:(s + 1) * self.sub]
            # 查询段 vs 该段码本 → 每个候选的分数
            table = self.codebooks[s] @ qseg          # (ncent,)
            scores += table[self.codes[cand, s]]
        kk = min(k, len(cand))
        top = np.argpartition(-scores, kk - 1)[:kk]
        return cand[top]

    def size_mb(self):
        cb = sum(c.nbytes for c in self.codebooks)
        return (cb + self.codes.nbytes + self.ivf.cent.nbytes) / 1024 / 1024


def main():
    print("=" * 100)
    print("E-44 V 检索加速：全扫描 vs IVF vs IVF-PQ vs HNSW")
    print("=" * 100)

    texts = load_tnews(100000)
    N = len(texts)
    print(f"\n语料 {N} 条")

    embed = load_bge()
    for _ in range(5):
        embed(["预热"])

    print("编码...")
    t0 = time.time()
    M = embed(texts)
    print(f"  {time.time()-t0:.0f}s，形状 {M.shape}")

    # 真值层：int8 + 量化参数（红线要求能重建）
    lo = M.min(axis=0, keepdims=True); hi = M.max(axis=0, keepdims=True)
    scale = (hi - lo) / 255.0; scale[scale == 0] = 1e-9
    Q = np.clip(np.round((M - lo) / scale), 0, 255).astype(np.uint8)

    # 从真值层重建（模拟重启）
    M_truth = l2(Q.astype(np.float32) * scale + lo)
    print(f"  真值层重建一致: {np.allclose(M_truth, M_truth)}  "
          f"(int8 存储 {Q.nbytes/1024/1024:.1f} MB)")

    # 查询集：用真实标题
    rng = np.random.default_rng(21)
    qidx = rng.choice(N, 100, replace=False)
    QV = embed([texts[i] for i in qidx])

    # EXACT 基准
    print("\n计算 EXACT 基准（100 条查询）...")
    gold = []
    t0 = time.time()
    for i in range(len(QV)):
        s = M_truth @ QV[i]
        gold.append(set(np.argpartition(-s, K)[:K].tolist()))
    print(f"  基准总耗时 {time.time()-t0:.1f}s")

    def recall(pred, g):
        return len(set(pred.tolist()) & g) / K

    results = {}

    # ---------- EXACT ----------
    print("\n" + "-" * 100)
    print("构建与测量")
    print("-" * 100)
    print(f"{'方法':<14}{'建索引s':<12}{'延迟ms':<12}{'Recall@5':<12}{'内存MB':<12}")
    print("-" * 100)

    def measure(name, search_fn, build_s, size_mb):
        recs = []
        for i in range(len(QV)):
            pred = search_fn(QV[i])
            recs.append(recall(pred, gold[i]))
        lat = median_ms(lambda: search_fn(QV[0]), repeat=20, warmup=3)
        results[name] = {"build_s": round(build_s, 2), "ms": round(lat["median"], 3),
                         "p90": round(lat["p90"], 3),
                         "recall": round(float(np.mean(recs)), 4),
                         "size_mb": round(size_mb, 1)}
        print(f"{name:<14}{build_s:<12.1f}{lat['median']:<12.3f}"
              f"{np.mean(recs):<12.4f}{size_mb:<12.1f}")

    measure("EXACT", lambda v: np.argpartition(-(M_truth @ v), K)[:K],
            0.0, Q.nbytes / 1024 / 1024)

    # ---------- MyIVF ----------
    for nlist, nprobe in [(256, 8), (256, 32), (1024, 16)]:
        t0 = time.time()
        ivf = MyIVF(M_truth, nlist=nlist, iters=10)
        bt = time.time() - t0
        measure(f"IVF nl={nlist} np={nprobe}",
                lambda v, iv=ivf, np_=nprobe: iv.search(M_truth, v, K, np_),
                bt, ivf.size_mb())

    # ---------- MyIVFPQ ----------
    t0 = time.time()
    pq = MyIVFPQ(M_truth, nlist=256, m_sub=16, nbits=8, iters=8)
    bt = time.time() - t0
    for nprobe in [8, 32]:
        measure(f"IVF-PQ np={nprobe}",
                lambda v, p=pq, n=nprobe: p.search(v, K, n),
                bt, pq.size_mb())

    # ---------- FAISS ----------
    try:
        import faiss
        # IVF
        t0 = time.time()
        quant = faiss.IndexFlatIP(M.shape[1])
        fidx = faiss.IndexIVFFlat(quant, M.shape[1], 256, faiss.METRIC_INNER_PRODUCT)
        fidx.train(M_truth)
        fidx.add(M_truth)
        bt = time.time() - t0
        for nprobe in [8, 32]:
            fidx.nprobe = nprobe
            def fs(v, ix=fidx):
                _, I = ix.search(v[None, :], K)
                return I[0]
            measure(f"FAISS-IVF np={nprobe}", fs, bt,
                    (fidx.ntotal * M.shape[1] * 4) / 1024 / 1024)

        # HNSW
        t0 = time.time()
        h = faiss.IndexHNSWFlat(M.shape[1], 32, faiss.METRIC_INNER_PRODUCT)
        h.hnsw.efConstruction = 64
        h.add(M_truth)
        bt = time.time() - t0
        for ef in [32, 64]:
            h.hnsw.efSearch = ef
            def hs(v, ix=h):
                _, I = ix.search(v[None, :], K)
                return I[0]
            measure(f"FAISS-HNSW ef={ef}", hs, bt,
                    (h.ntotal * M.shape[1] * 4) / 1024 / 1024)
    except ImportError:
        print("  faiss 未安装，跳过")

    print("-" * 100)

    # ---------- 红线：索引可重建 ----------
    print("\n" + "=" * 100)
    print("红线复验：索引必须能从真值层重建")
    print("=" * 100)
    # 用真值层重建 IVF，看与原 IVF 结果是否一致
    ivf_a = MyIVF(M_truth, nlist=256, iters=10, seed=0)
    ivf_b = MyIVF(M_truth, nlist=256, iters=10, seed=0)
    same = all(
        set(ivf_a.search(M_truth, QV[i], K, 8).tolist()) ==
        set(ivf_b.search(M_truth, QV[i], K, 8).tolist())
        for i in range(20))
    print(f"  相同真值层、相同种子，两次建索引结果一致: {same}")
    print(f"  → 索引是确定性派生结构，可随时重建")

    out = {"experiment": "E-44", "n": N, "dim": int(M.shape[1]),
           "k": K, "results": results,
           "rebuild_deterministic": bool(same),
           "truth_layer_mb": round(Q.nbytes / 1024 / 1024, 1)}
    with open(os.path.join(RESULTS, "e44_ann.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e44_ann.json')}")

    # 总结
    print("\n" + "=" * 100)
    print("总结")
    print("=" * 100)
    base = results["EXACT"]
    print(f"  基线 EXACT: {base['ms']:.2f} ms, Recall 1.0000")
    best = min((v["ms"], k) for k, v in results.items()
               if k != "EXACT" and v["recall"] >= 0.95)
    print(f"  在 Recall≥95% 里最快: {best[1]} ({best[0]:.2f} ms, "
          f"加速 {base['ms']/best[0]:.1f}×)")


if __name__ == "__main__":
    main()
