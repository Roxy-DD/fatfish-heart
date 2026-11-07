"""
E-28 三条现实规律的验证（此前只搜到，没跑）

来源 docs/17：
  §2.1 档案管理：按「类别」定保留期，不逐个打分；有覆盖规则（legal hold）
  §2.3 组织/控制论：轮值反对者、任期制、anti-windup
  §2.4 生物适应：阈值由当前数据滚动决定（慢时间常数）

本实验逐个验证，不预设门槛，只问"什么让它变好/变差"。

实验 A：按类保留期
  现状：所有类别同一处理（同一阈值 0.560）
  改法：每个类有自己的保留期（= 该类允许的最少星数 / 允许的最少 mass）
        + legal hold：被"引用"过的星不剪

实验 B：轮值反对者（修 dominance 吸收态）
  现状：d 到 1.0 后另一路永远 0 分，回不来
  改法：每 N 步强制让弱势方主判一次（任期制）；d 接近 1 时累加变慢（anti-windup）

实验 C：流动阈值
  现状：0.560 是离线算死的
  改法：阈值 = 当前数据同类 cos 的滚动分位数，随数据流动
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
from run_e27_alignment import center_img, align

RESULTS = os.path.join(_HERE, "results")
TASKS = {"A": {"0", "1", "2"}, "B": {"3", "4", "5"}, "C": {"6", "7", "8", "9"}}
TR, TE = 900, 300
SEEDS = [0, 1, 2]
BASE_THR = 0.560


def l2(v):
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    n[n == 0] = 1.0
    return v / n


def split_idx(y, labels, ntr, nte, seed):
    rng = np.random.default_rng(seed)
    labels = {str(l) for l in labels}
    idx = np.array([i for i, lab in enumerate(y) if str(lab) in labels])
    rng.shuffle(idx)
    return idx[:ntr], idx[ntr:ntr + nte]


# ---------------- 实验 A：按类保留期 + legal hold ----------------

class ClassWiseStore:
    """
    每条星有：label / mass / n_retrieval / created_step
    修剪规则（来自档案管理）：
      · 保留期按「类」定，不按星定
      · 某类若星数低于 floor，禁止再剪（保底配额）
      · 被引用过（n_retrieval>0）的星进入 legal hold，不剪
    """

    def __init__(self, dim, threshold=BASE_THR, floor_per_class=8, hold_threshold=1):
        self.dim = dim
        self.threshold = threshold
        self.floor_per_class = floor_per_class
        self.hold_threshold = hold_threshold
        self.M = np.zeros((2048, dim), np.float32)
        self.L = np.empty(2048, dtype=object)
        self.mass = np.zeros(2048, np.int32)
        self.ref = np.zeros(2048, np.int32)     # 被检索命中的次数（legal hold 判据）
        self.n = 0
        self.n_pruned = 0
        self.n_held = 0

    def _grow(self):
        if self.n >= len(self.mass):
            c = len(self.mass) * 2
            self.M = np.vstack([self.M, np.zeros((c - len(self.M), self.dim), np.float32)])
            self.L = np.concatenate([self.L, np.empty(c - len(self.L), dtype=object)])
            self.mass = np.concatenate([self.mass, np.zeros(c - len(self.mass), np.int32)])
            self.ref = np.concatenate([self.ref, np.zeros(c - len(self.ref), np.int32)])

    def memorize(self, x, y, mitosis=5):
        y = str(y)
        if self.n:
            sim = self.M[:self.n] @ x
            i = int(np.argmax(sim))
            if self.L[i] == y and sim[i] > self.threshold:
                self.ref[i] += 1                       # 被"提取"一次 → 进 legal hold 候选
                if self.mass[i] > mitosis:
                    self._grow(); self.M[self.n] = x; self.L[self.n] = y
                    self.mass[self.n] = 1; self.n += 1
                    return
                rate = 1.0 / (self.mass[i] + 1)
                nv = self.M[i] * (1 - rate) + x * rate
                nn = np.linalg.norm(nv)
                self.M[i] = nv / nn if nn > 0 else nv
                self.mass[i] += 1
                return
        self._grow(); self.M[self.n] = x; self.L[self.n] = y
        self.mass[self.n] = 1; self.n += 1

    def prune(self, mode="classwise"):
        if mode == "origin":
            # 原规则：全局 mass<=1 剪掉
            keep = [i for i in range(self.n) if self.mass[i] > 1]
        elif mode == "none":
            keep = list(range(self.n))
        else:
            # 档案规则：按类保底 + legal hold
            keep = []
            labels = [str(self.L[i]) for i in range(self.n)]
            for i in range(self.n):
                lab = labels[i]
                cnt = labels.count(lab)
                held = self.ref[i] >= self.hold_threshold
                if held:
                    self.n_held += 1
                    keep.append(i)
                elif cnt <= self.floor_per_class:
                    keep.append(i)                      # 该类已到保底，禁止再剪
                elif self.mass[i] > 1:
                    keep.append(i)
            self.n_pruned = self.n - len(keep)
        if mode != "classwise":
            self.n_pruned = self.n - len(keep)
        self.M[:len(keep)] = self.M[keep]
        self.L[:len(keep)] = self.L[keep]
        self.mass[:len(keep)] = self.mass[keep]
        self.ref[:len(keep)] = self.ref[keep]
        self.n = len(keep)

    def predict(self, X, topk=3):
        if self.n == 0:
            return np.array(["?"] * len(X), dtype=object)
        uniq = sorted(set(str(v) for v in self.L[:self.n]))
        lid = np.array([uniq.index(str(self.L[i])) for i in range(self.n)], np.int32)
        masks = [np.where(lid == i)[0] for i in range(len(uniq))]
        S = X @ self.M[:self.n].T
        sc = np.empty((len(X), len(uniq)), np.float32)
        for j, idx in enumerate(masks):
            v = S[:, idx]
            kk = min(topk, v.shape[1])
            sc[:, j] = np.partition(v, -kk, axis=1)[:, -kk:].mean(axis=1)
        return np.array(uniq, dtype=object)[np.argmax(sc, axis=1)]

    def acc(self, X, y, topk=3):
        yv = np.array([str(v) for v in y], dtype=object)
        return float(np.mean(self.predict(X, topk) == yv))


def exp_A(Ftr, ytr, Fte, yte):
    print("=" * 100)
    print("实验A：按类保留期 + legal hold（档案管理 §2.1）")
    print("=" * 100)
    print(f"{'模式':<18}{'A':<9}{'B':<9}{'C':<9}{'macro':<9}{'星数':<8}{'剪掉':<8}{'hold':<8}")
    print("-" * 100)
    rows = []
    for mode in ["none", "origin", "classwise"]:
        accs = {"A": [], "B": [], "C": []}
        stats = {"n": [], "pruned": [], "held": []}
        for seed in SEEDS:
            st = ClassWiseStore(Ftr.shape[1])
            for name in "ABC":
                itr, _ = split_idx(ytr, TASKS[name], TR, TE, seed)
                for i in itr:
                    st.memorize(Ftr[i], ytr[i])
            st.prune(mode=mode)
            for name in "ABC":
                _, ite = split_idx(yte, TASKS[name], TR, TE, seed)
                accs[name].append(st.acc(Fte[ite], yte[ite]))
            stats["n"].append(st.n)
            stats["pruned"].append(st.n_pruned)
            stats["held"].append(st.n_held)
        A, B, C = [float(np.mean(accs[k])) for k in "ABC"]
        macro = float(np.mean([A, B, C]))
        rows.append({"mode": mode, "A": round(A, 4), "B": round(B, 4), "C": round(C, 4),
                     "macro": round(macro, 4), "n_stars": int(np.mean(stats["n"])),
                     "n_pruned": int(np.mean(stats["pruned"])),
                     "n_held": int(np.mean(stats["held"]))})
        print(f"{mode:<18}{A:<9.4f}{B:<9.4f}{C:<9.4f}{macro:<9.4f}"
              f"{np.mean(stats['n']):<8.0f}{np.mean(stats['pruned']):<8.0f}{np.mean(stats['held']):<8.0f}")
    print("-" * 100)
    return rows


# ---------------- 实验 B：轮值反对者 + anti-windup ----------------

def exp_B(Ftr, ytr, Fte, yte):
    """
    简化两路：路1=全局 topk3，路2=类内均值（不同视角）
    测 dominance 能否双向变化，以及在吸收态下是否还能恢复。

    rotation：每 N 步强制弱势方主判一次
    antiwindup：d 接近 1 时步长衰减
    """
    print("\n" + "=" * 100)
    print("实验B：轮值反对者 + anti-windup（组织/控制论 §2.3）")
    print("=" * 100)

    def run_one(mode, seed, n_steps=1500):
        rng = np.random.default_rng(seed)
        idx = np.array([i for i, lab in enumerate(ytr) if str(lab) in TASKS["A"]])
        rng.shuffle(idx); idx = idx[:n_steps]
        F = Ftr[idx]; Y = [str(ytr[i]) for i in idx]
        # 两路用不同"视角"：路1 用原始 HOG；路2 用"降采样 HOG"（前 64 维）
        F2 = F[:, :64].copy()
        n2 = np.linalg.norm(F2, axis=1, keepdims=True); n2[n2 == 0] = 1
        F2 = F2 / n2

        M1, L1 = [], []
        M2, L2 = [], []
        d = 0.5
        lr = 0.02
        hist = []
        for t in range(len(F)):
            x1, x2 = F[t], F2[t]
            y = Y[t]
            # 两条路各自预测
            def pred(M, L, x):
                if not M:
                    return None
                Mx = np.array(M, np.float32)
                sim = Mx @ x
                k = int(np.argmax(sim))
                return L[k] if sim[k] > 0.3 else None
            p1 = pred(M1, L1, x1)
            p2 = pred(M2, L2, x2)
            c1 = (p1 == y)
            c2 = (p2 == y)

            step = lr
            if mode == "antiwindup":
                step = lr * (1 - abs(2 * d - 1))          # 接近两端时步长变小
            elif mode == "rotation" and t % 300 == 299:
                d = 0.5                                  # 定期轮换
                hist.append(d)
                continue

            if c1 and not c2:
                d = min(1.0, d + step)
            elif c2 and not c1:
                d = max(0.0, d - step)
            hist.append(d)

            # 两侧都学（抑制表达不抑制学习）
            for M, L, x in [(M1, L1, x1), (M2, L2, x2)]:
                if M:
                    Mx = np.array(M, np.float32)
                    sim = Mx @ x
                    k = int(np.argmax(sim))
                    if L[k] == y and sim[k] > 0.560:
                        rate = 1.0 / (len(M) and 2)
                        nv = M[k] * (1 - rate) + x * rate
                        nn = np.linalg.norm(nv)
                        M[k] = nv / nn if nn > 0 else nv
                        continue
                M.append(x.copy()); L.append(y)

        h = np.array(hist)
        delta = np.diff(h) if len(h) > 1 else np.array([0.0])
        return {"mode": mode, "seed": seed,
                "final_d": round(float(h[-1]), 4),
                "n_up": int(np.sum(delta > 1e-9)), "n_down": int(np.sum(delta < -1e-9)),
                "bidirectional": bool(np.sum(delta > 1e-9) > 0 and np.sum(delta < -1e-9) > 0),
                "range": round(float(h.max() - h.min()), 4)}

    rows = []
    for mode in ["plain", "antiwindup", "rotation"]:
        rs = [run_one(mode, s) for s in [0, 1, 2]]
        n_up = np.mean([r["n_up"] for r in rs]); n_dn = np.mean([r["n_down"] for r in rs])
        fin = np.mean([r["final_d"] for r in rs])
        bidir = sum(r["bidirectional"] for r in rs)
        rows.append({"mode": mode, "final_d": round(float(fin), 4),
                     "n_up": round(float(n_up), 1), "n_down": round(float(n_dn), 1),
                     "bidirectional": f"{bidir}/3",
                     "range": round(float(np.mean([r["range"] for r in rs])), 4)})
        print(f"  {mode:<14} 终值d={fin:.3f}  升{ n_up:.0f}次  降{n_dn:.0f}次  "
              f"双向={bidir}/3  d跨度={rows[-1]['range']:.3f}")
    return rows


# ---------------- 实验 C：流动阈值 ----------------

def exp_C(Ftr, ytr, Fte, yte):
    """
    阈值 = 当前已见同类 cos 的滚动分位数（慢时间常数积分反馈）
    与固定阈值对比。
    """
    print("\n" + "=" * 100)
    print("实验C：流动阈值（生物适应 §2.4）")
    print("=" * 100)

    rows = []
    for qname, q in [("fixed0.560", None), ("p20", 20), ("p30", 30), ("p50", 50)]:
        accs = {"A": [], "B": [], "C": []}
        thrs = []
        for seed in SEEDS:
            st = ClassWiseStore(Ftr.shape[1], threshold=BASE_THR)
            if q is None:
                st.threshold = BASE_THR
            sims_same = []
            for name in "ABC":
                itr, _ = split_idx(ytr, TASKS[name], TR, TE, seed)
                for i in itr:
                    x = Ftr[i]
                    y = str(ytr[i])
                    if q is not None and st.n:
                        sim = st.M[:st.n] @ x
                        k = int(np.argmax(sim))
                        if st.L[k] == y:
                            sims_same.append(float(sim[k]))
                            if len(sims_same) >= 30:
                                st.threshold = float(np.percentile(sims_same[-200:], q))
                    st.memorize(x, y)
            thrs.append(st.threshold)
            for name in "ABC":
                _, ite = split_idx(yte, TASKS[name], TR, TE, seed)
                accs[name].append(st.acc(Fte[ite], yte[ite]))
        A, B, C = [float(np.mean(accs[k])) for k in "ABC"]
        macro = float(np.mean([A, B, C]))
        rows.append({"rule": qname, "A": round(A, 4), "B": round(B, 4), "C": round(C, 4),
                     "macro": round(macro, 4), "final_thr": round(float(np.mean(thrs)), 4)})
        print(f"  {qname:<12} macro={macro:.4f}  终值阈值={np.mean(thrs):.4f}  "
              f"(A={A:.3f} B={B:.3f} C={C:.3f})")
    return rows


def main():
    Xtr, ytr = load_mnist(True)
    Xte, yte = load_mnist(False)
    Xtr, ytr = Xtr[:12000], ytr[:12000]
    Xte, yte = Xte[:4000], yte[:4000]
    print("构造 HOG（质心对齐）...")
    Ftr = f_hog(align(Xtr, "center"))
    Fte = f_hog(align(Xte, "center"))
    print(f"  {Ftr.shape}")
    t0 = time.time()

    out = {"experiment": "E-28", "feature": "hog128+center_align",
           "source": "docs/17 三条现实规律",
           "seeds": SEEDS, "per_task": [TR, TE]}
    out["A_class_retention"] = exp_A(Ftr, ytr, Fte, yte)
    out["B_rotation_antiwindup"] = exp_B(Ftr, ytr, Fte, yte)
    out["C_adaptive_threshold"] = exp_C(Ftr, ytr, Fte, yte)
    out["elapsed_sec"] = round(time.time() - t0, 1)

    print(f"\n耗时 {time.time()-t0:.0f}s")
    with open(os.path.join(RESULTS, "e28_three_laws.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"写入 {os.path.join(RESULTS, 'e28_three_laws.json')}")


if __name__ == "__main__":
    main()
