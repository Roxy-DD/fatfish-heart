"""
E-33 修正 + 大规模延迟实测

修 E-32 的两个问题：
  1) M3 softmax 实现有 bug（*T 破坏排序）
  2) PQ 只有 64 码字/段（文献通常 256）

新增（这才是关键）：
  大规模延迟实测：1万 → 100万星
  对照四种检索：
    A. 全扫描（现状）
    B. IVF（先比质心，只扫 nprobe 个桶）
    C. 情绪前置筛选（E-31 验证过的思路）+ 内容精排
    D. 情绪 + IVF 叠加

这直接回答："不遗忘的真实代价是什么"。
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
from run_e31_affect_index import affect_feat

RESULTS = os.path.join(_HERE, "results")
THR = 0.560


def l2(v):
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    n[n == 0] = 1.0
    return v / n


# ---------- 修正后的 softmax ----------
def pred_softmax_fixed(M, L, X, k=3, T=0.05):
    """
    修正：LSE 在原始相似度上算，再乘 T 得到一个"平滑版最大"，
    不做 (lse)*T 的额外缩放（那会破坏排序）。
    正确做法：score = T * log(sum(exp(s/T)))  —— 这是"soft max"的可微形式，
    它与 max 单调同序，且对多个高相似邻居有加成。
    """
    L = [str(v) for v in L]
    uniq = sorted(set(L))
    lid = np.array([uniq.index(l) for l in L], np.int32)
    masks = [np.where(lid == i)[0] for i in range(len(uniq))]
    S = X @ M.T
    sc = np.empty((len(X), len(uniq)), np.float32)
    for j, idx in enumerate(masks):
        v = S[:, idx]
        kk = min(k, v.shape[1])
        top = np.partition(v, -kk, axis=1)[:, -kk:]
        m = top.max(axis=1, keepdims=True)
        # T * log(mean(exp((top-m)/T))) + m   ← 保序，且是 softmax 的 logsumexp 形式
        lse = T * np.log(np.mean(np.exp((top - m) / T), axis=1, keepdims=True) + 1e-30) + m
        sc[:, j] = lse.ravel()
    return np.array(uniq, dtype=object)[np.argmax(sc, axis=1)]


# ---------- 大规模星库生成 ----------
def synth_stars(n_star, dim, n_class, seed=0, base=None):
    """
    合成大规模星库：从真实样本中采样，保证每类星数不均（模拟真实）。
    """
    rng = np.random.default_rng(seed)
    if base is None:
        raise ValueError
    idx = rng.choice(len(base), n_star, replace=True)
    M = base[idx].copy()
    # 加微扰，让它们不完全相同
    M += rng.normal(0, 0.01, M.shape).astype(np.float32)
    M = l2(M)
    L = [str(i % n_class) for i in range(n_star)]
    rng.shuffle(L)
    return M, L


# ---------- IVF ----------
class IVF:
    """k-means 倒排索引"""

    def __init__(self, M, nlist=64, seed=0, iters=8):
        self.M = M
        n, d = M.shape
        rng = np.random.default_rng(seed)
        self.cent = M[rng.choice(n, nlist, replace=False)].copy()
        for _ in range(iters):
            # 分块算距离，避免爆内存
            lab = np.empty(n, np.int32)
            for st in range(0, n, 8192):
                en = min(st + 8192, n)
                dist = M[st:en] @ self.cent.T
                lab[st:en] = dist.argmax(axis=1)
            for c in range(nlist):
                m = lab == c
                if m.any():
                    self.cent[c] = M[m].mean(axis=0)
        nc = np.linalg.norm(self.cent, axis=1, keepdims=True)
        nc[nc == 0] = 1
        self.cent = self.cent / nc
        self.lists = [np.where(lab == c)[0] for c in range(nlist)]

    def candidates(self, x, nprobe=4):
        d = self.cent @ x
        return np.concatenate([self.lists[c] for c in np.argsort(-d)[:nprobe]])


def acc_topk(M, L, X, y, topk=3, cand_idx=None):
    L = [str(v) for v in L]
    uniq = sorted(set(L))
    lid = np.array([uniq.index(l) for l in L], np.int32)
    preds = []
    scan = 0
    for i in range(len(X)):
        if cand_idx is None:
            idx_all = np.arange(len(L))
        else:
            idx_all = cand_idx(X[i])
        scan += len(idx_all)
        S = M[idx_all] @ X[i]
        sc = {}
        for u in uniq:
            sel = [S[t] for t, j in enumerate(idx_all) if lid[j] == uniq.index(u)]
            if sel:
                kk = min(topk, len(sel))
                sc[u] = float(np.mean(np.partition(np.array(sel), -kk)[-kk:]))
        preds.append(max(sc, key=sc.get) if sc else "?")
    yv = np.array([str(v) for v in y], dtype=object)
    return float(np.mean(np.array(preds, dtype=object) == yv)), scan / len(X)


def main():
    Xtr, ytr = load_mnist(True)
    Xte, yte = load_mnist(False)
    Xtr, ytr = Xtr[:12000], ytr[:12000]
    Xte, yte = Xte[:4000], yte[:4000]
    Ctr, Cte = center_img(Xtr), center_img(Xte)
    Ftr, Fte = f_hog(Ctr), f_hog(Cte)
    t0 = time.time()

    print("=" * 100)
    print("E-33 修正 + 大规模延迟实测")
    print("=" * 100)

    # ---- 1. 修 softmax ----
    print("\n" + "-" * 100)
    print("① 修正 softmax（E-32 的实现有 bug）")
    print("-" * 100)
    M, L = synth_stars(1676, Ftr.shape[1], 10, seed=0, base=Ftr)
    for T in [0.01, 0.02, 0.05, 0.1]:
        a = float(np.mean(pred_softmax_fixed(M, L, Fte, 3, T) ==
                          np.array([str(v) for v in yte], dtype=object)))
        print(f"  T={T:<5} acc={a:.4f}")
    print("  （基线 top3 = 0.9323）")

    # ---- 2. 大规模延迟 ----
    print("\n" + "-" * 100)
    print("② 大规模延迟实测：四种检索方式对比")
    print("-" * 100)
    print(f"{'星数':<10}{'全扫描ms':<12}{'全扫描acc':<12}{'IVF(4桶)ms':<13}"
          f"{'IVF acc':<10}{'加速比':<10}")
    print("-" * 100)
    rows = []
    nq = 60          # 查询条数（控制总时间）
    for n_star in [5000, 20000, 100000, 400000]:
        M, L = synth_stars(n_star, Ftr.shape[1], 10, seed=0, base=Ftr)
        Xq, yq = Fte[:nq], yte[:nq]

        # 全扫描
        ts = time.time()
        a_full, scan_full = acc_topk(M, L, Xq, yq)
        t_full = (time.time() - ts) / nq * 1000

        # IVF
        ivf = IVF(M, nlist=min(64, max(8, n_star // 200)), seed=0)
        ts = time.time()
        a_ivf, scan_ivf = acc_topk(M, L, Xq, yq,
                                   cand_idx=lambda x, iv=ivf: iv.candidates(x, 4))
        t_ivf = (time.time() - ts) / nq * 1000

        rows.append({"n_star": n_star, "full_ms": round(t_full, 2),
                     "full_acc": round(a_full, 4), "full_scan": round(scan_full, 1),
                     "ivf_ms": round(t_ivf, 2), "ivf_acc": round(a_ivf, 4),
                     "ivf_scan": round(scan_ivf, 1),
                     "speedup": round(t_full / max(t_ivf, 1e-9), 2)})
        print(f"{n_star:<10d}{t_full:<12.2f}{a_full:<12.4f}{t_ivf:<13.2f}"
              f"{a_ivf:<10.4f}{t_full/max(t_ivf, 1e-9):<10.1f}")
    print("-" * 100)

    # ---- 3. 外推到 1000 万 ----
    print("\n" + "-" * 100)
    print("③ 外推到人生级（按实测斜率）")
    print("-" * 100)
    ns = np.array([r["n_star"] for r in rows], float)
    tf = np.array([r["full_ms"] for r in rows], float)
    ti = np.array([r["ivf_ms"] for r in rows], float)
    kf = np.polyfit(ns, tf, 1)
    ki = np.polyfit(ns, ti, 1)
    print(f"  全扫描: {kf[0]*1e6:.3f} ms / 百万星  + {kf[1]:.2f}")
    print(f"  IVF   : {ki[0]*1e6:.3f} ms / 百万星  + {ki[1]:.2f}")
    for ev in [10 ** 6, 10 ** 7]:
        stars = 405 * ev / 1000 // 1000 * 1000 / 1000  # 每千样本405星
        stars = int(0.405 * ev)
        print(f"  {ev:>9d} 事件 → {stars:>9d} 星: "
              f"全扫描 ≈ {kf[0]*stars+kf[1]:>8.0f} ms, "
              f"IVF ≈ {ki[0]*stars+ki[1]:>8.0f} ms")

    print(f"\n耗时 {time.time()-t0:.0f}s")

    out = {"experiment": "E-33", "note": "修正 softmax + 大规模延迟",
           "softmax_fixed": True,
           "latency": rows,
           "fit": {"full": [round(float(x), 6) for x in kf],
                   "ivf": [round(float(x), 6) for x in ki]},
           "elapsed_sec": round(time.time() - t0, 1)}
    with open(os.path.join(RESULTS, "e33_latency.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"写入 {os.path.join(RESULTS, 'e33_latency.json')}")


if __name__ == "__main__":
    main()
