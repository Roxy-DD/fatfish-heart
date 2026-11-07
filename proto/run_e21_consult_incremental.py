"""
E-21 联合会诊（修正版）：在真正的增量设置下

E-20 的错误：
  build_path 只用一个任务的样本训练 → 每路只认识 3-4 个类
  → 0.96 是"任务内"成绩，等价于 oracle（E-13 已证明这是个作弊上限）
  → 那个 +0.0189 不能算数

本版修正：
  路1、路2 都按 A→B→C **增量学习**，各认识全部 10 类
  然后对每个任务的测试集做联合会诊
  这才是"平时用一路、可疑时叫第二路"的真实场景
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


def split_idx(y, labels, ntr, nte, seed):
    rng = np.random.default_rng(seed)
    idx = np.array([i for i, lab in enumerate(y) if lab in labels])
    rng.shuffle(idx)
    return idx[:ntr], idx[ntr:ntr + nte]


class Path:
    """一条通路：增量学完 A→B→C，认识全部 10 类。"""

    def __init__(self, seed, tf, threshold=0.705):
        self.tf = tf
        self.b = CorpusCallosum(threshold=threshold, seed=seed)

    def learn(self, F, y, idx):
        for i in idx:
            self.b.memorize(self.tf(F[i]), str(y[i]))

    def freeze(self):
        stars = self.b.all_stars()
        self.M = np.array([s.vector for s in stars], np.float32)
        L = [str(s.label) for s in stars]
        self.uniq = sorted(set(L))
        lid = np.array([self.uniq.index(l) for l in L], np.int32)
        self.masks = [np.where(lid == i)[0] for i in range(len(self.uniq))]
        self.uniq_arr = np.array(self.uniq, dtype=object)
        self.n_stars = len(stars)

    def scores(self, X, k=3):
        S = X @ self.M.T
        sc = np.empty((len(X), len(self.uniq)), np.float32)
        for i, idx in enumerate(self.masks):
            v = S[:, idx]
            kk = min(k, v.shape[1])
            sc[:, i] = np.partition(v, -kk, axis=1)[:, -kk:].mean(axis=1)
        return sc


def top2_gap(sc):
    order = np.argsort(-sc, axis=1)
    a, b = order[:, 0], order[:, 1]
    return a, b, sc[np.arange(len(sc)), a] - sc[np.arange(len(sc)), b]


def main():
    # 路1：语义特征
    F_sem, y_tr, _ = get_features(True, "main", limit=20000, input_size=96)
    F_sem_te, y_te_all, _ = get_features(False, "main", limit=8000, input_size=96)
    mu = F_sem[:9000].mean(axis=0)
    sd = F_sem[:9000].std(axis=0) + 1e-6
    tf_sem = lambda F: l2((F - mu) / sd)

    # 路2：原始像素
    Xtr_raw, ytr_raw = load_mnist(True)
    Xte_raw, yte_raw = load_mnist(False)

    def tf_pix(F):
        a = np.asarray(F, dtype=np.float32)
        if a.ndim <= 2:
            a = a.reshape(-1)
            return a / max(np.linalg.norm(a), 1e-9)
        v = a.reshape(len(a), -1) / 255.0
        n = np.linalg.norm(v, axis=1, keepdims=True)
        n[n == 0] = 1.0
        return v / n

    seeds = [0, 1, 2]
    t0 = time.time()

    print("=" * 104)
    print("E-21 联合会诊（修正版）：两路都增量学完 A→B→C，认识全部 10 类")
    print("=" * 104)

    all_rows = []
    for seed in seeds:
        p1 = Path(seed, tf_sem)
        p2 = Path(seed, tf_pix)
        train_idx_by_task = {}
        test_idx_by_task = {}
        for name in "ABC":
            itr, _ = split_idx(y_tr, TASKS[name], TR, TE, seed)
            # 测试集索引必须在测试集内部切分（不能复用训练集索引）
            _, ite = split_idx(y_te_all, TASKS[name], TR, TE, seed)
            train_idx_by_task[name] = itr
            test_idx_by_task[name] = ite
            p1.learn(F_sem, y_tr, itr)
            p2.learn(Xtr_raw, y_tr, itr)
        p1.freeze()
        p2.freeze()

        print(f"\n--- seed={seed}: 路1 星数 {p1.n_stars}, 路2 星数 {p2.n_stars} ---")

        for name in "ABC":
            ite = test_idx_by_task[name]
            X1 = np.stack([tf_sem(F_sem_te[i]) for i in ite])
            X2 = np.stack([tf_pix(Xte_raw[i]) for i in ite])
            yt = np.array([str(y_te_all[i]) for i in ite], dtype=object)

            sc1, sc2 = p1.scores(X1), p2.scores(X2)
            a1, b1, g1 = top2_gap(sc1)
            a2, b2, g2 = top2_gap(sc2)
            pred1, pred2 = p1.uniq_arr[a1], p2.uniq_arr[a2]

            acc1 = float(np.mean(pred1 == yt))
            acc2 = float(np.mean(pred2 == yt))
            agree = float(np.mean(pred1 == pred2))

            print(f"  任务{name}: 路1={acc1:.4f} 路2={acc2:.4f} 两路一致率={agree:.3f}")

            for d in [0.0, 0.01, 0.02, 0.03, 0.05, 0.08]:
                consult = g1 < d
                n_c = int(consult.sum())
                pred = pred1.copy()
                conf = consult & (pred1 != pred2)
                # 会诊：分歧时听更自信的一路
                pick2 = conf & (g2 > g1)
                pred[pick2] = pred2[pick2]

                acc = float(np.mean(pred == yt))
                saved = int((conf & (pred2 == yt)).sum())
                hurt = int((conf & (pred2 != yt) & (pred1 == yt)).sum())

                all_rows.append({
                    "seed": seed, "task": name, "d": d,
                    "n_consult": n_c, "consult_rate": round(n_c / len(yt), 4),
                    "n_disagree": int(conf.sum()),
                    "saved": saved, "hurt": hurt,
                    "acc_path1": round(acc1, 4), "acc_path2": round(acc2, 4),
                    "acc_consult": round(acc, 4), "delta": round(acc - acc1, 4),
                })

    print(f"\n耗时 {time.time()-t0:.0f}s")

    print("\n" + "=" * 104)
    print("汇总（9 组：3 seeds × 3 任务）")
    print("=" * 104)
    print(f"{'d':<8}{'触发率':<10}{'分歧':<8}{'救回':<8}{'误伤':<8}{'净':<8}{'平均Δ':<10}{'胜/负':<10}")
    print("-" * 104)
    summary = {}
    for d in [0.0, 0.01, 0.02, 0.03, 0.05, 0.08]:
        rs = [r for r in all_rows if r["d"] == d]
        cr = float(np.mean([r["consult_rate"] for r in rs]))
        nd = float(np.mean([r["n_disagree"] for r in rs]))
        sv = float(np.mean([r["saved"] for r in rs]))
        ht = float(np.mean([r["hurt"] for r in rs]))
        dd = float(np.mean([r["delta"] for r in rs]))
        win = int(sum(1 for r in rs if r["delta"] > 0.001))
        lose = int(sum(1 for r in rs if r["delta"] < -0.001))
        verdict = "有益" if dd > 0.002 else ("有害" if dd < -0.002 else "无差别")
        summary[str(d)] = {"consult_rate": round(cr, 4), "n_disagree": round(nd, 2),
                           "saved": round(sv, 2), "hurt": round(ht, 2),
                           "net": round(sv - ht, 2), "delta": round(dd, 4),
                           "win": win, "lose": lose, "verdict": verdict}
        print(f"{d:<8.2f}{cr:<10.4f}{nd:<8.2f}{sv:<8.2f}{ht:<8.2f}{sv-ht:<+8.2f}"
              f"{dd:<+10.4f}{win}/{lose:<10d}")
    print("-" * 104)

    base1 = float(np.mean([r["acc_path1"] for r in all_rows if r["d"] == 0.0]))
    base2 = float(np.mean([r["acc_path2"] for r in all_rows if r["d"] == 0.0]))
    print(f"\n单独两路（增量 10 类，9 组均值）：路1={base1:.4f}  路2={base2:.4f}")

    best = max(summary.items(), key=lambda kv: kv[1]["delta"])
    print(f"\n★ 最佳 d={best[0]}: Δ={best[1]['delta']:+.4f} "
          f"触发率 {best[1]['consult_rate']:.1%}，救回 {best[1]['saved']:.1f}，"
          f"误伤 {best[1]['hurt']:.1f}，净 {best[1]['net']:+.1f}，"
          f"胜 {best[1]['win']}/9 负 {best[1]['lose']}/9")

    out = {"experiment": "E-21",
           "correction_of": "E-20（E-20 误用 oracle 式单任务训练）",
           "setting": "两路均增量学完 A→B→C（10 类）",
           "path1": "MobileNetV2 1280d zsc", "path2": "raw pixel 784d",
           "seeds": seeds, "base_path1": round(base1, 4), "base_path2": round(base2, 4),
           "rows": all_rows, "summary": summary,
           "best_d": best[0], "elapsed_sec": round(time.time() - t0, 1)}
    with open(os.path.join(RESULTS, "e21_consult_incremental.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e21_consult_incremental.json')}")


if __name__ == "__main__":
    main()
