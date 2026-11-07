"""
E-14 回收跨任务干扰（6.89pp）

诊断（E-13）：类内上限 0.9520（oracle/within），实际 0.8831，缺口 6.89pp 全在跨任务标签竞争。

流程规则（受 E-8/E-12 两次量级错误教训）：
  任何加减项，**先打印其实际取值范围**，再定系数。本脚本第一步即做量级审计。

方法：
  base      topk3（基线 0.8831）
  zc        类内标准化：(score - mean_k) / std_k
  zc2       类内标准化 + topk 聚合
  margin    若 top1-top2 < delta，回退到 topk 共识
  quota_s   加常数配额：score + q * (1/n_k)^0.5    （温和版，非 log）
  score_std 全类统一标准化（把所有类得分拉到同一尺度再取 argmax）
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


def split(F, y, labels, ntr, nte, seed):
    rng = np.random.default_rng(seed)
    idx = np.array([i for i, lab in enumerate(y) if lab in labels])
    rng.shuffle(idx)
    return F[idx[:ntr]], y[idx[:ntr]], F[idx[ntr:ntr + nte]], y[idx[ntr:ntr + nte]]


def build(Ftr, ytr, order, seed, threshold=0.705):
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


class GroupIndex:
    """按 label 分组的星矩阵，返回完整 [m, n_class] 打分矩阵供各种后处理。"""

    def __init__(self, vectors, labels):
        self.M = np.ascontiguousarray(vectors, dtype=np.float32)
        self.labels = [str(x) for x in labels]
        self.uniq = sorted(set(self.labels))
        lid = np.array([self.uniq.index(l) for l in self.labels], dtype=np.int32)
        self.masks = [np.where(lid == i)[0] for i in range(len(self.uniq))]
        self.sizes = np.array([len(m) for m in self.masks], dtype=np.float32)

    def raw(self, X, topk=3):
        """返回 (topk_scores, max_scores, intra_mean, intra_std) 各 [m, n_class]"""
        S = X @ self.M.T
        m, nc = len(X), len(self.uniq)
        tk = np.empty((m, nc), np.float32)
        mx = np.empty((m, nc), np.float32)
        mu = np.empty((m, nc), np.float32)
        sd = np.empty((m, nc), np.float32)
        for i, idx in enumerate(self.masks):
            v = S[:, idx]
            mx[:, i] = v.max(axis=1)
            mu[:, i] = v.mean(axis=1)
            sd[:, i] = v.std(axis=1) + 1e-6
            k = min(topk, v.shape[1])
            tk[:, i] = np.partition(v, -k, axis=1)[:, -k:].mean(axis=1)
        return tk, mx, mu, sd

    def predict(self, X, method: str, p: dict):
        tk, mx, mu, sd = self.raw(X, topk=p.get("topk", 3))
        if method == "base":
            sc = tk
        elif method == "zc":
            sc = (tk - mu) / sd
        elif method == "zmax":
            sc = (mx - mu) / sd
        elif method == "quota":
            q = p.get("q", 0.0)
            pen = (1.0 / np.sqrt(self.sizes))
            pen = pen / pen.mean()
            sc = tk + q * pen
        elif method == "global_std":
            sc = (tk - tk.mean(axis=1, keepdims=True)) / (tk.std(axis=1, keepdims=True) + 1e-6)
        elif method == "margin":
            sc = tk
        else:
            raise ValueError(method)

        pred_idx = np.argmax(sc, axis=1)

        if method == "margin":
            delta = p.get("delta", 0.0)
            order = np.argsort(-sc, axis=1)
            top1, top2 = order[:, 0], order[:, 1]
            gap = sc[np.arange(len(sc)), top1] - sc[np.arange(len(sc)), top2]
            low = gap < delta
            # 冲突时：回退到 max 与 topk 一致性最高的类（用 tk 在 top1/top2 之间重选）
            for i in np.where(low)[0]:
                a, b = top1[i], top2[i]
                pred_idx[i] = a if tk[i, a] >= tk[i, b] else b

        return np.array(self.uniq, dtype=object)[pred_idx], sc


def acc(idx: GroupIndex, X, y, method, p):
    if len(X) == 0:
        return 0.0
    pred, _ = idx.predict(X, method, p)
    return float(np.mean(pred == y))


def main():
    Ftr, ytr, _ = get_features(True, "main", limit=20000, input_size=96)
    Fte, yte, _ = get_features(False, "main", limit=8000, input_size=96)
    seeds = [0, 1, 2, 3, 4]
    t0 = time.time()

    # ---------- 0. 量级审计 ----------
    print("=" * 100)
    print("E-14 第一步：量级审计（吸取 E-8 density / E-12 evt_beta 的教训）")
    print("=" * 100)
    b, tests = build(Ftr, ytr, ["A", "B", "C"], 0)
    idx = GroupIndex(np.array([s.vector for s in b.all_stars()], dtype=np.float32),
                     [s.label for s in b.all_stars()])
    X = tests["A"][0][:100]
    tk, mx, mu, sd = idx.raw(X, topk=3)
    print(f"  星数={idx.M.shape[0]}  类数={len(idx.uniq)}  类内星数范围=[{idx.sizes.min():.0f}, {idx.sizes.max():.0f}]")
    print(f"  topk3 得分范围      : [{tk.min():.4f}, {tk.max():.4f}]  std={tk.std():.4f}")
    print(f"  类内均值 mu 范围    : [{mu.min():.4f}, {mu.max():.4f}]")
    print(f"  类内标准差 sd 范围  : [{sd.min():.4f}, {sd.max():.4f}]")
    print(f"  (topk-mu)/sd 范围   : [{((tk-mu)/sd).min():.3f}, {((tk-mu)/sd).max():.3f}]  "
          f"std={((tk-mu)/sd).std():.3f}")
    q_pen = 1.0 / np.sqrt(idx.sizes)
    q_pen = q_pen / q_pen.mean()
    print(f"  quota 项 (1/sqrt(n)) 归一后范围: [{q_pen.min():.4f}, {q_pen.max():.4f}]  "
          f"跨度={q_pen.max()-q_pen.min():.4f}")
    gap = np.sort(tk, axis=1)[:, -1] - np.sort(tk, axis=1)[:, -2]
    print(f"  top1-top2 差距范围  : [{gap.min():.4f}, {gap.max():.4f}]  中位数={np.median(gap):.4f}")
    print()
    print("  ★ 量级判断：")
    print(f"    topk3 得分跨度 {tk.max()-tk.min():.4f}")
    print(f"    quota 项跨度 {q_pen.max()-q_pen.min():.4f} → q 取 0.01~0.05 才同量级")
    print(f"    (topk-mu)/sd 跨度 {((tk-mu)/sd).max()-((tk-mu)/sd).min():.2f} → 会放大差异，需谨慎")
    print(f"    margin delta 应取 0.01~0.05（top1-top2 中位数 {np.median(gap):.4f}）")

    # ---------- 1. 主实验 ----------
    print("\n" + "=" * 100)
    print("E-14 主实验：回收跨任务干扰（5 seeds，A→B→C）")
    print("=" * 100)

    configs = [
        ("base", "base", {"topk": 3}),
        ("margin.01", "margin", {"topk": 3, "delta": 0.01}),
        ("margin.03", "margin", {"topk": 3, "delta": 0.03}),
        ("margin.05", "margin", {"topk": 3, "delta": 0.05}),
        ("quota.01", "quota", {"topk": 3, "q": 0.01}),
        ("quota.03", "quota", {"topk": 3, "q": 0.03}),
        ("quota.05", "quota", {"topk": 3, "q": 0.05}),
        ("zc", "zc", {"topk": 3}),
        ("zmax", "zmax", {"topk": 3}),
        ("gstd", "global_std", {"topk": 3}),
    ]

    records = []
    for seed in seeds:
        b, tests = build(Ftr, ytr, ["A", "B", "C"], seed)
        stars = b.all_stars()
        idx = GroupIndex(np.array([s.vector for s in stars], dtype=np.float32),
                         [s.label for s in stars])
        for name, method, p in configs:
            r = {"seed": seed, "cfg": name}
            for t in ["A", "B", "C"]:
                r[t] = acc(idx, *tests[t], method, p)
            r["macro"] = (r["A"] + r["B"] + r["C"]) / 3
            records.append(r)
        print(f"  seed={seed} done (stars={idx.M.shape[0]})")

    print(f"\n耗时 {time.time()-t0:.0f}s")

    print("\n" + "=" * 100)
    print("汇总（均值 ± std, n=5）")
    print("=" * 100)
    print(f"{'配置':<12}{'A':<16}{'B':<16}{'C':<16}{'macro':<16}{'回收率':<10}")
    print("-" * 100)
    base_macro = np.mean([r["macro"] for r in records if r["cfg"] == "base"])
    oracle = 0.9520
    summary = {}
    for name, _, _ in configs:
        rs = [r for r in records if r["cfg"] == name]
        A = np.array([r["A"] for r in rs]); B = np.array([r["B"] for r in rs])
        C = np.array([r["C"] for r in rs]); M = np.array([r["macro"] for r in rs])
        rec = (M.mean() - base_macro) / (oracle - base_macro) * 100
        summary[name] = {"A": float(A.mean()), "A_std": float(A.std()),
                         "B": float(B.mean()), "C": float(C.mean()),
                         "macro": float(M.mean()), "macro_std": float(M.std()),
                         "A_min": float(A.min()), "recovery_pct": float(rec)}
        print(f"{name:<12}{A.mean():.4f}±{A.std():.3f}  {B.mean():.4f}±{B.std():.3f}  "
              f"{C.mean():.4f}±{C.std():.3f}  {M.mean():.4f}±{M.std():.3f}  {rec:+6.1f}%")

    print("-" * 100)
    best = max(summary.items(), key=lambda kv: kv[1]["macro"])
    print(f"\n基线 macro={base_macro:.4f}  oracle上限={oracle:.4f}  缺口={oracle-base_macro:.4f}")
    print(f"★ 最优 {best[0]}: macro={best[1]['macro']:.4f} "
          f"({best[1]['macro']-base_macro:+.4f}, 回收 {best[1]['recovery_pct']:+.1f}%)")

    print("\n判据检验（macro>0.90 且 A_min>0.88）:")
    for name, s in sorted(summary.items(), key=lambda kv: -kv[1]["macro"]):
        ok1 = s["macro"] > 0.90
        ok2 = s["A_min"] > 0.88
        print(f"  {name:<12} macro={s['macro']:.4f} ({'✓' if ok1 else '✗'})  "
              f"A_min={s['A_min']:.4f} ({'✓' if ok2 else '✗'})")

    out = {"experiment": "E-14", "seeds": seeds,
           "baseline_macro": round(float(base_macro), 4), "oracle_ceiling": oracle,
           "summary": summary, "records": records,
           "best": {"cfg": best[0], **best[1]}}
    with open(os.path.join(RESULTS, "e14_interference_recovery.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e14_interference_recovery.json')}")


if __name__ == "__main__":
    main()
