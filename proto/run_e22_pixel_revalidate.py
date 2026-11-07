"""
E-22 换到像素特征后，重新验证三个机制结论 + 改进会诊判据

要回答：
  1. 像素特征下，层级递归还害不害？（E-10 曾测：语义特征下 -17pp）
  2. 像素特征下，子星还有没有用？（E-11 曾测：语义特征下 +8.33pp 内容增益）
  3. "两路不一致才触发" vs "某一路 gap<d 才触发"，哪个更符合"危重才会诊"？

方法：
  · 像素特征空间，先做阈值标定（因为 0.705 是语义空间的值）
  · 层级 / 平坦 / root-only 三组消融
  · 两种会诊触发判据对比：救回、误伤、净收益、触发率

不预设门槛。只观测什么让它变好/变差。
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
from retina import get_features, load_mnist

RESULTS = os.path.join(_HERE, "results")
TASKS = {"A": {"0", "1", "2"}, "B": {"3", "4", "5"}, "C": {"6", "7", "8", "9"}}
TR, TE = 900, 300


def l2(v):
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    n[n == 0] = 1.0
    return v / n


def tf_pix(F):
    a = np.asarray(F, dtype=np.float32)
    if a.ndim <= 2:
        a = a.reshape(-1)
        return a / max(np.linalg.norm(a), 1e-9)
    v = a.reshape(len(a), -1) / 255.0
    n = np.linalg.norm(v, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return v / n


def split_idx(y, labels, ntr, nte, seed):
    """labels 是字符串集合；y 可能是 int 或 str，统一转 str 比较。"""
    rng = np.random.default_rng(seed)
    labels = {str(l) for l in labels}
    idx = np.array([i for i, lab in enumerate(y) if str(lab) in labels])
    if len(idx) == 0:
        return np.array([], dtype=int), np.array([], dtype=int)
    rng.shuffle(idx)
    return idx[:ntr], idx[ntr:ntr + nte]


class Flat:
    def __init__(self, V, L):
        self.M = np.ascontiguousarray(V, np.float32)
        L = [str(x) for x in L]
        self.uniq = sorted(set(L))
        lid = np.array([self.uniq.index(l) for l in L], np.int32)
        self.masks = [np.where(lid == i)[0] for i in range(len(self.uniq))]
        self.arr = np.array(self.uniq, dtype=object)
        self.n = len(L)

    def scores(self, X, k=3):
        S = X @ self.M.T
        sc = np.empty((len(X), len(self.uniq)), np.float32)
        for i, idx in enumerate(self.masks):
            v = S[:, idx]
            kk = min(k, v.shape[1])
            sc[:, i] = np.partition(v, -kk, axis=1)[:, -kk:].mean(axis=1)
        return sc

    def acc(self, X, y, k=3):
        y = np.array([str(v) for v in y], dtype=object)
        return float(np.mean(self.arr[np.argmax(self.scores(X, k), axis=1)] == y))


def build_pixel(Xtr, ytr, seed, threshold, mode="flat_all"):
    """mode: flat_all | flat_root | hier"""
    b = CorpusCallosum(threshold=threshold, seed=seed)
    for name in "ABC":
        itr, _ = split_idx(ytr, TASKS[name], TR, TE, seed)
        for i in itr:
            b.memorize(tf_pix(Xtr[i]), str(ytr[i]))
    if mode == "flat_root":
        stars = b.right.galaxy
    else:
        stars = b.all_stars()
    return b, Flat(np.array([s.vector for s in stars], np.float32), [s.label for s in stars])


def main():
    Xtr, ytr = load_mnist(True)
    Xte, yte = load_mnist(False)
    seeds = [0, 1, 2]
    t0 = time.time()

    # ---------- 步骤 1：像素空间阈值标定 ----------
    print("=" * 100)
    print("步骤1：像素空间的阈值标定（0.705 是语义空间的值，不能直接搬）")
    print("=" * 100)
    # 用同类/异类 cos 分布找最优阈值
    rng = np.random.default_rng(0)
    samp = rng.choice(4000, 400, replace=False)
    P = np.stack([tf_pix(Xtr[i]) for i in samp])
    ys = np.array([str(ytr[i]) for i in samp])
    S = P @ P.T
    iu = np.triu_indices(len(P), k=1)
    sims = S[iu]
    same = np.array(ys)[None, :] == np.array(ys)[:, None]
    same = same[iu]
    print(f"  同类 cos: 均值 {sims[same].mean():.4f} ± {sims[same].std():.4f}")
    print(f"  异类 cos: 均值 {sims[~same].mean():.4f} ± {sims[~same].std():.4f}")
    print(f"  间隔: {sims[same].mean()-sims[~same].mean():+.4f}")
    best_t, best_b = 0.5, 0.0
    for t in np.arange(0.2, 0.99, 0.005):
        tpr = float(np.mean(sims[same] > t))
        tnr = float(np.mean(sims[~same] <= t))
        b = 0.5 * (tpr + tnr)
        if b > best_b:
            best_b, best_t = b, float(t)
    print(f"  最优阈值 T={best_t:.3f}  balanced_acc={best_b:.4f}")
    print(f"  （语义空间的最优是 0.705；像素空间是 {best_t:.3f}）")

    # ---------- 步骤 2：层级 / 平坦 / root-only 消融 ----------
    print("\n" + "=" * 100)
    print("步骤2：像素特征下，层级还害不害？")
    print("=" * 100)
    print(f"{'配置':<14}{'A':<10}{'B':<10}{'C':<10}{'macro':<10}{'星数':<8}")
    print("-" * 100)
    abl = {}
    for mode in ["flat_all", "flat_root", "hier"]:
        accs = {"A": [], "B": [], "C": []}
        nstars = []
        for seed in seeds:
            b, idx = build_pixel(Xtr, ytr, seed, best_t, mode)
            nstars.append(idx.n)
            for name in "ABC":
                _, ite = split_idx(yte, TASKS[name], TR, TE, seed)
                Xt = np.stack([tf_pix(Xte[i]) for i in ite])
                yt = np.array([str(yte[i]) for i in ite], dtype=object)
                if mode == "hier":
                    c = 0
                    for xi, yi in zip(Xt, yt):
                        s, _ = b.right.perceive(xi)
                        c += int((s.label if s else None) == yi)
                    accs[name].append(c / len(Xt))
                else:
                    accs[name].append(idx.acc(Xt, yt))
        A, B, C = [np.mean(accs[k]) for k in "ABC"]
        macro = float(np.mean([A, B, C]))
        abl[mode] = {"A": round(float(A), 4), "B": round(float(B), 4),
                     "C": round(float(C), 4), "macro": round(macro, 4),
                     "n_stars": int(np.mean(nstars))}
        print(f"{mode:<14}{A:<10.4f}{B:<10.4f}{C:<10.4f}{macro:<10.4f}{np.mean(nstars):<8.0f}")
    print("-" * 100)
    d_hier = abl["flat_all"]["macro"] - abl["hier"]["macro"]
    print(f"\n  平坦全星 − 层级 = {d_hier:+.4f}  "
          f"({'层级仍然有害' if d_hier > 0 else '层级在像素空间无害'})")
    print(f"  平坦全星 − 只用根星 = {abl['flat_all']['macro']-abl['flat_root']['macro']:+.4f}")

    # ---------- 步骤 3：会诊两种触发判据 ----------
    print("\n" + "=" * 100)
    print("步骤3：两种会诊触发判据对比（路1=像素, 路2=语义）")
    print("=" * 100)
    Fsem, _, _ = get_features(True, "main", limit=20000, input_size=96)
    Fsem_te, _, _ = get_features(False, "main", limit=8000, input_size=96)
    mu = Fsem[:9000].mean(axis=0)
    sd = Fsem[:9000].std(axis=0) + 1e-6
    tf_sem = lambda F: l2((F - mu) / sd)

    rows = []
    for seed in seeds:
        # 两路必须用同一批样本索引，故统一截断到特征缓存的长度
        n_tr = min(len(Xtr), len(Fsem))
        n_te = min(len(Xte), len(Fsem_te))
        y_tr_c = ytr[:n_tr]
        y_te_c = yte[:n_te]
        Xtr_c = Xtr[:n_tr]
        Xte_c = Xte[:n_te]

        # 路1：像素（主路）
        b1 = CorpusCallosum(threshold=best_t, seed=seed)
        # 路2：语义
        b2 = CorpusCallosum(threshold=0.705, seed=seed)
        for name in "ABC":
            itr, _ = split_idx(y_tr_c, TASKS[name], TR, TE, seed)
            for i in itr:
                b1.memorize(tf_pix(Xtr_c[i]), str(y_tr_c[i]))
                b2.memorize(tf_sem(Fsem[i]), str(y_tr_c[i]))
        s1 = b1.all_stars()
        s2 = b2.all_stars()
        idx1 = Flat(np.array([v.vector for v in s1], np.float32), [v.label for v in s1])
        idx2 = Flat(np.array([v.vector for v in s2], np.float32), [v.label for v in s2])

        for name in "ABC":
            _, ite = split_idx(y_te_c, TASKS[name], TR, TE, seed)
            X1 = np.stack([tf_pix(Xte_c[i]) for i in ite])
            X2 = np.stack([tf_sem(Fsem_te[i]) for i in ite])
            yt = np.array([str(y_te_c[i]) for i in ite], dtype=object)

            sc1, sc2 = idx1.scores(X1), idx2.scores(X2)
            o1, o2 = np.argsort(-sc1, axis=1), np.argsort(-sc2, axis=1)
            g1 = sc1[np.arange(len(sc1)), o1[:, 0]] - sc1[np.arange(len(sc1)), o1[:, 1]]
            g2 = sc2[np.arange(len(sc2)), o2[:, 0]] - sc2[np.arange(len(sc2)), o2[:, 1]]
            p1v, p2v = idx1.arr[o1[:, 0]], idx2.arr[o2[:, 0]]
            acc1 = float(np.mean(p1v == yt))
            acc2 = float(np.mean(p2v == yt))

            # 判据 A：主路 gap < d
            for d in [0.01, 0.02, 0.03, 0.05]:
                trg = g1 < d
                pred = p1v.copy()
                chg = trg & (p1v != p2v)
                pick2 = chg & (g2 > g1)
                pred[pick2] = p2v[pick2]
                acc = float(np.mean(pred == yt))
                rows.append({"seed": seed, "task": name, "criterion": "gap",
                             "param": d, "trigger_rate": round(float(trg.mean()), 4),
                             "n_chg": int(chg.sum()),
                             "saved": int((chg & (p2v == yt)).sum()),
                             "hurt": int((chg & (p2v != yt) & (p1v == yt)).sum()),
                             "acc_main": round(acc1, 4), "acc_other": round(acc2, 4),
                             "acc": round(acc, 4), "delta": round(acc - acc1, 4)})

            # 判据 B：两路不一致才触发（分歧即危重）
            dis = p1v != p2v
            pred = p1v.copy()
            pick2 = dis & (g2 > g1)
            pred[pick2] = p2v[pick2]
            acc = float(np.mean(pred == yt))
            rows.append({"seed": seed, "task": name, "criterion": "disagree",
                         "param": 0, "trigger_rate": round(float(dis.mean()), 4),
                         "n_chg": int(dis.sum()),
                         "saved": int((dis & (p2v == yt)).sum()),
                         "hurt": int((dis & (p2v != yt) & (p1v == yt)).sum()),
                         "acc_main": round(acc1, 4), "acc_other": round(acc2, 4),
                         "acc": round(acc, 4), "delta": round(acc - acc1, 4)})

    print(f"{'判据':<12}{'参数':<8}{'触发率':<10}{'改判':<8}{'救回':<8}{'误伤':<8}"
          f"{'净':<8}{'平均Δ':<10}{'胜/负':<8}")
    print("-" * 100)
    summ = {}
    for crit in ["gap", "disagree"]:
        params = sorted({r["param"] for r in rows if r["criterion"] == crit})
        for p in params:
            rs = [r for r in rows if r["criterion"] == crit and r["param"] == p]
            tr = float(np.mean([r["trigger_rate"] for r in rs]))
            sv = float(np.mean([r["saved"] for r in rs]))
            ht = float(np.mean([r["hurt"] for r in rs]))
            dd = float(np.mean([r["delta"] for r in rs]))
            win = sum(1 for r in rs if r["delta"] > 0.001)
            lose = sum(1 for r in rs if r["delta"] < -0.001)
            key = f"{crit}:{p}"
            summ[key] = {"trigger_rate": round(tr, 4), "saved": round(sv, 2),
                         "hurt": round(ht, 2), "net": round(sv - ht, 2),
                         "delta": round(dd, 4), "win": win, "lose": lose}
            print(f"{crit:<12}{p:<8.2f}{tr:<10.4f}{np.mean([r['n_chg'] for r in rs]):<8.1f}"
                  f"{sv:<8.2f}{ht:<8.2f}{sv-ht:<+8.2f}{dd:<+10.4f}{win}/{lose}")
    print("-" * 100)

    b_main = float(np.mean([r["acc_main"] for r in rows if r["criterion"] == "gap" and r["param"] == 0.01]))
    b_other = float(np.mean([r["acc_other"] for r in rows if r["criterion"] == "gap" and r["param"] == 0.01]))
    print(f"\n主路(像素)={b_main:.4f}  副路(语义)={b_other:.4f}")

    print(f"\n耗时 {time.time()-t0:.0f}s")

    out = {"experiment": "E-22",
           "pixel_calibration": {"same_mean": round(float(sims[same].mean()), 4),
                                 "diff_mean": round(float(sims[~same].mean()), 4),
                                 "best_threshold": round(best_t, 4),
                                 "best_balanced_acc": round(best_b, 4)},
           "ablation": abl, "hierarchy_cost": round(float(d_hier), 4),
           "consult": summ, "rows": rows,
           "base_pixel": round(b_main, 4), "base_semantic": round(b_other, 4),
           "elapsed_sec": round(time.time() - t0, 1)}
    with open(os.path.join(RESULTS, "e22_pixel_revalidate.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e22_pixel_revalidate.json')}")


if __name__ == "__main__":
    main()
