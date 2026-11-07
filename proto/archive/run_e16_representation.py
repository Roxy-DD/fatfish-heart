"""
E-16 表征层检验

假说（由 E-14/E-15 两轮失败推出）：6.89pp 跨任务干扰来自 1280 维特征的类间重叠，
不是打分层问题。

四问：
  ① 混淆矩阵：错误是否集中在固定类对？
  ② 特征上限：同一特征上，用非 Cosmos 的分类器能做到多少？
  ③ 换表征：像素 PCA / 特征拼接 / 归一化变体，base 能否提升？
  ④ 原型精选：每类只用 medoid，能否接近 0.95？

判据：若 ② 显示的"特征上限"≈ 0.95 且 ③ 换特征后 base 提升 → 确认表征瓶颈。
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
from retina import get_features

RESULTS = os.path.join(_HERE, "results")
TASKS = {"A": {"0", "1", "2"}, "B": {"3", "4", "5"}, "C": {"6", "7", "8", "9"}}
TR, TE = 900, 300
ALL10 = set("0123456789")


def split(F, y, labels, ntr, nte, seed, cap=None):
    rng = np.random.default_rng(seed)
    idx = np.array([i for i, lab in enumerate(y) if lab in labels])
    rng.shuffle(idx)
    if cap:
        idx = idx[:cap]
    return F[idx[:ntr]], y[idx[:ntr]], F[idx[ntr:ntr + nte]], y[idx[ntr:ntr + nte]]


def build(Ftr, ytr, seed, threshold=0.705, order=("A", "B", "C")):
    b = CorpusCallosum(threshold=threshold, seed=seed)
    chunks, tests = {}, {}
    for name in order:
        Xtr, ytr_t, Xte, yte_t = split(Ftr, ytr, TASKS[name], TR, TE, seed)
        chunks[name] = (Xtr, ytr_t)
        tests[name] = (Xte, yte_t)
    for name in order:
        Xtr, ytr_t = chunks[name]
        for xi, yi in zip(Xtr, ytr_t):
            b.memorize(xi, yi)
    return b, tests


class Idx:
    def __init__(self, V, L):
        self.M = np.ascontiguousarray(V, np.float32)
        self.labels = [str(x) for x in L]
        self.uniq = sorted(set(self.labels))
        lid = np.array([self.uniq.index(l) for l in self.labels], np.int32)
        self.masks = [np.where(lid == i)[0] for i in range(len(self.uniq))]
        self.sizes = np.array([len(m) for m in self.masks], np.float32)

    def topk_scores(self, X, k=3):
        S = X @ self.M.T
        out = np.empty((len(X), len(self.uniq)), np.float32)
        for i, idx in enumerate(self.masks):
            v = S[:, idx]
            kk = min(k, v.shape[1])
            out[:, i] = np.partition(v, -kk, axis=1)[:, -kk:].mean(axis=1)
        return out

    def predict(self, X, k=3):
        sc = self.topk_scores(X, k)
        return np.array(self.uniq, dtype=object)[np.argmax(sc, axis=1)]

    def accuracy(self, X, y, k=3):
        y = np.array([str(v) for v in y], dtype=object)
        return float(np.mean(self.predict(X, k) == y))


def confusion_10x10(idx, X, y):
    y = np.array([str(v) for v in y], dtype=object)
    pred = idx.predict(X)
    labs = [str(i) for i in range(10)]
    cm = {a: {b: 0 for b in labs} for a in labs}
    for t, p in zip(y, pred):
        cm[t][p] += 1
    return cm


def main():
    Ftr, ytr, _ = get_features(True, "main", limit=20000, input_size=96)
    Fte, yte, _ = get_features(False, "main", limit=8000, input_size=96)
    t0 = time.time()

    # ================= ① 混淆矩阵 =================
    print("=" * 96)
    print("① 混淆矩阵（seed=0，学完 A→B→C，topk3）")
    print("=" * 96)
    b, tests = build(Ftr, ytr, 0)
    stars = b.all_stars()
    idx = Idx(np.array([s.vector for s in stars], np.float32), [s.label for s in stars])
    Xall = np.vstack([tests[t][0] for t in "ABC"])
    yall = np.concatenate([tests[t][1] for t in "ABC"])
    cm = confusion_10x10(idx, Xall, yall)

    print("       " + "".join(f"{b2:>6s}" for b2 in [str(i) for i in range(10)]))
    for a in [str(i) for i in range(10)]:
        row = "".join(f"{cm[a][b2]:6d}" for b2 in [str(i) for i in range(10)])
        tot = sum(cm[a].values())
        ok = cm[a][a] / tot if tot else 0
        print(f"  真{a}: {row}   acc={ok:.3f}")

    # 错误集中度
    errs = []
    for a in [str(i) for i in range(10)]:
        for b2 in [str(i) for i in range(10)]:
            if a != b2 and cm[a][b2] > 0:
                errs.append((cm[a][b2], a, b2))
    errs.sort(reverse=True)
    total_err = sum(e[0] for e in errs)
    print(f"\n  总错误 {total_err} 条")
    print("  前 10 大错误类对:")
    top10_share = 0
    for n, a, b2 in errs[:10]:
        print(f"    {a}→{b2}: {n} 条 ({n/total_err*100:.1f}%)")
        top10_share += n
    print(f"  前 10 类对占总错误 {top10_share/total_err*100:.1f}%")
    print(f"  错误类对数 {len(errs)}（若少量类对占多数 → 支持表征重叠假说）")

    # ================= ② 特征上限 =================
    print("\n" + "=" * 96)
    print("② 同一特征上的「非 Cosmos」分类器上限（10 类，每类 900 训练 / 1000 测试）")
    print("=" * 96)
    from sklearn.linear_model import LogisticRegression
    from sklearn.neighbors import KNeighborsClassifier
    from sklearn.discriminant_analysis import LinearDiscriminantAnalysis
    from sklearn.cluster import KMeans

    Xtr, ytr_t, Xte, yte_t = split(Ftr, ytr, ALL10, 900, 1000, 0, cap=9000)
    ytr_s = np.array([str(v) for v in ytr_t])
    yte_s = np.array([str(v) for v in yte_t])
    print(f"  训练 {Xtr.shape}  测试 {Xte.shape}")

    results = {}
    for name, clf in [
        ("LogReg", LogisticRegression(max_iter=1000, C=1.0, n_jobs=1)),
        ("kNN-5", KNeighborsClassifier(n_neighbors=5, metric="cosine", n_jobs=1)),
        ("kNN-1", KNeighborsClassifier(n_neighbors=1, metric="cosine", n_jobs=1)),
        ("LDA", LinearDiscriminantAnalysis()),
        ("Centroid", None),
    ]:
        if clf is None:
            cents = {c: Xtr[ytr_s == c].mean(axis=0) for c in np.unique(ytr_s)}
            C = np.array([cents[c] for c in sorted(cents)])
            C = C / np.linalg.norm(C, axis=1, keepdims=True)
            pred = np.array(sorted(cents), dtype=object)[np.argmax(Xte @ C.T, axis=1)]
        else:
            clf.fit(Xtr, ytr_s)
            pred = clf.predict(Xte)
        a = float(np.mean(pred == yte_s))
        results[name] = a
        print(f"  {name:<10} acc={a:.4f}")

    # Cosmos 在同数据量下的表现（10类联合）
    b_all = CorpusCallosum(threshold=0.705, seed=0)
    for xi, yi in zip(Xtr, ytr_t):
        b_all.memorize(xi, yi)
    stars_all = b_all.all_stars()
    idx_all = Idx(np.array([s.vector for s in stars_all], np.float32),
                  [s.label for s in stars_all])
    cosmos_all = idx_all.accuracy(Xte, yte_t)
    results["Cosmos-10joint"] = cosmos_all
    print(f"  {'Cosmos-10joint':<10} acc={cosmos_all:.4f}  (stars={idx_all.M.shape[0]})")

    best_clf = max(results.items(), key=lambda kv: kv[1])
    print(f"\n  ★ 同特征上的最佳分类器: {best_clf[0]} = {best_clf[1]:.4f}")
    print(f"  ★ 增量 Cosmos base = 0.8831；oracle = 0.9520")
    print(f"  → 若最佳分类器 ≈ 0.95，说明特征本身能到 0.95，Cosmos 表达方式有损")

    # ================= ③ 换表征 =================
    print("\n" + "=" * 96)
    print("③ 换表征：base 能否提升？")
    print("=" * 96)

    def l2(v):
        n = np.linalg.norm(v, axis=-1, keepdims=True)
        n[n == 0] = 1
        return v / n

    # 变体 A：原始 1280 维
    variants = {"raw1280": (lambda F: F)}

    # 变体 B：PCA 到 64/128 维（用测试集外拟合，避免泄漏——这里用训练特征拟合）
    from sklearn.decomposition import PCA
    pca64 = PCA(n_components=64, random_state=0).fit(Ftr[:9000])
    pca256 = PCA(n_components=256, random_state=0).fit(Ftr[:9000])
    variants["pca64"] = lambda F: l2(pca64.transform(F))
    variants["pca256"] = lambda F: l2(pca256.transform(F))

    # 变体 C：中心化后再归一化（去公共方向）
    mu = Ftr[:9000].mean(axis=0)
    variants["center"] = lambda F: l2(F - mu)

    # 变体 D：signed sqrt（压缩大分量，抑制少数维度主导）
    variants["sqrt"] = lambda F: l2(np.sign(F) * np.sqrt(np.abs(F)))

    # 变体 E：rank-gauss 近似（每维标准化到 N(0,1)）
    sd = Ftr[:9000].std(axis=0) + 1e-6
    variants["zscore-dim"] = lambda F: l2((F - mu) / sd)

    rows = []
    print(f"{'表征':<14}{'A':<9}{'B':<9}{'C':<9}{'macro':<9}{'星数':<8}")
    print("-" * 96)
    for vname, fn in variants.items():
        accs = {}
        for t in "ABC":
            # 重跑学习（用变换后的特征重新分配）
            bv = CorpusCallosum(threshold=0.705, seed=0)
            for name in "ABC":
                Xtr, ytr_t, _, _ = split(Ftr, ytr, TASKS[name], TR, TE, 0)
                for xi, yi in zip(fn(Xtr), ytr_t):
                    bv.memorize(xi, yi)
            sv = bv.all_stars()
            iv = Idx(np.array([s.vector for s in sv], np.float32), [s.label for s in sv])
            Xte, yte_t = tests[t]
            accs[t] = iv.accuracy(fn(Xte), yte_t)
            if t == "C":
                nstars = iv.M.shape[0]
        macro = np.mean([accs[t] for t in "ABC"])
        print(f"{vname:<14}{accs['A']:<9.4f}{accs['B']:<9.4f}{accs['C']:<9.4f}"
              f"{macro:<9.4f}{nstars:<8d}")
        rows.append({"variant": vname, **{k: round(v, 4) for k, v in accs.items()},
                     "macro": round(float(macro), 4), "n_stars": int(nstars)})

    print("-" * 96)
    best_v = max(rows, key=lambda r: r["macro"])
    print(f"\n★ 最佳表征 {best_v['variant']}: macro={best_v['macro']:.4f} "
          f"(raw={rows[0]['macro']:.4f}, {best_v['macro']-rows[0]['macro']:+.4f})")

    out = {
        "experiment": "E-16",
        "confusion": cm,
        "top_errors": [{"n": n, "from": a, "to": b2} for n, a, b2 in errs[:15]],
        "top10_error_share": round(top10_share / total_err, 4),
        "n_error_pairs": len(errs),
        "feature_ceiling": {k: round(v, 4) for k, v in results.items()},
        "best_classifier": {"name": best_clf[0], "acc": round(best_clf[1], 4)},
        "representation_variants": rows,
        "best_representation": best_v,
        "baseline_incremental": 0.8831,
        "oracle": 0.9520,
        "elapsed_sec": round(time.time() - t0, 1),
    }
    with open(os.path.join(RESULTS, "e16_representation.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e16_representation.json')}")


if __name__ == "__main__":
    main()

