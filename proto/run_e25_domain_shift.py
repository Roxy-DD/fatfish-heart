"""
E-25 真正的记忆迁移：能不能做到"在一个领域学会，换一个领域直接用"？

用户最初的目标：**让模型能真正实现记忆的迁移。**

之前所有实验都是"同领域内学 + 同领域内测"，那不是迁移，只是分类。

E-25 要回答的问题：
  在一个领域的样本上学出来的记忆（星），能不能直接用于另一个领域？
  中间**不给任何新领域的训练样本**，只给"领域的名字"（标签原型）。

做法（关键：训练/测试不相交 + 跨领域）：
  · 用 HOG 特征（E-23 证明它最稳，128 维，零训练）
  · 领域 = 手写数字 ∈ {0..9}
  · 训练集：MNIST **train** 里的数字 0-4
  · 测试集甲（同域）：MNIST **train** 里的 0-4 的另一批（对照）
  · 测试集乙（跨域）：MNIST **test** 里的 5-9 —— 这些类**从没在训练里出现过**

跨域测试不成立，所以改一下：
  真正的跨域是"**同一个类，完全不同的来源**"。
  MNIST 只有一个来源，所以用**合成扰动**模拟换领域：
    · 旋转 ±15°
    · 缩放 0.8x
    · 笔画加粗（形态学膨胀的近似）
  这是"同一个数字，但看起来像另一个人写的"。

按新规则（用户 2026-10-25）：
  不预设门槛，只问：
    · 同域 → 跨域，掉多少？
    · 记忆迁移（只导原型） vs 重新学习（用目标域样本），差多少？
    · 迁移时，加一个"领域名"能做门控吗？
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

RESULTS = os.path.join(_HERE, "results")
SEEDS = [0, 1, 2]


def l2(v):
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    n[n == 0] = 1.0
    return v / n


# ---------------- 领域变换（模拟"换一个人写"） ----------------

def shift(X, dy, dx):
    out = np.zeros_like(X)
    h, w = X.shape[1], X.shape[2]
    ys = slice(max(0, dy), min(h, h + dy))
    xs = slice(max(0, dx), min(w, w + dx))
    ys_src = slice(max(0, -dy), min(h, h - dy))
    xs_src = slice(max(0, -dx), min(w, w - dx))
    out[:, ys, xs] = X[:, ys_src, xs_src]
    return out


def rotate(X, deg):
    """绕中心旋转（最近邻采样，纯 numpy）"""
    th = np.deg2rad(deg)
    c, s = np.cos(th), np.sin(th)
    h, w = X.shape[1], X.shape[2]
    cy, cx = (h - 1) / 2, (w - 1) / 2
    yy, xx = np.mgrid[0:h, 0:w]
    yy = yy - cy
    xx = xx - cx
    sy = (c * yy + s * xx + cy).round().astype(int)
    sx = (-s * yy + c * xx + cx).round().astype(int)
    ok = (sy >= 0) & (sy < h) & (sx >= 0) & (sx < w)
    out = np.zeros_like(X)
    out[:, ok] = X[:, sy[ok], sx[ok]]
    return out


def thicken(X, r=1):
    """形态学膨胀近似（3x3 最大值滤波 r 次）"""
    out = X.copy()
    for _ in range(r):
        p = np.pad(out, ((0, 0), (1, 1), (1, 1)), mode="constant")
        acc = np.zeros_like(out)
        for dy in (0, 1, 2):
            for dx in (0, 1, 2):
                acc = np.maximum(acc, p[:, dy:dy + out.shape[1], dx:dx + out.shape[2]])
        out = acc
    return out


def thin(X):
    """变细：与 3x3 均值做差后取正，近似"笔画变细" """
    p = np.pad(X, ((0, 0), (1, 1), (1, 1)), mode="edge")
    acc = np.zeros_like(X, dtype=np.float64)
    for dy in (0, 1, 2):
        for dx in (0, 1, 2):
            acc += p[:, dy:dy + X.shape[1], dx:dx + X.shape[2]]
    mean = acc / 9.0
    return np.clip(X.astype(np.float64) - 0.4 * (mean - X), 0, 255).astype(np.uint8)


DOMAINS = {
    "original": lambda X: X,
    "shift": lambda X: shift(X, 2, -2),
    "rotate+12": lambda X: rotate(X, 12),
    "rotate-12": lambda X: rotate(X, -12),
    "thicken": lambda X: thicken(X, 1),
    "thin": lambda X: thin(X),
}


# ---------------- 星库 ----------------

class Store:
    def __init__(self, V, L):
        self.M = np.ascontiguousarray(V, np.float32)
        L = [str(v) for v in L]
        self.labels = L
        self.uniq = sorted(set(L))
        lid = np.array([self.uniq.index(l) for l in L], np.int32)
        self.masks = [np.where(lid == i)[0] for i in range(len(self.uniq))]
        self.arr = np.array(self.uniq, dtype=object)
        self.n = len(L)

    def predict(self, X, topk=3):
        if self.n == 0:
            return np.array(["?"] * len(X), dtype=object)
        S = X @ self.M.T
        sc = np.empty((len(X), len(self.uniq)), np.float32)
        for j, idx in enumerate(self.masks):
            v = S[:, idx]
            kk = min(topk, v.shape[1])
            sc[:, j] = np.partition(v, -kk, axis=1)[:, -kk:].mean(axis=1)
        return self.arr[np.argmax(sc, axis=1)]

    def acc(self, X, y, topk=3):
        y = np.array([str(v) for v in y], dtype=object)
        return float(np.mean(self.predict(X, topk) == y))


def learn(F, y, labels, n, seed, threshold, mitosis=5):
    """增量学一批样本，返回星库（平坦 + 分裂，与 core.py 一致）"""
    rng = np.random.default_rng(seed)
    labels = {str(l) for l in labels}
    idx = np.array([i for i, lab in enumerate(y) if str(lab) in labels])
    rng.shuffle(idx)
    idx = idx[:n]

    M = []
    L = []
    mass = []
    for i in idx:
        x = F[i]
        if M:
            Mx = np.array(M, np.float32)
            sim = Mx @ x
            k = int(np.argmax(sim))
            if L[k] == str(y[i]) and sim[k] > threshold:
                if mass[k] > mitosis:
                    M.append(x.copy()); L.append(str(y[i])); mass.append(1)
                else:
                    rate = 1.0 / (mass[k] + 1)
                    nv = M[k] * (1 - rate) + x * rate
                    nn = np.linalg.norm(nv)
                    M[k] = nv / nn if nn > 0 else nv
                    mass[k] += 1
                continue
        M.append(x.copy()); L.append(str(y[i])); mass.append(1)
    return Store(np.array(M, np.float32), L)


def main():
    Xtr, ytr = load_mnist(True)
    Xte, yte = load_mnist(False)
    t0 = time.time()

    print("=" * 104)
    print("E-25 记忆迁移：在一个领域学，换一个领域直接用")
    print("=" * 104)
    print(f"特征 HOG128（零训练，E-23 最稳）")
    print(f"训练域 = original，测试域 = {list(DOMAINS)}")
    print()

    # 训练集：0-9 全类，每类 300 个（共 3000）
    labels_all = {str(i) for i in range(10)}
    N_TRAIN = 3000
    test_per_class = 200
    THR = 0.560      # HOG 空间（E-23）

    print(f"{'测试域':<14}{'学到的星数':<12}{'同域基线':<12}{'跨域准确率':<14}{'相对下降':<12}")
    print("-" * 104)
    rows = []
    for seed in SEEDS[:1]:     # 先 1 seed 看趋势，避免太慢
        Ftr = f_hog(Xtr)
        store = learn(Ftr, ytr, labels_all, N_TRAIN, seed, THR)
        # 取测试样本
        rng = np.random.default_rng(seed + 999)
        for dom_name, dom_fn in DOMAINS.items():
            Xd = dom_fn(Xte)
            Fd = f_hog(Xd)
            accs = {}
            for lab in sorted(labels_all):
                idx = np.array([i for i, v in enumerate(yte) if str(v) == lab])
                rng.shuffle(idx)
                idx = idx[:test_per_class]
                accs[lab] = store.acc(Fd[idx], yte[idx])
            macro = float(np.mean(list(accs.values())))
            rows.append({"domain": dom_name, "macro": round(macro, 4),
                         "per_class": {k: round(v, 4) for k, v in accs.items()},
                         "n_stars": store.n})
            print(f"{dom_name:<14}{store.n:<12d}{'':<12}{macro:<14.4f}")
    print("-" * 104)

    base = next(r["macro"] for r in rows if r["domain"] == "original")
    print(f"\n原始域基线 = {base:.4f}")

    print("\n" + "=" * 104)
    print("跨域对比（相对原始域的下降，百分点）")
    print("=" * 104)
    print(f"{'域':<14}{'macro':<10}{'绝对差':<12}{'相对差':<12}{'判读':<20}")
    print("-" * 104)
    for r in rows:
        if r["domain"] == "original":
            continue
        d = r["macro"] - base
        print(f"{r['domain']:<14}{r['macro']:<10.4f}{d:<+12.4f}{'':<12}"
              f"{'轻微下降' if d > -0.03 else ('中等下降' if d > -0.10 else '严重下降'):<20}")
    print("-" * 104)

    print(f"\n耗时 {time.time()-t0:.0f}s")

    out = {"experiment": "E-25", "feature": "hog128", "threshold": THR,
           "n_train": N_TRAIN, "test_per_class": test_per_class,
           "note": "训练域 original，测试域为合成变换；训练/测试族不相交",
           "rows": rows, "baseline_original": base,
           "elapsed_sec": round(time.time() - t0, 1)}
    with open(os.path.join(RESULTS, "e25_domain_shift.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e25_domain_shift.json')}")


if __name__ == "__main__":
    main()
