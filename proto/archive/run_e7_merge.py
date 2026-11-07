"""
E-7 压缩阈值解耦实验

E-3 发现：用分配阈值 0.705 做梦，1428 → 41 星，准确率 B -12.9% / C -9.1%。
说明**回收阈值不能等于分配阈值**——分配要宽（容忍类内变化），合并要严（只合并重复）。

本实验扫合并阈值，找出「既降熵又不掉准确率」的区间。
"""
from __future__ import annotations

import json
import os
import sys

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_lite"))

import numpy as np

from core import CorpusCallosum, conflict_degree, evaluate, system_entropy
from retina import get_features
from run_e1_e6 import TASKS, split_task, PER_TASK_TRAIN, PER_TASK_TEST

RESULTS = os.path.join(_HERE, "results")


def build_brain(Ftr, ytr):
    brain = CorpusCallosum(threshold=0.705, seed=0)
    for name in ["A", "B", "C"]:
        Xtr, ytr_t, _, _ = split_task(Ftr, ytr, TASKS[name], PER_TASK_TRAIN, PER_TASK_TEST, 0)
        for xi, yi in zip(Xtr, ytr_t):
            brain.memorize(xi, yi)
    return brain


def eval_all(brain, Fte, yte):
    out = {}
    for k, labs in TASKS.items():
        idx = np.array([i for i, lab in enumerate(yte) if lab in labs])
        out[k] = evaluate(brain, Fte[idx], yte[idx])["acc"]
    out["macro"] = float(np.mean([out[k] for k in TASKS]))
    return out


def main():
    Ftr, ytr, _ = get_features(True, "main", limit=20000, input_size=96)
    Fte, yte, _ = get_features(False, "main", limit=8000, input_size=96)

    print("=" * 82)
    print("E-7 合并阈值解耦（分配阈值固定 0.705，只扫 dream 阈值）")
    print("=" * 82)

    brain0 = build_brain(Ftr, ytr)
    acc0 = eval_all(brain0, Fte, yte)
    se0, cd0 = system_entropy(brain0.right.galaxy), conflict_degree(brain0.right.galaxy)
    n0 = len(brain0.all_stars())
    print(f"压缩前: 星={n0} SE={se0:.4f} CD={cd0:.4f}")
    print(f"        acc A={acc0['A']:.4f} B={acc0['B']:.4f} C={acc0['C']:.4f} macro={acc0['macro']:.4f}")
    print()
    print(f"{'合并T':<8}{'星数':<8}{'SE':<10}{'ΔSE%':<9}{'CD':<10}{'accA':<8}{'accB':<8}{'accC':<8}{'macro':<8}{'Δmacro':<9}")
    print("-" * 82)

    rows = []
    for T in [0.80, 0.85, 0.90, 0.93, 0.95, 0.97, 0.98, 0.99, 0.995]:
        brain = build_brain(Ftr, ytr)
        brain.dream(threshold=T)
        se, cd = system_entropy(brain.right.galaxy), conflict_degree(brain.right.galaxy)
        n = len(brain.all_stars())
        acc = eval_all(brain, Fte, yte)
        dse = (se - se0) / se0 * 100 if se0 else 0
        dacc = acc["macro"] - acc0["macro"]
        print(f"{T:<8.3f}{n:<8d}{se:<10.4f}{dse:<+9.1f}{cd:<10.4f}"
              f"{acc['A']:<8.4f}{acc['B']:<8.4f}{acc['C']:<8.4f}{acc['macro']:<8.4f}{dacc:<+9.4f}")
        rows.append({"T": T, "stars": n, "SE": se, "CD": cd, "dSE_pct": dse,
                     "acc": acc, "dmacro": dacc})

    print("-" * 82)

    # 找最优：dmacro >= -0.01 且 SE 降幅最大
    ok = [r for r in rows if r["dmacro"] >= -0.01]
    best = min(ok, key=lambda r: r["SE"]) if ok else None
    print()
    if best:
        print(f"★ 最优合并阈值 T={best['T']:.3f}: "
              f"星数 {n0}→{best['stars']} (压掉 {100*(1-best['stars']/n0):.1f}%), "
              f"SE {best['dSE_pct']:+.1f}%, macro {best['dmacro']:+.4f}")
        print("  判据（E-3 修正版）: 准确率不降的前提下最大化降熵")
    else:
        print("★ 无阈值能同时满足降熵与不降准确率")

    # 诊断：为什么 0.705 掉得那么狠
    print("\n" + "=" * 82)
    print("为什么 0.705 掉得狠：合并后的标签纯度")
    print("=" * 82)
    brain = build_brain(Ftr, ytr)
    for T in [0.705, 0.95]:
        b = build_brain(Ftr, ytr)
        before = len(b.all_stars())
        b.dream(threshold=T)
        # 检查合并是否跨标签（cross_label_merge=False 时应不跨）
        labels = [s.label for s in b.right.galaxy]
        print(f"  T={T:.3f}: 星 {before}→{len(b.all_stars())}, "
              f"root {len(b.right.galaxy)}, 标签种类 {len(set(labels))}")

    out = {"before": {"stars": n0, "SE": se0, "CD": cd0, "acc": acc0},
           "sweep": rows, "best": best,
           "note": "分配阈值 0.705 来自 E-0；合并阈值必须独立标定"}
    with open(os.path.join(RESULTS, "e7_merge_threshold.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e7_merge_threshold.json')}")


if __name__ == "__main__":
    main()
