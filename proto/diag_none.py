"""
诊断第二层：为什么 perceive() 返回 None？

core.py CorpusCallosum.perceive() 的返回逻辑：
    if star is not None and r_score >= l_score:  return star.label
    if l_label is not None:                      return l_label
    if star is not None:                         return star.label
    return None, 0.0, info

只有当 star is None 且 l_label is None 时才返回 None。
star 来自 right.perceive()，pool 非空时永不返回 None。

所以 None 只可能来自：**右脑 galaxy 为空**？不可能（411 颗）。
→ 说明是别的路径。逐条打印实际分支。
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_lite"))

import numpy as np
from core import CorpusCallosum, cos, evaluate, normalize
from retina import get_features
from run_e1_e6 import TASKS, split_task, PER_TASK_TRAIN, PER_TASK_TEST


def main():
    Ftr, ytr, _ = get_features(True, "main", limit=20000, input_size=96)
    Fte, yte, _ = get_features(False, "main", limit=8000, input_size=96)

    brain = CorpusCallosum(threshold=0.705, seed=0)
    for name in ["A", "B", "C"]:
        Xtr, ytr_t, Xte, yte_t = split_task(Ftr, ytr, TASKS[name], PER_TASK_TRAIN, PER_TASK_TEST, 0)
        for xi, yi in zip(Xtr, ytr_t):
            brain.memorize(xi, yi)
        if name == "A":
            XA, yA = Xte, yte_t

    print("=" * 76)
    print("逐样本追踪（任务 A 测试集前 10 条）")
    print("=" * 76)
    for i in range(10):
        xi, yi = XA[i], yA[i]
        star, r_sim = brain.right.perceive(xi)
        l_label, l_conf = brain.left.perceive(xi)
        r_score = r_sim * brain.dominance
        l_score = l_conf * (1 - brain.dominance)
        pred, sim, info = brain.perceive(xi)
        print(f"[{i}] 真={yi!r}")
        print(f"     right: star={'None' if star is None else repr(star.label)} "
              f"sim={r_sim:.4f} r_score={r_score:.4f}")
        print(f"     left : label={l_label!r} conf={l_conf:.2f} l_score={l_score:.4f}")
        print(f"     dom={brain.dominance:.4f}  → pred={pred!r} winner={info['winner']}")

    # 统计 None 时各分支状态
    print("\n" + "=" * 76)
    print("全部 A 测试集：None 预测的成因统计")
    print("=" * 76)
    n_none = 0
    n_none_star_none = 0
    n_none_left_none = 0
    n_right_win = 0
    n_left_win = 0
    null_sims = []
    for xi, yi in zip(XA, yA):
        star, r_sim = brain.right.perceive(xi)
        l_label, l_conf = brain.left.perceive(xi)
        pred, sim, info = brain.perceive(xi)
        if pred is None:
            n_none += 1
            if star is None:
                n_none_star_none += 1
                null_sims.append(r_sim)
            if l_label is None:
                n_none_left_none += 1
        else:
            if info["winner"] == "right":
                n_right_win += 1
            elif info["winner"] == "left":
                n_left_win += 1

    print(f"  预测为 None: {n_none}/{len(XA)}")
    print(f"  其中 star is None: {n_none_star_none}")
    print(f"  其中 left label is None: {n_none_left_none}")
    print(f"  非 None 中: right 赢 {n_right_win}, left 赢 {n_left_win}")
    if null_sims:
        print(f"  star=None 时的 r_sim: min={min(null_sims):.4f} max={max(null_sims):.4f}")

    # 检查所有星的标签集合
    print("\n" + "=" * 76)
    print("库内标签集合检查")
    print("=" * 76)
    root_labels = [s.label for s in brain.right.galaxy]
    all_labels = [s.label for s in brain.all_stars()]
    print(f"  root 标签种类: {sorted(set(root_labels))}")
    print(f"  全部标签种类: {sorted(set(all_labels))}")
    print(f"  标签是否含空字符串: {'' in all_labels}")
    print(f"  标签类型: {set(type(x).__name__ for x in all_labels)}")

    # 关键：右脑 perceive 在递归时的行为
    print("\n" + "=" * 76)
    print("右脑 perceive 递归行为检查（子池是否返回 None）")
    print("=" * 76)
    cats = [s for s in brain.right.galaxy if s.children]
    print(f"  有子星的类目数: {len(cats)}")
    for c in cats[:3]:
        print(f"    类目 label={c.label!r} mass={c.mass} 子星数={len(c.children)}")
        # 递归池子是否可能为空
        if len(c.children) == 0:
            print("      ⚠ 子池为空！")

    # 空子池会导致 perceive 递归返回 (None, 0.0)
    empty_children = [s for s in brain.right.galaxy if s.children == []]
    print(f"  子星数为 0 的类目: {len(empty_children)}")


if __name__ == "__main__":
    main()
