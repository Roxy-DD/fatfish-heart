"""
E-32 系统检索到的工程方法，逐个试

从文献找到的、与本系统直接相关的方法（来源见 docs/28）：

【检索侧】
  M1  BKNN 平衡分数：score = (s_k - mean_over_all_classes) / mean_over_all_classes
      → 让"类内绝对相似度"变成"相对突出度"，消除类大小偏置
      （Wang/Huang/Ding, AAAI: Class Balanced KNN）
  M2  类内 b 邻居（每类取固定 b 个，不是全局 top-k）
  M3  温度 softmax 聚合（替代简单均值）
  M4  距离加权（同类别的多个邻居按相似度加权）

【存储侧】
  M5  SQ8 标量量化（float32 → int8，4 倍压缩）
  M6  PQ 乘积量化（128 维 → m 字节）
  M7  原型选择：medoid（每类取离类中心最近的那颗）

【检索加速】
  M8  IVF 倒排索引（先比质心，只扫最近的几个桶）

本实验：**逐个单独测**，记录每个方法各自的增益/损失。
下一轮（E-33）再叠加。
"""
from __future__ import annotations

import json
import os
import sys
import time

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_lite"))

import numpy as np

from retina import load_mnist
from run_e23_features import f_hog
from run_e27_alignment import center_img

RESULTS = os.path.join(_HERE, "results")
THR = 0.560
SEEDS = [0, 1, 2]


def build(F, y, n, seed, threshold=THR):
    rng = np.random.default_rng(seed)
    idx = np.arange(len(y)); rng.shuffle(idx); idx = idx[:n]
    M, L, mass = [], [], []
    for i in idx:
        x, lab = F[i], str(y[i])
        if M:
            sim = np.array(M, np.float32) @ x
            k = int(np.argmax(sim))
            if L[k] == lab and sim[k] > threshold:
                if mass[k] > 5:
                    M.append(x.copy()); L.append(lab); mass.append(1)
                else:
                    rate = 1.0 / (mass[k] + 1)
                    nv = M[k] * (1 - rate) + x * rate
                    nn = np.linalg.norm(nv)
                    M[k] = nv / nn if nn > 0 else nv
                    mass[k] += 1
                continue
        M.append(x.copy()); L.append(lab); mass.append(1)
    return np.array(M, np.float32), L, np.array(mass, np.int32)


class Idx:
    def __init__(self, M, L, mass=None):
        self.M = M
        self.L = [str(v) for v in L]
        self.mass = mass
        self.uniq = sorted(set(self.L))
        lid = np.array([self.uniq.index(l) for l in self.L], np.int32)
        self.masks = [np.where(lid == i)[0] for i in range(len(self.uniq))]
        self.arr = np.array(self.uniq, dtype=object)

    def S(self, X):
        return X @ self.M.T

    # ---- M0 基线：类内 top-k 均值 ----
    def pred_topk(self, X, k=3):
        S = self.S(X)
        sc = np.empty((len(X), len(self.uniq)), np.float32)
        for j, idx in enumerate(self.masks):
            v = S[:, idx]
            kk = min(k, v.shape[1])
            sc[:, j] = np.partition(v, -kk, axis=1)[:, -kk:].mean(axis=1)
        return self.arr[np.argmax(sc, axis=1)], sc

    # ---- M1 BKNN 平衡分数 ----
    def pred_balanced(self, X, k=3):
        _, sc = self.pred_topk(X, k)
        mu = sc.mean(axis=1, keepdims=True)
        sd = sc.std(axis=1, keepdims=True) + 1e-6
        bal = (sc - mu) / np.abs(mu)
        return self.arr[np.argmax(bal, axis=1)], bal

    # ---- M2 类内固定 b 邻居（每类同样多）----
    def pred_perclass_b(self, X, b=3):
        S = self.S(X)
        avail = np.array([len(m) for m in self.masks])
        b = int(max(1, min(b, avail.min())))      # 不多于最少类
        sc = np.empty((len(X), len(self.uniq)), np.float32)
        for j, idx in enumerate(self.masks):
            v = S[:, idx]
            kk = min(b, v.shape[1])
            sc[:, j] = np.partition(v, -kk, axis=1)[:, -kk:].mean(axis=1)
        return self.arr[np.argmax(sc, axis=1)], sc

    # ---- M3 温度 softmax 聚合 ----
    def pred_softmax(self, X, k=3, T=0.05):
        S = self.S(X)
        sc = np.empty((len(X), len(self.uniq)), np.float32)
        for j, idx in enumerate(self.masks):
            v = S[:, idx]
            kk = min(k, v.shape[1])
            top = np.partition(v, -kk, axis=1)[:, -kk:]
            m = top.max(axis=1, keepdims=True)
            lse = m + np.log(np.mean(np.exp((top - m) / T), axis=1, keepdims=True) + 1e-12)
            sc[:, j] = (lse * T).ravel()
        return self.arr[np.argmax(sc, axis=1)], sc

    # ---- M4 距离加权 ----
    def pred_weighted(self, X, k=5, gamma=8.0):
        S = self.S(X)
        sc = np.empty((len(X), len(self.uniq)), np.float32)
        for j, idx in enumerate(self.masks):
            v = S[:, idx]
            kk = min(k, v.shape[1])
            top = np.partition(v, -kk, axis=1)[:, -kk:]
            w = np.exp(gamma * top)
            sc[:, j] = (top * w).sum(axis=1) / (w.sum(axis=1) + 1e-12)
        return self.arr[np.argmax(sc, axis=1)], sc

    def acc(self, pred, y):
        yv = np.array([str(v) for v in y], dtype=object)
        return float(np.mean(pred == yv))


def medoid_select(M, L, mass=None):
    """M7 每类只留 medoid（离类中心最近的）"""
    L = [str(v) for v in L]
    uniq = sorted(set(L))
    keep = []
    for u in uniq:
        idx = np.array([i for i, l in enumerate(L) if l == u])
        if len(idx) <= 1:
            keep.extend(idx.tolist()); continue
        c = M[idx].mean(axis=0)
        c /= max(np.linalg.norm(c), 1e-9)
        sims = M[idx] @ c
        keep.append(int(idx[np.argmax(sims)]))
    return M[keep].copy(), [L[i] for i in keep]


def quantize_sq8(M):
    """M5 SQ8：按维度映射到 int8"""
    lo = M.min(axis=0)
    hi = M.max(axis=0)
    scale = (hi - lo) / 255.0
    scale[scale == 0] = 1e-9
    Q = np.clip(((M - lo) / scale).round(), 0, 255).astype(np.uint8)
    return Q, lo, scale


def dequant_sq8(Q, lo, scale):
    return Q.astype(np.float32) * scale + lo


def quantize_pq(M, m_sub=16, n_cent=64, seed=0):
    """M6 PQ：切成 m_sub 段，每段 k-means 到 n_cent 个码字"""
    n, d = M.shape
    sub_dim = d // m_sub
    d = sub_dim * m_sub
    M2 = M[:, :d]
    codebooks = []
    codes = np.zeros((n, m_sub), np.uint8)
    rng = np.random.default_rng(seed)
    for s in range(m_sub):
        seg = M2[:, s * sub_dim:(s + 1) * sub_dim]
        # 简化 k-means（10 次迭代）
        cen = seg[rng.choice(n, n_cent, replace=False)].copy()
        for _ in range(10):
            dist = ((seg[:, None, :] - cen[None, :, :]) ** 2).sum(axis=2)
            lab = dist.argmin(axis=1)
            for c in range(n_cent):
                mm = lab == c
                if mm.any():
                    cen[c] = seg[mm].mean(axis=0)
        dist = ((seg[:, None, :] - cen[None, :, :]) ** 2).sum(axis=2)
        codes[:, s] = dist.argmin(axis=1)
        codebooks.append(cen)
    return codes, codebooks, m_sub, sub_dim, d


def decode_pq(codes, codebooks, m_sub, sub_dim, d):
    n = len(codes)
    out = np.zeros((n, d), np.float32)
    for s in range(m_sub):
        out[:, s * sub_dim:(s + 1) * sub_dim] = codebooks[s][codes[:, s]]
    nn = np.linalg.norm(out, axis=1, keepdims=True)
    nn[nn == 0] = 1
    return out / nn


def main():
    Xtr, ytr = load_mnist(True)
    Xte, yte = load_mnist(False)
    Xtr, ytr = Xtr[:12000], ytr[:12000]
    Xte, yte = Xte[:4000], yte[:4000]
    Ctr, Cte = center_img(Xtr), center_img(Xte)
    Ftr, Fte = f_hog(Ctr), f_hog(Cte)
    N = 4000
    t0 = time.time()

    print("=" * 108)
    print("E-32 逐个验证工程方法（基线=类内top3）")
    print("=" * 108)

    # 用 seed 0 建库做方法筛选，再在 3 seeds 上确认
    results = {}

    def run(name, fn, note=""):
        accs = []
        for seed in SEEDS:
            M, L, ms = build(Ftr, ytr, N, seed)
            ix = Idx(M, L, ms)
            accs.append(ix.acc(fn(ix, Fte), yte))
        m = float(np.mean(accs))
        results[name] = {"acc": round(m, 4), "std": round(float(np.std(accs)), 4),
                         "note": note}
        return m

    # 基线
    M0, L0, MS0 = build(Ftr, ytr, N, 0)
    ix0 = Idx(M0, L0, MS0)
    base = ix0.acc(ix0.pred_topk(Fte)[0], yte)
    print(f"\n基线 M0（类内 top3）: acc={base:.4f}  星数={ix0.M.shape[0]}")
    results["M0_baseline"] = {"acc": round(base, 4), "note": "类内top3均值"}

    print("\n" + "-" * 108)
    print("【检索侧】")
    print("-" * 108)
    print(f"{'方法':<28}{'acc':<10}{'相对基线':<12}{'std':<10}{'说明'}")
    print("-" * 108)

    b1 = run("M1_bknn", lambda ix, X: ix.pred_balanced(X)[0], "BKNN 相对分数")
    print(f"{'M1 BKNN 平衡分数':<28}{b1:<10.4f}{b1-base:<+12.4f}"
          f"{results['M1_bknn']['std']:<10.4f}{'AAAI Class Balanced KNN 思路'}")

    for b in [2, 3, 5]:
        nm = f"M2_perclass_b{b}"
        v = run(nm, lambda ix, X, b=b: ix.pred_perclass_b(X, b)[0],
                f"每类固定 {b} 邻居")
        print(f"{f'M2 每类固定 b={b}':<28}{v:<10.4f}{v-base:<+12.4f}"
              f"{results[nm]['std']:<10.4f}{'每类候选数相等'}")

    for T in [0.02, 0.05, 0.1]:
        nm = f"M3_softmax_T{T}"
        v = run(nm, lambda ix, X, T=T: ix.pred_softmax(X, 3, T)[0], f"温度 T={T}")
        print(f"{f'M3 softmax T={T}':<28}{v:<10.4f}{v-base:<+12.4f}"
              f"{results[nm]['std']:<10.4f}{'温度聚合替代均值'}")

    for g in [5.0, 8.0]:
        nm = f"M4_weighted_g{g}"
        v = run(nm, lambda ix, X, g=g: ix.pred_weighted(X, 5, g)[0], f"gamma={g}")
        print(f"{f'M4 距离加权 g={g}':<28}{v:<10.4f}{v-base:<+12.4f}"
              f"{results[nm]['std']:<10.4f}{'按相似度加权聚合'}")

    print("\n" + "-" * 108)
    print("【存储侧】")
    print("-" * 108)
    # M7 medoid
    M7, L7 = medoid_select(M0, L0)
    acc7 = Idx(M7, L7).acc(Idx(M7, L7).pred_topk(Fte)[0], yte)
    print(f"{'M7 medoid（每类1颗）':<28}{acc7:<10.4f}{acc7-base:<+12.4f}"
          f"{'-':<10}星数 {len(L7)}（原 {len(L0)}）")
    results["M7_medoid"] = {"acc": round(acc7, 4), "n_stars": len(L7),
                            "note": "每类只留类中心最近的原型"}

    # M5 SQ8
    Q, lo, sc_ = quantize_sq8(M0)
    M5 = dequant_sq8(Q, lo, sc_)
    ix5 = Idx(M5, L0)
    acc5 = ix5.acc(ix5.pred_topk(Fte)[0], yte)
    q_bytes = Q.nbytes
    print(f"{'M5 SQ8 量化':<28}{acc5:<10.4f}{acc5-base:<+12.4f}"
          f"{'-':<10}存储 {q_bytes/1024:.0f}KB（原 {M0.nbytes/1024:.0f}KB）")
    results["M5_sq8"] = {"acc": round(acc5, 4), "bytes": int(q_bytes),
                         "bytes_raw": int(M0.nbytes), "note": "float32→uint8"}

    # M6 PQ
    for m_sub in [8, 16]:
        codes, cbs, ms_, sd_, d_ = quantize_pq(M0, m_sub=m_sub, n_cent=64)
        M6 = decode_pq(codes, cbs, ms_, sd_, d_)
        ix6 = Idx(M6, L0)
        acc6 = ix6.acc(ix6.pred_topk(Fte)[0], yte)
        print(f"{f'M6 PQ m={m_sub}':<28}{acc6:<10.4f}{acc6-base:<+12.4f}"
              f"{'-':<10}存储 {codes.nbytes/1024:.0f}KB（原 {M0.nbytes/1024:.0f}KB）")
        results[f"M6_pq{m_sub}"] = {"acc": round(acc6, 4), "bytes": int(codes.nbytes),
                                    "note": f"PQ m={m_sub}, 64码字/段"}

    print("\n" + "-" * 108)
    print("【汇总】按增益排序")
    print("-" * 108)
    for k, v in sorted(results.items(), key=lambda kv: -kv[1]["acc"]):
        print(f"  {k:<24} acc={v['acc']:.4f}  ({v['acc']-base:+.4f})  {v.get('note','')}")

    print(f"\n耗时 {time.time()-t0:.0f}s")

    out = {"experiment": "E-32", "baseline": round(base, 4),
           "n_stars": len(L0), "methods": results,
           "seeds": SEEDS, "n_train": N,
           "source": "docs/28 工程方法检索",
           "elapsed_sec": round(time.time() - t0, 1)}
    with open(os.path.join(RESULTS, "e32_methods.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"写入 {os.path.join(RESULTS, 'e32_methods.json')}")


if __name__ == "__main__":
    main()
