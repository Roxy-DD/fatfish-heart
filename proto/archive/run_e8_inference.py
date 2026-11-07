"""
E-8 迭代实验一：只改推理规则，能否修复最近邻侵占？

科学设计：学习过程（分配/合并/分裂）完全不变，只换推理规则。
若某变体把 A 拉回 90%，则侵占是"推理缺陷"，不需重建记忆层。
若全都拉不回，则侵占是"表征层/分配层缺陷"，必须改结构。

变体：
  max       基线（原实现）
  topk      k=1,3,5 类内 top-k 均值
  density   alpha = 0.5,1.0,2.0  惩罚类内星数
  centroid  类质心余弦
  zscore    类内相对突出度
"""
from __future__ import annotations

import json
import os
import sys

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_lite"))

import numpy as np

from retina import get_features
from variant import VBrain

RESULTS = os.path.join(_HERE, "results")
TASKS = {"A": {"0", "1", "2"}, "B": {"3", "4", "5"}, "C": {"6", "7", "8", "9"}}
TR, TE = 900, 300


def split(F, y, labels, n_train, n_test, seed=0):
    rng = np.random.default_rng(seed)
    idx = np.array([i for i, lab in enumerate(y) if lab in labels])
    rng.shuffle(idx)
    return F[idx[:n_train]], y[idx[:n_train]], F[idx[n_train:n_train + n_test]], y[idx[n_train:n_train + n_test]]


def run(variant_kwargs: dict, Ftr, ytr, Fte, yte, threshold=0.705):
    """跑 A→B→C，返回每阶段的保留率。"""
    b = VBrain(Ftr.shape[1], threshold=threshold, **variant_kwargs)
    tests = {}
    stage = {}
    for name in ["A", "B", "C"]:
        Xtr, ytr_t, Xte, yte_t = split(Ftr, ytr, TASKS[name], TR, TE)
        tests[name] = (Xte, yte_t)
        for xi, yi in zip(Xtr, ytr_t):
            b.memorize(xi, yi)
        ret = {k: b.evaluate(*v) for k, v in tests.items()}
        stage[name] = {"retention": ret, "n_stars": b.g.n,
                       "created": b.n_created, "reinforced": b.n_reinforced,
                       "mitosis": b.n_mitosis}
    return b, stage


def main():
    Ftr, ytr, _ = get_features(True, "main", limit=20000, input_size=96)
    Fte, yte, _ = get_features(False, "main", limit=8000, input_size=96)

    variants = [("max", {})]
    for k in (1, 3, 5):
        variants.append((f"topk{k}", {"mode": "topk", "topk": k}))
    for a in (0.5, 1.0, 2.0):
        variants.append((f"den{a}", {"mode": "density", "alpha": a}))
    variants.append(("centroid", {"mode": "centroid"}))
    variants.append(("zscore", {"mode": "zscore"}))

    print("=" * 96)
    print("E-8 只改推理：能否修复侵占？（阈值 0.705，学习过程完全一致）")
    print("=" * 96)
    print(f"{'变体':<10}{'学完A后A':<10}{'学完C后A':<10}{'学完C后B':<10}{'学完C后C':<10}{'macro':<9}{'星数':<8}{'ΔA':<9}")
    print("-" * 96)

    rows = []
    base_A = None
    for name, kw in variants:
        try:
            b, stage = run(kw, Ftr, ytr, Fte, yte)
        except Exception as e:
            print(f"{name:<10} ERROR {type(e).__name__}: {e}")
            continue
        A0 = stage["A"]["retention"]["A"]
        final = stage["C"]["retention"]
        macro = float(np.mean([final["A"], final["B"], final["C"]]))
        if name == "max":
            base_A = final["A"]
        dA = final["A"] - (base_A if base_A is not None else final["A"])
        print(f"{name:<10}{A0:<10.4f}{final['A']:<10.4f}{final['B']:<10.4f}"
              f"{final['C']:<10.4f}{macro:<9.4f}{b.g.n:<8d}{dA:<+9.4f}")
        rows.append({"variant": name, "kwargs": kw,
                     "A_after_A": round(A0, 4),
                     "A_final": round(final["A"], 4),
                     "B_final": round(final["B"], 4),
                     "C_final": round(final["C"], 4),
                     "macro": round(macro, 4),
                     "n_stars": b.g.n,
                     "created": b.n_created, "reinforced": b.n_reinforced,
                     "mitosis": b.n_mitosis,
                     "delta_A_vs_max": round(dA, 4)})

    print("-" * 96)
    best = max(rows, key=lambda r: r["A_final"])
    print(f"\n★ A 保留率最高: {best['variant']}  A={best['A_final']:.4f}  "
          f"macro={best['macro']:.4f}")
    print(f"  基线 max: A={base_A:.4f}")
    print(f"  判据要求: A > 0.90 → {'达成' if best['A_final'] > 0.90 else '未达成'}")

    # 最优的也用 macro 看
    best_macro = max(rows, key=lambda r: r["macro"])
    print(f"★ macro 最高: {best_macro['variant']}  macro={best_macro['macro']:.4f}  "
          f"(A={best_macro['A_final']:.4f})")

    out = {"experiment": "E-8", "threshold": 0.705,
           "data": {"train": list(Ftr.shape), "test": list(Fte.shape), "per_task": [TR, TE]},
           "variants": rows, "best_by_A": best, "best_by_macro": best_macro,
           "verdict": {"A_above_90": bool(best["A_final"] > 0.90),
                       "best_A": best["A_final"], "baseline_A": base_A}}
    with open(os.path.join(RESULTS, "e8_inference_variants.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e8_inference_variants.json')}")


if __name__ == "__main__":
    main()
