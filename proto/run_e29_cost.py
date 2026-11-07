"""
E-29 不遗忘的代价是什么？

用户问（2026-10-25）：
  "遗忘如果不遗忘的代价是什么，当然可以不遗忘，
   对人来说遗忘只是一种节省和强化机制，ai并不一定要遗忘。"
  "优先保证准确率"

本实验直接量化"不遗忘"的代价，不外推、不猜：
  随数据量增长（1k → 32k），测四个量：
    ① 准确率（是否还升）
    ② 星数（增长曲线）
    ③ 存储字节（增长曲线）
    ④ 单次查询延迟（是否变慢）

并做外推：按实测增长率，推到"人生级"（10^6 / 10^7 / 10^8 条）会是多少。

同时对照"剪枝版"，看剪枝换来了什么、付出了什么。
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


def l2(v):
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    n[n == 0] = 1.0
    return v / n


class Store:
    """全量存储，可选剪枝。记录所有成本。"""

    def __init__(self, dim, threshold=THR, mitosis=5):
        self.dim = dim
        self.threshold = threshold
        self.mitosis = mitosis
        self.M = np.zeros((1024, dim), np.float32)
        self.L = np.empty(1024, dtype=object)
        self.mass = np.zeros(1024, np.int32)
        self.n = 0

    def _grow(self):
        if self.n >= len(self.mass):
            c = len(self.mass) * 2
            self.M = np.vstack([self.M, np.zeros((c - len(self.M), self.dim), np.float32)])
            self.L = np.concatenate([self.L, np.empty(c - len(self.L), dtype=object)])
            self.mass = np.concatenate([self.mass, np.zeros(c - len(self.mass), np.int32)])

    def memorize(self, x, y):
        y = str(y)
        if self.n:
            sim = self.M[:self.n] @ x
            i = int(np.argmax(sim))
            if self.L[i] == y and sim[i] > self.threshold:
                if self.mass[i] > self.mitosis:
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

    def prune(self):
        keep = [i for i in range(self.n) if self.mass[i] > 1]
        self.M[:len(keep)] = self.M[keep]
        self.L[:len(keep)] = self.L[keep]
        self.mass[:len(keep)] = self.mass[keep]
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

    def bytes(self):
        """星库占用的估算字节数"""
        return self.n * (self.dim * 4 + 8)   # 向量 float32 + 标签/元数据


def main():
    Xtr, ytr = load_mnist(True)
    Xte, yte = load_mnist(False)
    t0 = time.time()

    # 测试集固定 2000 条（不参与训练）
    rng = np.random.default_rng(7)
    ti = rng.choice(len(Xte), 2000, replace=False)
    Xt_raw, yt = Xte[ti], yte[ti]

    print("=" * 100)
    print("E-29 不遗忘的代价（随数据量增长，测四个量）")
    print("=" * 100)
    print("特征：HOG128 + 质心对齐｜阈值 0.560｜类内 top-3｜无剪枝")

    # 逐步加数据，每次全量重学（保证可比）
    sizes = [1000, 2000, 4000, 8000, 16000, 32000]
    rows = []
    print(f"\n{'训练量':<8}{'星数':<8}{'准确率':<10}{'存储KB':<10}{'建库秒':<10}"
          f"{'单条查询ms':<12}{'每千样本星数':<14}")
    print("-" * 100)
    for n in sizes:
        if n > len(Xtr):
            break
        Xs = Xtr[:n]
        ys = ytr[:n]
        Ftr = f_hog(center_img(Xs))
        Ft = f_hog(center_img(Xt_raw))

        ts = time.time()
        st = Store(Ftr.shape[1])
        for i in range(n):
            st.memorize(Ftr[i], ys[i])
        build_s = time.time() - ts

        a = st.acc(Ft, yt)

        # 单条查询延迟
        qs = time.time()
        for i in range(200):
            st.predict(Ft[i:i + 1])
        q_ms = (time.time() - qs) / 200 * 1000

        rows.append({"n_train": n, "n_stars": st.n, "acc": round(a, 4),
                     "bytes": st.bytes(), "build_sec": round(build_s, 2),
                     "query_ms": round(q_ms, 3),
                     "stars_per_1k": round(st.n / n * 1000, 1)})
        print(f"{n:<8d}{st.n:<8d}{a:<10.4f}{st.bytes()/1024:<10.1f}{build_s:<10.2f}"
              f"{q_ms:<12.3f}{st.n/n*1000:<14.1f}")
    print("-" * 100)

    # 增长拟合
    ns = np.array([r["n_train"] for r in rows], float)
    ss = np.array([r["n_stars"] for r in rows], float)
    slope, intercept = np.polyfit(ns, ss, 1)
    print(f"\n星数 ≈ {slope:.4f} × 样本数 + {intercept:.1f}")
    print(f"  → 每 1000 条样本约产生 {slope*1000:.0f} 颗星")

    # 准确率是否还在升
    accs = [r["acc"] for r in rows]
    print(f"\n准确率走势: {' → '.join(f'{a:.4f}' for a in accs)}")
    print(f"  从 {accs[0]:.4f} 到 {accs[-1]:.4f}，{'仍在上升' if accs[-1] > accs[0] else '未上升'}"
          f"（{accs[-1]-accs[0]:+.4f}）")

    # ---------- 外推：人生级 ----------
    print("\n" + "=" * 100)
    print("外推：按实测增长率，推到人生级上下文会是多少")
    print("=" * 100)
    dim = rows[0]["bytes"] / (rows[0]["n_stars"] * (128 * 4 + 8))  # sanity
    per_star_bytes = 128 * 4 + 8
    print(f"每颗星约 {per_star_bytes} 字节（{128}维 float32 + 8 字节元数据）")
    print(f"{'事件数':<14}{'星数':<14}{'存储':<14}{'说明':<30}")
    print("-" * 100)
    extrap = []
    for ev in [10 ** 5, 10 ** 6, 10 ** 7, 10 ** 8]:
        stars = slope * ev + intercept
        b = stars * per_star_bytes
        if b < 1024 ** 2:
            sz = f"{b/1024:.0f} KB"
        elif b < 1024 ** 3:
            sz = f"{b/1024**2:.1f} MB"
        else:
            sz = f"{b/1024**3:.1f} GB"
        note = {"100000": "约 3 年的每日记录", "1000000": "约 10 年",
                "10000000": "一个人一生的经历量级", "100000000": "超出一生"}[str(ev)]
        extrap.append({"events": ev, "stars": int(stars), "bytes": int(b), "size": sz, "note": note})
        print(f"{ev:<14d}{stars:<14.0f}{sz:<14}{note:<30}")
    print("-" * 100)

    # ---------- 剪枝版对照 ----------
    print("\n" + "=" * 100)
    print("对照：加剪枝后，代价变成什么")
    print("=" * 100)
    print(f"{'训练量':<8}{'剪前星':<10}{'剪后星':<10}{'准确率':<10}{'存储KB':<10}{'准确率变化':<12}")
    print("-" * 100)
    pruned_rows = []
    for n in [4000, 8000, 16000, 32000]:
        if n > len(Xtr):
            break
        Ftr = f_hog(center_img(Xtr[:n]))
        Ft = f_hog(center_img(Xt_raw))
        st = Store(Ftr.shape[1])
        for i in range(n):
            st.memorize(Ftr[i], ytr[i])
        before = st.n
        a_before = st.acc(Ft, yt)
        st.prune()
        a_after = st.acc(Ft, yt)
        pruned_rows.append({"n_train": n, "stars_before": before, "stars_after": st.n,
                            "acc_before": round(a_before, 4), "acc_after": round(a_after, 4),
                            "bytes_after": st.bytes()})
        print(f"{n:<8d}{before:<10d}{st.n:<10d}{a_after:<10.4f}{st.bytes()/1024:<10.1f}"
              f"{a_after-a_before:<+12.4f}")
    print("-" * 100)

    print(f"\n耗时 {time.time()-t0:.0f}s")

    out = {"experiment": "E-29", "question": "不遗忘的代价是什么",
           "feature": "hog128+center", "threshold": THR,
           "scaling": rows, "fit": {"slope": round(float(slope), 4),
                                    "intercept": round(float(intercept), 1)},
           "extrapolation": extrap, "pruning": pruned_rows,
           "per_star_bytes": per_star_bytes,
           "elapsed_sec": round(time.time() - t0, 1)}
    with open(os.path.join(RESULTS, "e29_cost_of_not_forgetting.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"写入 {os.path.join(RESULTS, 'e29_cost_of_not_forgetting.json')}")


if __name__ == "__main__":
    main()
