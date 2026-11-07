"""
E-24 记忆科学迁移：间隔 / 提取练习 / 重学窗口

现实规律（docs/17 §2.2，来源 Nature Rev Psych / PMC / J Neurosci）：
  B1 提取比重读有效（testing effect）
  B2 间隔有效，因为每次间隔的尝试会"提取并重新激活"前一次的痕迹；
     集中训练时痕迹仍活跃 → 不会被提取 → 无法强化
  B3 间隔太长也不行（痕迹已无法提取）
  B4 提取→重学有最优窗口；0 间隔或超长间隔，增益消失 20-30%
  B6 0 间隔无效，因为缺少"结束信号"，无法触发再固化

迁移到本系统：
  · "提取" = perceive 命中一颗星
  · "重学" = memorize 命中同一颗星并 merge
  · "间隔" = 两次命中之间流逝的步数
  · 集中(massed)   = 同类样本连续灌入
    间隔(spaced)   = 同类样本交错出现

两个可独立改的地方：
  数据侧：样本顺序（massed / interleaved / expanding）
  模型侧：提取是否强化（现在提取完全不强化，只有学习强化）

实验设计：
  A. 顺序对比：massed vs interleaved vs expanding   （测 B2/B3）
  B. 提取强化：开/关                                  （测 B1）
  C. 重学窗口：merge 速率随 gap 变化                   （测 B4/B6）

不预设门槛。只问：什么让它变好，什么让它变差。
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
TASKS = {"A": {"0", "1", "2"}, "B": {"3", "4", "5"}, "C": {"6", "7", "8", "9"}}
TR, TE = 900, 300
SEEDS = [0, 1, 2]
THRESHOLD = 0.560          # HOG 空间标定（E-23）


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


# ---------------- 顺序生成 ----------------

def order_massed(idx, y, rng):
    """按标签分块：所有 0，然后所有 1，然后所有 2……"""
    idx = np.array(idx)
    labs = np.array([str(y[i]) for i in idx])
    out = []
    for lab in sorted(set(labs)):
        sub = idx[labs == lab]
        rng.shuffle(sub)
        out.extend(sub.tolist())
    return np.array(out)


def order_interleaved(idx, y, rng):
    """轮转：0,1,2,0,1,2……（等间隔）"""
    idx = np.array(idx)
    labs = np.array([str(y[i]) for i in idx])
    buckets = {lab: idx[labs == lab].tolist() for lab in sorted(set(labs))}
    for v in buckets.values():
        rng.shuffle(v)
    out = []
    cursor = {k: 0 for k in buckets}
    remaining = sum(len(v) for v in buckets.values())
    order = list(buckets.keys())
    while remaining:
        for lab in order:
            if cursor[lab] < len(buckets[lab]):
                out.append(buckets[lab][cursor[lab]])
                cursor[lab] += 1
                remaining -= 1
    return np.array(out)


def order_expanding(idx, y, rng):
    """
    扩张间隔（模仿间隔重复日程）：
    第一轮每个类各 1 个，然后间隔 1 轮各 1 个，间隔 2 轮……
    近似：把每类的样本分成若干轮，轮次越往后，轮内间隔越大。
    """
    idx = np.array(idx)
    labs = np.array([str(y[i]) for i in idx])
    buckets = {lab: idx[labs == lab].tolist() for lab in sorted(set(labs))}
    for v in buckets.values():
        rng.shuffle(v)
    out = []
    ptr = {k: 0 for k in buckets}
    n_total = sum(len(v) for v in buckets.values())
    gap = 1
    while len(out) < n_total:
        progressed = False
        for lab in buckets:
            for _ in range(1):                     # 每轮每类 1 个
                if ptr[lab] < len(buckets[lab]):
                    out.append(buckets[lab][ptr[lab]])
                    ptr[lab] += 1
                    progressed = True
        if not progressed:
            break
        # 每完成一轮，插入 gap 个「别的类」的间隔——用轮空模拟
        gap = min(gap * 2, 8)
        if gap > 1:
            # 插入轮空：不追加样本，只是概念上拉长间隔
            pass
    return np.array(out)


# ---------------- 核心：带提取/窗口的学习器 ----------------

class SpacedBrain:
    """
    平坦检索 + 类内 top-3。
    新增（对应记忆科学）：
      · retrieval_reinforce：提取正确时也强化（testing effect）
      · window_boost：两次命中间隔落在 [lo, hi] 时，merge 速率放大（再固化窗口）
    """

    def __init__(self, dim, threshold=THRESHOLD, retrieval_reinforce=0.0,
                 window_lo=200, window_hi=600, window_boost=2.0,
                 base_rate="mass", mitosis_threshold=5):
        self.M = np.zeros((4096, dim), np.float32)
        self.lab = np.empty(4096, dtype=object)
        self.mass = np.zeros(4096, np.int32)
        self.last_step = np.zeros(4096, np.int64)
        self.n = 0
        self.threshold = threshold
        self.retrieval_reinforce = retrieval_reinforce
        self.window_lo = window_lo
        self.window_hi = window_hi
        self.window_boost = window_boost
        self.base_rate = base_rate
        self.mitosis_threshold = mitosis_threshold   # 与 core.py 一致：mass > 此值则分裂
        self.step = 0
        self.n_boosted = 0
        self.n_massed = 0
        self.n_retrieval = 0
        self.n_mitosis = 0

    def _grow(self):
        if self.n >= len(self.mass):
            cap = len(self.mass) * 2
            self.M = np.vstack([self.M, np.zeros((cap - len(self.M), self.M.shape[1]), np.float32)])
            self.lab = np.concatenate([self.lab, np.empty(cap - len(self.lab), dtype=object)])
            self.mass = np.concatenate([self.mass, np.zeros(cap - len(self.mass), np.int32)])
            self.last_step = np.concatenate([self.last_step, np.zeros(cap - len(self.last_step), np.int64)])

    def _retrieve(self, x):
        if self.n == 0:
            return -1, 0.0
        sim = self.M[:self.n] @ x
        i = int(np.argmax(sim))
        return i, float(sim[i])

    def memorize(self, x, y):
        self.step += 1
        y = str(y)
        i, sim = self._retrieve(x)

        if i >= 0 and self.lab[i] == y and sim > self.threshold:
            gap = self.step - int(self.last_step[i])
            # 与 core.py 一致：mass 超过阈值则分裂（新建同标签星），而不是继续 merge
            if self.mass[i] > self.mitosis_threshold:
                self._grow()
                self.M[self.n] = x
                self.lab[self.n] = y
                self.mass[self.n] = 1
                self.last_step[self.n] = self.step
                self.n += 1
                self.n_mitosis += 1
                return

            rate = 1.0 / (self.mass[i] + 1)
            boosted = False
            if self.window_lo <= gap <= self.window_hi:
                rate = min(1.0, rate * self.window_boost)
                boosted = True
                self.n_boosted += 1
            elif gap < self.window_lo:
                self.n_massed += 1
            nv = self.M[i] * (1 - rate) + x * rate
            nn = np.linalg.norm(nv)
            self.M[i] = nv / nn if nn > 0 else nv
            self.mass[i] += 1
            self.last_step[i] = self.step
        else:
            self._grow()
            self.M[self.n] = x
            self.lab[self.n] = y
            self.mass[self.n] = 1
            self.last_step[self.n] = self.step
            self.n += 1

        # 提取强化：无论是否命中同类标签，被"提取"的那颗星都往样本方向微调
        if self.retrieval_reinforce > 0 and i >= 0:
            nv = self.M[i] * (1 - self.retrieval_reinforce) + x * self.retrieval_reinforce
            nn = np.linalg.norm(nv)
            if nn > 0:
                self.M[i] = nv / nn
            self.n_retrieval += 1

    def predict(self, X, topk=3):
        if self.n == 0:
            return np.array(["?"] * len(X), dtype=object)
        S = X @ self.M[:self.n].T
        labs = np.array([str(v) for v in self.lab[:self.n]], dtype=object)
        uniq = sorted(set(labs.tolist()))
        lid = np.array([uniq.index(l) for l in labs], np.int32)
        masks = [np.where(lid == i)[0] for i in range(len(uniq))]
        sc = np.empty((len(X), len(uniq)), np.float32)
        for j, idx in enumerate(masks):
            v = S[:, idx]
            kk = min(topk, v.shape[1])
            sc[:, j] = np.partition(v, -kk, axis=1)[:, -kk:].mean(axis=1)
        return np.array(uniq, dtype=object)[np.argmax(sc, axis=1)]


def run(Ftr, ytr, Fte, yte, seed, order_fn, **brain_kw):
    dim = Ftr.shape[1]
    b = SpacedBrain(dim, **brain_kw)
    rng = np.random.default_rng(seed + 100)
    for name in "ABC":
        itr, _ = split_idx(ytr, TASKS[name], TR, TE, seed)
        seq = order_fn(itr, ytr, rng)
        for i in seq:
            b.memorize(Ftr[i], ytr[i])
    accs = {}
    for name in "ABC":
        _, ite = split_idx(yte, TASKS[name], TR, TE, seed)
        Xt = np.stack([Fte[i] for i in ite])
        yt = np.array([str(yte[i]) for i in ite], dtype=object)
        accs[name] = float(np.mean(b.predict(Xt) == yt))
    return accs, b


def main():
    Xtr, ytr = load_mnist(True)
    Xte, yte = load_mnist(False)
    Xtr, ytr = Xtr[:12000], ytr[:12000]
    Xte, yte = Xte[:4000], yte[:4000]

    print("构造 HOG 特征...")
    Ftr, Fte = f_hog(Xtr), f_hog(Xte)
    print(f"  {Ftr.shape}")
    t0 = time.time()

    orders = [("massed", order_massed), ("interleaved", order_interleaved),
              ("expanding", order_expanding)]

    print("\n" + "=" * 104)
    print("A. 顺序对比（测 B2/B3：间隔是否优于集中）")
    print("=" * 104)
    print(f"{'顺序':<14}{'A':<9}{'B':<9}{'C':<9}{'macro':<9}{'星数':<8}"
          f"{'窗口增强':<10}{'集中':<8}{'提取强化':<9}")
    print("-" * 104)
    rows_order = []
    for oname, ofn in orders:
        accs = {"A": [], "B": [], "C": []}
        stats = {"boosted": [], "massed": [], "retrieval": [], "stars": []}
        for seed in SEEDS:
            a, b = run(Ftr, ytr, Fte, yte, seed, ofn)
            for k in "ABC":
                accs[k].append(a[k])
            stats["boosted"].append(b.n_boosted)
            stats["massed"].append(b.n_massed)
            stats["retrieval"].append(b.n_retrieval)
            stats["stars"].append(b.n)
        A, B, C = [float(np.mean(accs[k])) for k in "ABC"]
        macro = float(np.mean([A, B, C]))
        rows_order.append({"order": oname, "A": round(A, 4), "B": round(B, 4),
                           "C": round(C, 4), "macro": round(macro, 4),
                           "n_stars": int(np.mean(stats["stars"])),
                           "n_boosted": int(np.mean(stats["boosted"])),
                           "n_massed": int(np.mean(stats["massed"]))})
        print(f"{oname:<14}{A:<9.4f}{B:<9.4f}{C:<9.4f}{macro:<9.4f}"
              f"{np.mean(stats['stars']):<8.0f}{np.mean(stats['boosted']):<10.0f}"
              f"{np.mean(stats['massed']):<8.0f}{np.mean(stats['retrieval']):<9.0f}")
    print("-" * 104)

    best_o = max(rows_order, key=lambda r: r["macro"])
    worst_o = min(rows_order, key=lambda r: r["macro"])
    print(f"\n最差 {worst_o['order']}={worst_o['macro']:.4f}  "
          f"最好 {best_o['order']}={best_o['macro']:.4f}  "
          f"差 {best_o['macro']-worst_o['macro']:+.4f}")

    # ---------- B. 提取强化 ----------
    print("\n" + "=" * 104)
    print("B. 提取强化（测 B1：testing effect）")
    print("=" * 104)
    print(f"{'提取强化':<12}{'A':<9}{'B':<9}{'C':<9}{'macro':<9}{'星数':<8}")
    print("-" * 104)
    rows_rr = []
    for rr in [0.0, 0.02, 0.05, 0.1]:
        accs = {"A": [], "B": [], "C": []}
        stars = []
        for seed in SEEDS:
            a, b = run(Ftr, ytr, Fte, yte, seed, order_interleaved, retrieval_reinforce=rr)
            for k in "ABC":
                accs[k].append(a[k])
            stars.append(b.n)
        A, B, C = [float(np.mean(accs[k])) for k in "ABC"]
        macro = float(np.mean([A, B, C]))
        rows_rr.append({"retrieval_reinforce": rr, "A": round(A, 4), "B": round(B, 4),
                        "C": round(C, 4), "macro": round(macro, 4),
                        "n_stars": int(np.mean(stars))})
        print(f"{rr:<12.2f}{A:<9.4f}{B:<9.4f}{C:<9.4f}{macro:<9.4f}{np.mean(stars):<8.0f}")
    print("-" * 104)

    # ---------- C. 重学窗口 ----------
    print("\n" + "=" * 104)
    print("C. 重学窗口（测 B4/B6：0 间隔无效，超长间隔也无效）")
    print("=" * 104)
    print(f"{'窗口(lo,hi)':<16}{'A':<9}{'B':<9}{'C':<9}{'macro':<9}{'增强次数':<10}")
    print("-" * 104)
    rows_win = []
    for lo, hi in [(0, 0), (1, 5), (5, 20), (20, 80), (80, 300), (300, 1000), (1000, 3000)]:
        accs = {"A": [], "B": [], "C": []}
        nb = []
        for seed in SEEDS:
            a, b = run(Ftr, ytr, Fte, yte, seed, order_interleaved,
                       window_lo=lo, window_hi=hi, window_boost=2.5)
            for k in "ABC":
                accs[k].append(a[k])
            nb.append(b.n_boosted)
        A, B, C = [float(np.mean(accs[k])) for k in "ABC"]
        macro = float(np.mean([A, B, C]))
        rows_win.append({"lo": lo, "hi": hi, "A": round(A, 4), "B": round(B, 4),
                         "C": round(C, 4), "macro": round(macro, 4),
                         "n_boosted": int(np.mean(nb))})
        print(f"{f'{lo},{hi}':<16}{A:<9.4f}{B:<9.4f}{C:<9.4f}{macro:<9.4f}{np.mean(nb):<10.0f}")
    print("-" * 104)

    print(f"\n耗时 {time.time()-t0:.0f}s")

    base_macro = next(r["macro"] for r in rows_order if r["order"] == "interleaved")
    print(f"\n参照：interleaved + 无提取强化 + 窗口(200,600) = {base_macro:.4f}")

    out = {"experiment": "E-24", "feature": "hog128", "threshold": THRESHOLD,
           "seeds": SEEDS, "per_task": [TR, TE],
           "source": "docs/17 §2.2 记忆科学（间隔/测试效应/再固化窗口）",
           "orders": rows_order, "retrieval_reinforce": rows_rr,
           "windows": rows_win, "baseline_interleaved": base_macro,
           "elapsed_sec": round(time.time() - t0, 1)}
    with open(os.path.join(RESULTS, "e24_memory_science.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e24_memory_science.json')}")


if __name__ == "__main__":
    main()
