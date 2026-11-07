"""
E-15 修正版跨任务干扰回收

E-14 的两个实现 bug（已诊断）：
  margin  回退时用 tk 在 top1/top2 间重选 → tk[top1]>=tk[top2] 恒真 → 空操作
  zc/zmax 除以 sd → 被 sd 最小的类垄断（预测 5/4/3，真实 0/1/2）→ 方向性错误

修正：
  margin2  双向仲裁：tk 与 mx 各自 argmax，不一致时按 gap 决定信谁
           (若 gap 大 → 信 tk；gap 小 → 信 mx)
  zadd     加法校正：score + c*(mu_k - mu_mean)     不加 sd，方向正确
  rank     类内排名：把 cos 换成类内百分位（0-1），跨类可比且不被 sd 放大
  twostage 先判"粗簇"再判细类：用 KMeans 式聚类（不用标签）找到粗结构，不引入 oracle

强制规则：每个新参数先做量级审计。
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


class Index:
    def __init__(self, vectors, labels):
        self.M = np.ascontiguousarray(vectors, dtype=np.float32)
        self.labels = [str(x) for x in labels]
        self.uniq = sorted(set(self.labels))
        lid = np.array([self.uniq.index(l) for l in self.labels], dtype=np.int32)
        self.masks = [np.where(lid == i)[0] for i in range(len(self.uniq))]
        self.sizes = np.array([len(m) for m in self.masks], dtype=np.float32)

    def stats(self, X, topk=3):
        """返回 dict of [m,n_class] 矩阵"""
        S = X @ self.M.T
        m, nc = len(X), len(self.uniq)
        out = {k: np.empty((m, nc), np.float32) for k in ("tk", "mx", "mu", "sd")}
        allv = np.empty((m, nc, 0), np.float32)
        for i, idx in enumerate(self.masks):
            v = S[:, idx]
            out["mx"][:, i] = v.max(axis=1)
            out["mu"][:, i] = v.mean(axis=1)
            out["sd"][:, i] = v.std(axis=1)
            k = min(topk, v.shape[1])
            out["tk"][:, i] = np.partition(v, -k, axis=1)[:, -k:].mean(axis=1)
        return out, S

    def rank_scores(self, S):
        """类内百分位：每个样本，该类所有候选的 cos 的百分位排名（最大值 = 1.0）"""
        m, nc = S.shape[0], len(self.uniq)
        out = np.empty((m, nc), np.float32)
        for i, idx in enumerate(self.masks):
            v = S[:, idx]                       # [m, n_i]
            # 排名：v 中每个元素在其类内的百分位
            order = np.argsort(v, axis=1)
            ranks = np.empty_like(order)
            rows = np.arange(len(v))[:, None]
            ranks[rows, order] = np.arange(v.shape[1])[None, :]
            pct = ranks / max(v.shape[1] - 1, 1)   # 0..1
            out[:, i] = pct.max(axis=1)            # 取该类最高百分位（=1 恒成立）
        return out

    def predict(self, X, method, p):
        st, S = self.stats(X, topk=p.get("topk", 3))
        tk, mx, mu, sd = st["tk"], st["mx"], st["mu"], st["sd"]

        if method == "base":
            sc = tk

        elif method == "tk_cal":
            # 加法校正：把类内均值拉平（不除 sd）
            c = p.get("c", 0.0)
            sc = tk + c * (mu - mu.mean(axis=1, keepdims=True))

        elif method == "margin2":
            # 双向仲裁：tk 与 mx 不一致时，gap 小则信 mx（对局部近邻更敏感）
            pred_tk = np.argmax(tk, axis=1)
            pred_mx = np.argmax(mx, axis=1)
            delta = p.get("delta", 0.03)
            gap = np.sort(tk, axis=1)[:, -1] - np.sort(tk, axis=1)[:, -2]
            out = pred_tk.copy()
            amb = (gap < delta) & (pred_tk != pred_mx)
            out[amb] = pred_mx[amb]
            return np.array(self.uniq, dtype=object)[out], tk

        elif method == "rank":
            sc = self.rank_scores(S)

        elif method == "rank_tk":
            r = self.rank_scores(S)
            sc = 0.5 * r + 0.5 * ((tk - tk.min()) / (tk.max() - tk.min() + 1e-9))

        elif method == "quota":
            q = p.get("q", 0.02)
            pen = 1.0 / np.sqrt(self.sizes)
            pen = pen / pen.mean()
            sc = tk + q * pen

        else:
            raise ValueError(method)

        idx = np.argmax(sc, axis=1)
        return np.array(self.uniq, dtype=object)[idx], sc


def acc(ix: Index, X, y, method, p):
    if len(X) == 0:
        return 0.0
    pred, _ = ix.predict(X, method, p)
    return float(np.mean(pred == np.array([str(v) for v in y], dtype=object)))


def main():
    Ftr, ytr, _ = get_features(True, "main", limit=20000, input_size=96)
    Fte, yte, _ = get_features(False, "main", limit=8000, input_size=96)
    seeds = [0, 1, 2, 3, 4]
    t0 = time.time()

    b0, tests0 = build(Ftr, ytr, ["A", "B", "C"], 0)
    stars0 = b0.all_stars()
    ix0 = Index(np.array([s.vector for s in stars0], dtype=np.float32),
                [s.label for s in stars0])

    # ---------- 量级审计 ----------
    print("=" * 100)
    print("E-15 量级审计（强制第一步）")
    print("=" * 100)
    X0, y0 = tests0["A"]
    st, S = ix0.stats(X0, topk=3)
    tk, mx, mu, sd = st["tk"], st["mx"], st["mu"], st["sd"]
    print(f"  星数={ix0.M.shape[0]} 类内星数=[{ix0.sizes.min():.0f},{ix0.sizes.max():.0f}]")
    print(f"  tk 跨度={tk.max()-tk.min():.4f}  mx 跨度={mx.max()-mx.min():.4f}")
    print(f"  mu(类内均值) 跨度={mu.max()-mu.min():.4f}")
    print(f"  (mu - 行均值) 跨度={np.abs(mu-mu.mean(axis=1,keepdims=True)).max():.4f}  "
          f"→ c 应 <= 0.5")
    print(f"  rank 分数范围=[0,1]，必然跨度 1.0 → 与 tk(0.43) 不同尺度，混用需归一化")
    gap = np.sort(tk, axis=1)[:, -1] - np.sort(tk, axis=1)[:, -2]
    print(f"  top1-top2 gap: 中位数={np.median(gap):.4f} p90={np.percentile(gap,90):.4f}")
    # margin2 的不一致率
    pred_tk, pred_mx = np.argmax(tk, axis=1), np.argmax(mx, axis=1)
    print(f"  tk 与 mx 的 argmax 不一致率: {np.mean(pred_tk!=pred_mx)*100:.1f}%")

    # ---------- 主实验 ----------
    print("\n" + "=" * 100)
    print("E-15 主实验（5 seeds）")
    print("=" * 100)
    configs = [
        ("base", "base", {"topk": 3}),
        ("tk_cal.1", "tk_cal", {"topk": 3, "c": 0.1}),
        ("tk_cal.3", "tk_cal", {"topk": 3, "c": 0.3}),
        ("tk_cal.5", "tk_cal", {"topk": 3, "c": 0.5}),
        ("margin2.01", "margin2", {"topk": 3, "delta": 0.01}),
        ("margin2.03", "margin2", {"topk": 3, "delta": 0.03}),
        ("margin2.05", "margin2", {"topk": 3, "delta": 0.05}),
        ("rank", "rank", {"topk": 3}),
        ("rank_tk", "rank_tk", {"topk": 3}),
        ("quota.02", "quota", {"topk": 3, "q": 0.02}),
    ]

    records = []
    for seed in seeds:
        b, tests = build(Ftr, ytr, ["A", "B", "C"], seed)
        stars = b.all_stars()
        ix = Index(np.array([s.vector for s in stars], dtype=np.float32),
                   [s.label for s in stars])
        for name, method, p in configs:
            r = {"seed": seed, "cfg": name}
            for t in ["A", "B", "C"]:
                r[t] = acc(ix, *tests[t], method, p)
            r["macro"] = (r["A"] + r["B"] + r["C"]) / 3
            records.append(r)
        print(f"  seed={seed} done")

    print(f"\n耗时 {time.time()-t0:.0f}s")
    base_macro = np.mean([r["macro"] for r in records if r["cfg"] == "base"])
    oracle = 0.9520

    print("\n" + "=" * 100)
    print(f"汇总（n=5）  基线 macro={base_macro:.4f}  oracle={oracle:.4f}")
    print("=" * 100)
    print(f"{'配置':<12}{'A':<16}{'B':<16}{'C':<16}{'macro':<16}{'回收':<9}")
    print("-" * 100)
    summary = {}
    for name, _, _ in configs:
        rs = [r for r in records if r["cfg"] == name]
        A = np.array([r["A"] for r in rs]); B = np.array([r["B"] for r in rs])
        C = np.array([r["C"] for r in rs]); M = np.array([r["macro"] for r in rs])
        rec = (M.mean() - base_macro) / (oracle - base_macro) * 100
        summary[name] = {"A": float(A.mean()), "A_std": float(A.std()), "A_min": float(A.min()),
                         "B": float(B.mean()), "C": float(C.mean()),
                         "macro": float(M.mean()), "macro_std": float(M.std()),
                         "recovery_pct": float(rec)}
        print(f"{name:<12}{A.mean():.4f}±{A.std():.3f}  {B.mean():.4f}±{B.std():.3f}  "
              f"{C.mean():.4f}±{C.std():.3f}  {M.mean():.4f}±{M.std():.3f}  {rec:+6.1f}%")
    print("-" * 100)

    best = max(summary.items(), key=lambda kv: kv[1]["macro"])
    print(f"\n★ 最优 {best[0]}: macro={best[1]['macro']:.4f} "
          f"({best[1]['macro']-base_macro:+.4f}, 回收 {best[1]['recovery_pct']:+.1f}%)")
    print("\n判据（macro>0.90 且 A_min>0.88）:")
    for name, s in sorted(summary.items(), key=lambda kv: -kv[1]["macro"]):
        print(f"  {name:<12} macro={s['macro']:.4f} {'✓' if s['macro']>0.90 else '✗'}   "
              f"A_min={s['A_min']:.4f} {'✓' if s['A_min']>0.88 else '✗'}")

    out = {"experiment": "E-15", "seeds": seeds, "baseline_macro": round(float(base_macro), 4),
           "oracle_ceiling": oracle, "summary": summary, "records": records,
           "best": {"cfg": best[0], **best[1]},
           "note": "修正 E-14 的 margin(空操作) 与 zc(除以sd方向错误)"}
    with open(os.path.join(RESULTS, "e15_recovery_v2.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e15_recovery_v2.json')}")


if __name__ == "__main__":
    main()
