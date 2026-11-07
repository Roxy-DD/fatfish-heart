"""
E-17 星数控制：zscore-dim 的增益来自表征，还是星数？

E-16 发现 zscore-dim: macro 0.8900 → 0.9000，但星数 1428 → 2181（+52.7%）。
E-11 已证明"星数增多"本身有 +5.34pp 效应，所以必须控制。

设计：在 4 个格子里匹配星数
  raw  × 自然阈值 0.705                → 1428
  raw  × 降阈值（强制多产星）            → ~2180
  zsc  × 自然阈值 0.705                → 2181
  zsc  × 升阈值（强制少产星）            → ~1428

判定：
  若 zsc@2181 > raw@2180  → 表征有独立贡献
  若 zsc@2181 ≈ raw@2180  → 增益主要来自星数
  若 zsc@1428 ≥ raw@1428  → 表征在同等星数下也不劣

附带：zsc 空间的阈值重新标定（E-0 方法）。
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


def l2(v):
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    n[n == 0] = 1.0
    return v / n


def split(F, y, labels, ntr, nte, seed):
    rng = np.random.default_rng(seed)
    idx = np.array([i for i, lab in enumerate(y) if lab in labels])
    rng.shuffle(idx)
    return F[idx[:ntr]], y[idx[:ntr]], F[idx[ntr:ntr + nte]], y[idx[ntr:ntr + nte]]


class Idx:
    def __init__(self, V, L):
        self.M = np.ascontiguousarray(V, np.float32)
        self.labels = [str(x) for x in L]
        self.uniq = sorted(set(self.labels))
        lid = np.array([self.uniq.index(l) for l in self.labels], np.int32)
        self.masks = [np.where(lid == i)[0] for i in range(len(self.uniq))]

    def predict(self, X, k=3):
        S = X @ self.M.T
        nc = len(self.uniq)
        sc = np.empty((len(X), nc), np.float32)
        for i, idx in enumerate(self.masks):
            v = S[:, idx]
            kk = min(k, v.shape[1])
            sc[:, i] = np.partition(v, -kk, axis=1)[:, -kk:].mean(axis=1)
        return np.array(self.uniq, dtype=object)[np.argmax(sc, axis=1)]

    def accuracy(self, X, y, k=3):
        return float(np.mean(self.predict(X, k) == np.array([str(v) for v in y], dtype=object)))


def build_with(Ftr, ytr, seed, transform, threshold):
    b = CorpusCallosum(threshold=threshold, seed=seed)
    chunks, tests = {}, {}
    for name in "ABC":
        Xtr, ytr_t, Xte, yte_t = split(Ftr, ytr, TASKS[name], TR, TE, seed)
        chunks[name] = (transform(Xtr), ytr_t)
        tests[name] = (Xte, yte_t)
    for name in "ABC":
        Xtr_t, ytr_t = chunks[name]
        for xi, yi in zip(Xtr_t, ytr_t):
            b.memorize(xi, yi)
    stars = b.all_stars()
    return Idx(np.array([s.vector for s in stars], np.float32), [s.label for s in stars]), tests


def main():
    Ftr, ytr, _ = get_features(True, "main", limit=20000, input_size=96)
    Fte, yte, _ = get_features(False, "main", limit=8000, input_size=96)
    mu = Ftr[:9000].mean(axis=0)
    sd = Ftr[:9000].std(axis=0) + 1e-6

    raw = lambda F: F
    zsc = lambda F: l2((F - mu) / sd)

    seeds = [0, 1, 2, 3, 4]
    t0 = time.time()

    # ---- 先粗扫阈值，使星数可控 ----
    print("=" * 100)
    print("E-17 第一步：阈值粗扫（目标：找到让 zsc 产生 ~1428 星的阈值）")
    print("=" * 100)
    probe = {}
    for tname, fn in [("raw", raw), ("zsc", zsc)]:
        for T in [0.60, 0.65, 0.705, 0.75, 0.80, 0.85]:
            ix, _ = build_with(Ftr, ytr, 0, fn, T)
            n = ix.M.shape[0]
            probe[f"{tname}_{T}"] = n
            print(f"  {tname:<5} T={T:<6} stars={n}")
    print()

    # 选阈值：raw 自然 0.705 -> 1428；raw 降阈值 -> ~2180；zsc 自然 -> 2181；zsc 升阈值 -> ~1428

    def find_T(target, fn, lo=0.55, hi=0.95, iters=14):
        """二分找使星数最接近 target 的阈值（seed=0）"""
        best_T, best_d = 0.705, 1e18
        for T in np.linspace(lo, hi, iters):
            ix, _ = build_with(Ftr, ytr, 0, fn, float(T))
            d = abs(ix.M.shape[0] - target)
            if d < best_d:
                best_d, best_T = d, float(T)
        return best_T, best_d

    print("=" * 100)
    print("E-17 第二步：二分搜索匹配星数的阈值")
    print("=" * 100)
    T_raw_high, d1 = find_T(1428, raw)      # 自然，应接近 0.705
    T_raw_low, d2 = find_T(2181, raw)       # 降阈值多产星
    T_zsc_nat, d3 = find_T(2181, zsc)       # 自然
    T_zsc_low, d4 = find_T(1428, zsc)       # 升阈值少产星
    print(f"  raw → 1428 星: T={T_raw_high:.3f} (偏差 {d1:.0f})")
    print(f"  raw → 2181 星: T={T_raw_low:.3f} (偏差 {d2:.0f})")
    print(f"  zsc → 2181 星: T={T_zsc_nat:.3f} (偏差 {d3:.0f})")
    print(f"  zsc → 1428 星: T={T_zsc_low:.3f} (偏差 {d4:.0f})")

    # ---- 主实验 ----
    print("\n" + "=" * 100)
    print("E-17 主实验：2×2 星数匹配矩阵（5 seeds）")
    print("=" * 100)
    cells = [
        ("raw@1428", raw, T_raw_high, 1428),
        ("raw@2181", raw, T_raw_low, 2181),
        ("zsc@1428", zsc, T_zsc_low, 1428),
        ("zsc@2181", zsc, T_zsc_nat, 2181),
    ]
    records = []
    for cname, fn, T, target in cells:
        for seed in seeds:
            ix, tests = build_with(Ftr, ytr, seed, fn, T)
            r = {"cell": cname, "seed": seed, "T": T, "stars": int(ix.M.shape[0])}
            for t in "ABC":
                Xte, yte_t = tests[t]
                r[t] = ix.accuracy(fn(Xte), yte_t)
            r["macro"] = (r["A"] + r["B"] + r["C"]) / 3
            records.append(r)
        sub = [r for r in records if r["cell"] == cname]
        print(f"  {cname:<10} T={T:.3f}  stars={np.mean([r['stars'] for r in sub]):.0f}  "
              f"macro={np.mean([r['macro'] for r in sub]):.4f}")

    print(f"\n耗时 {time.time()-t0:.0f}s")

    print("\n" + "=" * 100)
    print("汇总（均值 ± std, n=5）")
    print("=" * 100)
    print(f"{'格子':<12}{'星数':<8}{'A':<16}{'B':<16}{'C':<16}{'macro':<16}")
    print("-" * 100)
    summary = {}
    for cname, fn, T, target in cells:
        rs = [r for r in records if r["cell"] == cname]
        A = np.array([r["A"] for r in rs]); B = np.array([r["B"] for r in rs])
        C = np.array([r["C"] for r in rs]); M = np.array([r["macro"] for r in rs])
        stars = int(np.mean([r["stars"] for r in rs]))
        summary[cname] = {"stars": stars, "A": float(A.mean()), "A_std": float(A.std()),
                          "A_min": float(A.min()), "B": float(B.mean()), "C": float(C.mean()),
                          "macro": float(M.mean()), "macro_std": float(M.std()),
                          "macro_min": float(M.min()), "T": T}
        print(f"{cname:<12}{stars:<8d}{A.mean():.4f}±{A.std():.3f}  {B.mean():.4f}±{B.std():.3f}  "
              f"{C.mean():.4f}±{C.std():.3f}  {M.mean():.4f}±{M.std():.3f}")
    print("-" * 100)

    # ---- 关键判定：同星数下的表征贡献 ----
    print("\n" + "=" * 100)
    print("判定：同星数下，表征是否有独立贡献")
    print("=" * 100)
    d2181 = summary["zsc@2181"]["macro"] - summary["raw@2181"]["macro"]
    d1428 = summary["zsc@1428"]["macro"] - summary["raw@1428"]["macro"]
    print(f"  同 ~2181 星: zsc {summary['zsc@2181']['macro']:.4f} vs raw "
          f"{summary['raw@2181']['macro']:.4f}  → 表征贡献 {d2181:+.4f}")
    print(f"  同 ~1428 星: zsc {summary['zsc@1428']['macro']:.4f} vs raw "
          f"{summary['raw@1428']['macro']:.4f}  → 表征贡献 {d1428:+.4f}")
    ratio = summary["zsc@1428"]["stars"] / max(summary["raw@1428"]["stars"], 1)
    print(f"  星数匹配程度: zsc@1428/raw@1428 = {ratio:.3f}")
    print()
    if d2181 > 0.005 and d1428 > 0.005:
        verdict = "表征有独立贡献（两个星数档位都为正）"
    elif abs(d2181) <= 0.005 and abs(d1428) <= 0.005:
        verdict = "表征无独立贡献，之前的增益来自星数"
    else:
        verdict = "结论不一致，需进一步控制（星数匹配不够精确）"
    print(f"  ★ 判定：{verdict}")

    print("\n判据（macro>0.90 且 macro_min>0.88）:")
    for k, s in sorted(summary.items(), key=lambda kv: -kv[1]["macro"]):
        print(f"  {k:<12} macro={s['macro']:.4f} {'✓' if s['macro']>0.90 else '✗'}  "
              f"min={s['macro_min']:.4f} {'✓' if s['macro_min']>0.88 else '✗'}  "
              f"A_min={s['A_min']:.4f}")

    out = {"experiment": "E-17", "seeds": seeds, "summary": summary,
           "records": records, "threshold_probe": probe,
           "verdict": {"rep_contribution_at_2181": round(d2181, 4),
                       "rep_contribution_at_1428": round(d1428, 4),
                       "conclusion": verdict}}
    with open(os.path.join(RESULTS, "e17_star_control.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e17_star_control.json')}")


if __name__ == "__main__":
    main()
