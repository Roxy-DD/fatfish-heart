"""
E-10 消融：为什么基线从 0.6767 → 0.8467？

唯一差异：
  E-1 (core.perceive)    = 层级递归：先全局找 root，若 sim>T 且是类目，递归进子池
  E-9 (Infer, mode=max)  = 平坦：直接在全部 1428 颗星上取全局 max

若"平坦 > 层级"，则原设计的层级递归**损害**了准确率。
这是对设计意图第 5 条（质量计数→分裂→层级）的直接检验。

同时测：
  root_only     只用 411 根星
  flat_all      用 1428 全部星（平坦）
  hier_recursive 递归（= core.perceive）
"""
from __future__ import annotations

import json
import os
import sys

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_lite"))

import numpy as np

from core import CorpusCallosum, cos
from retina import get_features

RESULTS = os.path.join(_HERE, "results")
TASKS = {"A": {"0", "1", "2"}, "B": {"3", "4", "5"}, "C": {"6", "7", "8", "9"}}
TR, TE = 900, 300


def split(F, y, labels, ntr, nte, seed=0):
    rng = np.random.default_rng(seed)
    idx = np.array([i for i, lab in enumerate(y) if lab in labels])
    rng.shuffle(idx)
    return F[idx[:ntr]], y[idx[:ntr]], F[idx[ntr:ntr + nte]], y[idx[ntr:ntr + nte]]


def build(Ftr, ytr, threshold=0.705):
    b = CorpusCallosum(threshold=threshold, seed=0)
    tests = {}
    for name in ["A", "B", "C"]:
        Xtr, ytr_t, Xte, yte_t = split(Ftr, ytr, TASKS[name], TR, TE)
        tests[name] = (Xte, yte_t)
        for xi, yi in zip(Xtr, ytr_t):
            b.memorize(xi, yi)
    return b, tests


def eval_flat(brain, X, y, pool="all"):
    stars = brain.right.galaxy if pool == "root" else brain.all_stars()
    M = np.array([s.vector for s in stars], dtype=np.float32)
    labs = [s.label for s in stars]
    c = 0
    for xi, yi in zip(X, y):
        i = int(np.argmax(M @ xi))
        c += int(labs[i] == yi)
    return c / len(X)


def eval_hier(brain, X, y):
    c = 0
    for xi, yi in zip(X, y):
        s, _ = brain.right.perceive(xi)
        c += int((s.label if s else None) == yi)
    return c / len(X)


def eval_topk(brain, X, y, k=3, pool="all"):
    """在多星上做类内 top-k 均值（类 = label），不用层级"""
    stars = brain.right.galaxy if pool == "root" else brain.all_stars()
    groups = {}
    for s in stars:
        groups.setdefault(s.label, []).append(s.vector)
    labels = list(groups)
    mats = {k: np.array(v, dtype=np.float32) for k, v in groups.items()}
    c = 0
    for xi, yi in zip(X, y):
        best_k, best = None, -2.0
        for k2 in labels:
            v = mats[k2] @ xi
            kk = min(k, len(v))
            sc = float(np.mean(np.partition(v, -kk)[-kk:]))
            if sc > best:
                best, best_k = sc, k2
        c += int(best_k == yi)
    return c / len(X)


def main():
    Ftr, ytr, _ = get_features(True, "main", limit=20000, input_size=96)
    Fte, yte, _ = get_features(False, "main", limit=8000, input_size=96)
    brain, tests = build(Ftr, ytr)

    print("=" * 92)
    print("E-10 消融：层级递归 vs 平坦，谁更好？")
    print("=" * 92)
    print(f"root={len(brain.right.galaxy)}  all={len(brain.all_stars())}  "
          f"类目数={sum(1 for s in brain.right.galaxy if s.children)}")

    rows = []
    print(f"\n{'检索方式':<28}{'A':<9}{'B':<9}{'C':<9}{'macro':<9}")
    print("-" * 92)

    methods = [
        ("hier_recursive (原实现)", eval_hier),
        ("flat_root_only (411根)", lambda b, X, y: eval_flat(b, X, y, "root")),
        ("flat_all_stars (1428)", lambda b, X, y: eval_flat(b, X, y, "all")),
        ("topk3_all_stars", lambda b, X, y: eval_topk(b, X, y, 3, "all")),
        ("topk3_root_only", lambda b, X, y: eval_topk(b, X, y, 3, "root")),
        ("topk5_all_stars", lambda b, X, y: eval_topk(b, X, y, 5, "all")),
    ]
    for name, fn in methods:
        A, B, C = fn(brain, *tests["A"]), fn(brain, *tests["B"]), fn(brain, *tests["C"])
        macro = (A + B + C) / 3
        print(f"{name:<28}{A:<9.4f}{B:<9.4f}{C:<9.4f}{macro:<9.4f}")
        rows.append({"method": name, "A": round(A, 4), "B": round(B, 4),
                     "C": round(C, 4), "macro": round(macro, 4)})

    print("-" * 92)
    hier = rows[0]
    flat = rows[2]
    print(f"\n关键对比：")
    print(f"  层级递归 A={hier['A']:.4f}  vs  平坦全星 A={flat['A']:.4f}  "
          f"→ 差距 {flat['A']-hier['A']:+.4f}")
    print(f"  层级递归 macro={hier['macro']:.4f}  vs  平坦 macro={flat['macro']:.4f}  "
          f"→ {flat['macro']-hier['macro']:+.4f}")

    # 为什么层级差？统计递归时子池是否常常给出更高的相似度
    print("\n递归行为诊断：")
    subs = [s for s in brain.right.galaxy if s.children]
    print(f"  类目数: {len(subs)}, 平均子星数: {np.mean([len(s.children) for s in subs]):.1f}")
    print(f"  子星总数: {sum(len(s.children) for s in subs)}")

    # 根星与子星是否确实不同（子星是否是 root 的"细分"）
    root_ids = {id(s) for s in brain.right.galaxy}
    child_stars = [s for s in brain.all_stars() if id(s) not in root_ids]
    if child_stars:
        print(f"  子星数: {len(child_stars)}")
        # 子星与其父星的相似度
        sims_pc = []
        for r in subs:
            for c in r.children:
                sims_pc.append(cos(r.vector, c.vector))
        print(f"  子星 vs 父星 相似度: 均值={np.mean(sims_pc):.4f} "
              f"min={min(sims_pc):.4f} max={max(sims_pc):.4f}")
        print(f"  → 阈值 T=0.705; 子星若与父星很接近，说明分裂只是复制")
    # 关键：根星 vs 子星 的向量是否需要区分
    print("\n结构观察：")
    print("  递归逻辑：若 root 的 sim>T 且该 root 有子星，则只在子池里搜")
    print("  → 若 root 命中错误概念，子池搜索被**锁死**在错误子树内")
    print("  → 平坦检索没有这个锁死风险")

    out = {"experiment": "E-10", "ablation": "hierarchical vs flat",
           "n_root": len(brain.right.galaxy), "n_all": len(brain.all_stars()),
           "n_categories": sum(1 for s in brain.right.galaxy if s.children),
           "results": rows,
           "verdict": {"flat_minus_hier_A": round(flat["A"] - hier["A"], 4),
                       "flat_minus_hier_macro": round(flat["macro"] - hier["macro"], 4),
                       "hierarchy_hurts": bool(flat["macro"] > hier["macro"])}}
    with open(os.path.join(RESULTS, "e10_ablation.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e10_ablation.json')}")


if __name__ == "__main__":
    main()
