"""
诊断 E-1 未过：任务 A 的记忆去哪了？

假设：不是"权重覆盖"，而是**记忆星之间的干扰**——
新任务新建的星，在余弦空间里比 A 自己的星更靠近 A 的测试样本，
于是 perceive() 全局 argmax 把 A 的样本判给 B/C 的标签。

若成立，则设计意图第 1、2 步（冻结感知器 + 分配而非覆盖）**不足以**消除遗忘。
"""
from __future__ import annotations

import json
import os
import sys

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_lite"))

import numpy as np

from core import CorpusCallosum, conflict_degree, cos, evaluate, system_entropy
from retina import get_features
from run_e1_e6 import TASKS, split_task, PER_TASK_TRAIN, PER_TASK_TEST

RESULTS = os.path.join(_HERE, "results")


def confusion_matrix(brain, X, y, labels):
    labs = sorted(labels)
    cm = {a: {b: 0 for b in labs + ["?"]} for a in labs}
    for xi, yi in zip(X, y):
        p, _, _ = brain.perceive(xi)
        cm[yi][p if p in cm[yi] else "?"] += 1
    return cm


def main():
    Ftr, ytr, _ = get_features(True, "main", limit=20000, input_size=96)
    Fte, yte, _ = get_features(False, "main", limit=8000, input_size=96)

    # 重跑 A -> B -> C，同时在每步做混淆分析
    brain = CorpusCallosum(threshold=0.705, seed=0)
    tasks = {}
    for name in ["A", "B", "C"]:
        Xtr, ytr_t, Xte, yte_t = split_task(Ftr, ytr, TASKS[name], PER_TASK_TRAIN, PER_TASK_TEST, 0)
        tasks[name] = (Xte, yte_t)
        for xi, yi in zip(Xtr, ytr_t):
            brain.memorize(xi, yi)
        print(f"[{name}] 学完: root={len(brain.right.galaxy)} "
              f"all={len(brain.all_stars())}")

    print("\n" + "=" * 76)
    print("任务 A 的混淆矩阵（学完 C 之后）")
    print("=" * 76)
    XA, yA = tasks["A"]
    cm = confusion_matrix(brain, XA, yA, TASKS["A"])
    for a in sorted(cm):
        total = sum(cm[a].values())
        parts = "  ".join(f"{b}:{cm[a][b]:3d}" for b in sorted(cm[a]))
        print(f"  真 {a} (n={total}): {parts}")
        wrong = total - cm[a].get(a, 0)
        print(f"        → 错 {wrong} ({wrong/total:.1%})")

    # 关键：错判去向
    wrong_to_B = sum(cm[a].get(b, 0) for a in TASKS["A"] for b in TASKS["B"])
    wrong_to_C = sum(cm[a].get(b, 0) for a in TASKS["A"] for b in TASKS["C"])
    print(f"\n  A 的样本被判成 B: {wrong_to_B}")
    print(f"  A 的样本被判成 C: {wrong_to_C}")

    # 验证"干扰"假设：A 的测试样本与 A/B/C 各类星的最高余弦
    print("\n" + "=" * 76)
    print("干扰假设检验：A 的测试样本，最亲近的星来自哪个任务？")
    print("=" * 76)
    all_stars = brain.all_stars()
    star_task = {}
    for s in all_stars:
        if s.label in TASKS["A"]:
            star_task[s.label] = "A"
        elif s.label in TASKS["B"]:
            star_task[s.label] = "B"
        else:
            star_task[s.label] = "C"

    from collections import Counter
    nearest_task = Counter()
    nearest_sim = {"A": [], "B": [], "C": []}
    for xi, yi in zip(XA, yA):
        best_s, best_sim = None, -2
        for s in brain.right.galaxy:
            c = cos(xi, s.vector)
            if c > best_sim:
                best_sim, best_s = c, s
        t = star_task.get(best_s.label, "?")
        correct = (best_s.label == yi)
        nearest_task[(t, correct)] += 1
        nearest_sim[t].append(best_sim)

    print("  (最近星所属任务, 是否正确): 计数")
    for k, v in sorted(nearest_task.items()):
        print(f"    {k}: {v}")
    for t in ["A", "B", "C"]:
        if nearest_sim[t]:
            print(f"  最近星来自 {t}: n={len(nearest_sim[t])} "
                  f"平均cos={np.mean(nearest_sim[t]):.4f}")

    # 星的质量分布
    print("\n" + "=" * 76)
    print("星的质量分布（按任务）")
    print("=" * 76)
    for t in ["A", "B", "C"]:
        ms = [s.mass for s in brain.right.galaxy if star_task.get(s.label) == t]
        if ms:
            print(f"  任务 {t}: n={len(ms)} mass均值={np.mean(ms):.2f} "
                  f"最大={max(ms)} 总质量={sum(ms)}")

    # 反事实：只学 A，看准确率上限
    print("\n" + "=" * 76)
    print("反事实：只学 A（不学 B/C），任务 A 的准确率")
    print("=" * 76)
    bA = CorpusCallosum(threshold=0.705, seed=0)
    XtrA, ytrA, _, _ = split_task(Ftr, ytr, TASKS["A"], PER_TASK_TRAIN, PER_TASK_TEST, 0)
    for xi, yi in zip(XtrA, ytrA):
        bA.memorize(xi, yi)
    r = evaluate(bA, XA, yA)
    print(f"  只学 A: acc={r['acc']:.4f}  root={len(bA.right.galaxy)}")
    r_all = evaluate(brain, XA, yA)
    print(f"  学 A+B+C: acc={r_all['acc']:.4f}  root={len(brain.right.galaxy)}")
    print(f"  差距: {r_all['acc'] - r['acc']:+.4f}")

    out = {
        "task_A_confusion": cm,
        "wrong_to_B": wrong_to_B,
        "wrong_to_C": wrong_to_C,
        "nearest_task": {f"{k[0]}_{k[1]}": v for k, v in nearest_task.items()},
        "acc_A_only": r["acc"],
        "acc_after_all": r_all["acc"],
        "drop": r_all["acc"] - r["acc"],
    }
    with open(os.path.join(RESULTS, "diag_e1.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, default=str)
    print(f"\n写入 {os.path.join(RESULTS, 'diag_e1.json')}")


if __name__ == "__main__":
    main()
