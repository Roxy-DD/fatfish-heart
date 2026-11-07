"""
E-23 特征对比：还有比原始像素更好的输入吗？

方法论来源（docs/00 方法论卡片 §3.3）：
  "前置的这种数据处理、信息录入只是一种方式"
  → 换接口可能比优化逻辑更有效（已实证：0.8831 → 0.9441）

候选特征（全部零下载、本机可跑）：
  raw784     原始像素（当前最好）
  hog        方向梯度直方图（专为手写/形状识别发明的）
  pool2      2x2 均值池化（抗平移）
  center     去均值（去公共背景）
  pca64/128  主成分降维（去噪）
  pixel+hog  拼接

每个特征都要：
  1. 重新标定阈值（0.705/0.490 都是特定空间的值，不能搬）
  2. 用平坦检索 + 类内 top-3
  3. 3 seeds

不预设门槛。只问：哪个最好，边界在哪。
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

from core import CorpusCallosum
from retina import load_mnist

RESULTS = os.path.join(_HERE, "results")
TASKS = {"A": {"0", "1", "2"}, "B": {"3", "4", "5"}, "C": {"6", "7", "8", "9"}}
TR, TE = 900, 300
SEEDS = [0, 1, 2]


def l2(v):
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    n[n == 0] = 1.0
    return v / n


# ---------------- 特征实现 ----------------

def f_raw(X):
    v = X.reshape(len(X), -1).astype(np.float32) / 255.0
    return l2(v)


def _sobel(img):
    """返回 gx, gy（简单中心差分）"""
    gx = np.zeros_like(img)
    gy = np.zeros_like(img)
    gx[:, 1:-1] = img[:, 2:] - img[:, :-2]
    gy[1:-1, :] = img[2:, :] - img[:-2, :]
    return gx, gy


def f_hog(X, cells: int = 4, bins: int = 8):
    """
    简化 HOG：把 28x28 分成 cells×cells 个格子，
    每格统计梯度方向直方图（bins 个方向），并做块内归一化。
    输出维度 = cells*cells*bins。cells=4,bins=8 → 128 维。
    """
    Xf = X.astype(np.float32) / 255.0
    n = len(Xf)
    cs = 28 // cells
    feats = np.zeros((n, cells * cells * bins), np.float32)
    edges = np.linspace(-np.pi, np.pi, bins + 1)
    for k in range(n):
        gx, gy = _sobel(Xf[k])
        mag = np.sqrt(gx * gx + gy * gy)
        ang = np.arctan2(gy, gx)
        slot = np.clip(np.digitize(ang, edges) - 1, 0, bins - 1)
        out = np.zeros((cells, cells, bins), np.float32)
        for cy in range(cells):
            for cx in range(cells):
                m = mag[cy * cs:(cy + 1) * cs, cx * cs:(cx + 1) * cs].ravel()
                s = slot[cy * cs:(cy + 1) * cs, cx * cs:(cx + 1) * cs].ravel()
                for b in range(bins):
                    out[cy, cx, b] = m[s == b].sum()
        out = out.ravel()
        # L2 归一化（块归一化的简化版）
        nn = np.linalg.norm(out)
        feats[k] = out / nn if nn > 0 else out
    return feats


def f_pool2(X, k: int = 2):
    Xf = X.astype(np.float32) / 255.0
    n, h, w = Xf.shape
    h2, w2 = h // k, w // k
    out = Xf[:, :h2 * k, :w2 * k].reshape(n, h2, k, w2, k).mean(axis=(2, 4))
    return l2(out.reshape(n, -1))


def f_center(X):
    v = f_raw(X)
    mu = v.mean(axis=0, keepdims=True)
    return l2(v - mu)


def make_pca(X, n_comp):
    v = f_raw(X)
    mu = v.mean(axis=0, keepdims=True)
    vc = v - mu
    # SVD 降维
    U, S, Vt = np.linalg.svd(vc[:8000], full_matrices=False)
    W = Vt[:n_comp].T
    return W, mu


# ---------------- 阈值标定 ----------------

def best_threshold(F, y, seed=0, n=400):
    rng = np.random.default_rng(seed)
    idx = rng.choice(len(F), min(n, len(F)), replace=False)
    P = F[idx]
    ys = np.array([str(y[i]) for i in idx])
    S = P @ P.T
    iu = np.triu_indices(len(P), k=1)
    sims = S[iu]
    same = (ys[None, :] == ys[:, None])[iu]
    if same.sum() == 0 or (~same).sum() == 0:
        return 0.5, 0.0, 0.0, 0.0
    bt, bb = 0.5, 0.0
    for t in np.arange(0.05, 0.995, 0.005):
        tpr = float(np.mean(sims[same] > t))
        tnr = float(np.mean(sims[~same] <= t))
        b = 0.5 * (tpr + tnr)
        if b > bb:
            bb, bt = b, float(t)
    return bt, bb, float(sims[same].mean()), float(sims[~same].mean())


# ---------------- 学习+评测 ----------------

def split_idx(y, labels, ntr, nte, seed):
    rng = np.random.default_rng(seed)
    labels = {str(l) for l in labels}
    idx = np.array([i for i, lab in enumerate(y) if str(lab) in labels])
    rng.shuffle(idx)
    return idx[:ntr], idx[ntr:ntr + nte]


def run_feature(Ftr, ytr, Fte, yte, T, seed, topk=3):
    b = CorpusCallosum(threshold=T, seed=seed)
    for name in "ABC":
        itr, _ = split_idx(ytr, TASKS[name], TR, TE, seed)
        for i in itr:
            b.memorize(Ftr[i], str(ytr[i]))
    stars = b.all_stars()
    M = np.array([s.vector for s in stars], np.float32)
    L = [str(s.label) for s in stars]
    uniq = sorted(set(L))
    lid = np.array([uniq.index(l) for l in L], np.int32)
    masks = [np.where(lid == i)[0] for i in range(len(uniq))]
    uarr = np.array(uniq, dtype=object)

    accs = {}
    for name in "ABC":
        _, ite = split_idx(yte, TASKS[name], TR, TE, seed)
        Xt = np.stack([Fte[i] for i in ite])
        yt = np.array([str(yte[i]) for i in ite], dtype=object)
        S = Xt @ M.T
        sc = np.empty((len(Xt), len(uniq)), np.float32)
        for j, idx in enumerate(masks):
            v = S[:, idx]
            kk = min(topk, v.shape[1])
            sc[:, j] = np.partition(v, -kk, axis=1)[:, -kk:].mean(axis=1)
        accs[name] = float(np.mean(uarr[np.argmax(sc, axis=1)] == yt))
    return accs, len(stars), M.shape[1]


def main():
    Xtr, ytr = load_mnist(True)
    Xte, yte = load_mnist(False)
    # 只用前 12000 / 4000，控制内存与时间
    Xtr, ytr = Xtr[:12000], ytr[:12000]
    Xte, yte = Xte[:4000], yte[:4000]
    t0 = time.time()

    print("=" * 104)
    print("E-23 特征对比（每个特征独立标定阈值）")
    print("=" * 104)

    # 构造各特征（只算一次）
    print("构造特征...")
    feats = {}
    feats["raw784"] = (f_raw(Xtr), f_raw(Xte))
    print("  raw784 ok")
    h_tr, h_te = f_hog(Xtr), f_hog(Xte)
    feats["hog128"] = (h_tr, h_te)
    print("  hog128 ok")
    feats["pool2_196"] = (f_pool2(Xtr), f_pool2(Xte))
    print("  pool2 ok")
    # center 需要统一均值
    r_tr = f_raw(Xtr)
    mu_c = r_tr.mean(axis=0, keepdims=True)
    feats["center784"] = (l2(r_tr - mu_c), l2(f_raw(Xte) - mu_c))
    print("  center ok")
    for nc in (64, 128):
        W, mu_p = make_pca(Xtr, nc)
        feats[f"pca{nc}"] = (l2((f_raw(Xtr) - mu_p) @ W), l2((f_raw(Xte) - mu_p) @ W))
        print(f"  pca{nc} ok")
    # 拼接 pixel + hog
    feats["pixel+hog"] = (l2(np.hstack([feats["raw784"][0], h_tr])),
                          l2(np.hstack([feats["raw784"][1], h_te])))
    print("  pixel+hog ok")
    print(f"特征构造耗时 {time.time()-t0:.0f}s\n")

    # 逐个评估
    rows = []
    print(f"{'特征':<14}{'维度':<8}{'标定T':<10}{'同类cos':<10}{'异类cos':<10}{'间隔':<9}"
          f"{'A':<9}{'B':<9}{'C':<9}{'macro':<9}{'星数':<8}")
    print("-" * 104)
    for name, (Ftr_, Fte_) in feats.items():
        T, bacc, same_m, diff_m = best_threshold(Ftr_, ytr)
        acc_runs = {"A": [], "B": [], "C": []}
        stars_runs, dim = [], 0
        for seed in SEEDS:
            accs, ns, dim = run_feature(Ftr_, ytr, Fte_, yte, T, seed)
            for k in "ABC":
                acc_runs[k].append(accs[k])
            stars_runs.append(ns)
        A, B, C = [float(np.mean(acc_runs[k])) for k in "ABC"]
        macro = float(np.mean([A, B, C]))
        rows.append({"feature": name, "dim": int(dim), "threshold": round(T, 4),
                     "balanced_acc": round(bacc, 4),
                     "same_cos": round(same_m, 4), "diff_cos": round(diff_m, 4),
                     "gap": round(same_m - diff_m, 4),
                     "A": round(A, 4), "B": round(B, 4), "C": round(C, 4),
                     "macro": round(macro, 4),
                     "n_stars": int(np.mean(stars_runs))})
        print(f"{name:<14}{dim:<8d}{T:<10.3f}{same_m:<10.4f}{diff_m:<10.4f}"
              f"{same_m-diff_m:<9.4f}{A:<9.4f}{B:<9.4f}{C:<9.4f}{macro:<9.4f}"
              f"{np.mean(stars_runs):<8.0f}")
    print("-" * 104)

    best = max(rows, key=lambda r: r["macro"])
    base = next(r for r in rows if r["feature"] == "raw784")
    print(f"\n基线 raw784: macro={base['macro']:.4f} (T={base['threshold']:.3f}, "
          f"星数 {base['n_stars']})")
    print(f"★ 最佳 {best['feature']}: macro={best['macro']:.4f} "
          f"({best['macro']-base['macro']:+.4f}, 维度 {best['dim']}, "
          f"星数 {best['n_stars']}, T={best['threshold']:.3f})")

    print("\n按维度排序（看'信息量'与'效果'的关系）:")
    for r in sorted(rows, key=lambda x: x["dim"]):
        print(f"  {r['feature']:<14} dim={r['dim']:<6d} macro={r['macro']:.4f}  "
              f"间隔={r['gap']:.4f}")

    print(f"\n耗时 {time.time()-t0:.0f}s")

    out = {"experiment": "E-23", "setting": "pixel-space features, flat retrieval, top3",
           "seeds": SEEDS, "per_task": [TR, TE], "rows": rows,
           "best": best, "baseline": base,
           "note": "每个特征独立标定阈值；0.705/0.490 不可跨空间搬运"}
    with open(os.path.join(RESULTS, "e23_features.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e23_features.json')}")


if __name__ == "__main__":
    main()
