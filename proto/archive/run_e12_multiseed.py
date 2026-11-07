"""
E-12 稳定版：多种子 × 序列顺序 × 推理变体

目的：
  E-9 的 0.8933 只有 seed=0 一个点。差 0.67pp 到 90%，必须先确认这是信号还是噪声。
  同时测序列顺序（A→B→C vs C→B→A），因为"后学的任务更吃亏"可能是顺序假象。

设计：
  seeds        = 0..4（5 个）
  orders       = ABC, CBA（2 个）
  variants     = max, topk3, topk3_mix0.5, topk3_evt, topk5_evt
  threshold    = 0.705
  每任务       训练 900 / 测试 300

输出：均值 ± 标准差，以及每个变体是否显著超过基线。
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
from evalkit import FlatIndex, build_index_from_brain
from retina import get_features

RESULTS = os.path.join(_HERE, "results")
TASKS = {"A": {"0", "1", "2"}, "B": {"3", "4", "5"}, "C": {"6", "7", "8", "9"}}
TR, TE = 900, 300


def split(F, y, labels, ntr, nte, seed):
    rng = np.random.default_rng(seed)
    idx = np.array([i for i, lab in enumerate(y) if lab in labels])
    rng.shuffle(idx)
    return F[idx[:ntr]], y[idx[:ntr]], F[idx[ntr:ntr + nte]], y[idx[ntr:ntr + nte]]


def build_sequence(Ftr, ytr, order: list[str], seed: int, threshold=0.705):
    """按 order 依次学习，返回 (brain, tests, first_task_test)"""
    brain = CorpusCallosum(threshold=threshold, seed=seed)
    tests = {}
    chunks = {}
    for name in order:
        Xtr, ytr_t, Xte, yte_t = split(Ftr, ytr, TASKS[name], TR, TE, seed)
        chunks[name] = (Xtr, ytr_t)
        tests[name] = (Xte, yte_t)
    for name in order:
        Xtr, ytr_t = chunks[name]
        for xi, yi in zip(Xtr, ytr_t):
            brain.memorize(xi, yi)
    return brain, tests


VARIANTS = [
    ("max", {"mode": "max"}),
    ("topk3", {"mode": "topk", "topk": 3}),
    ("topk5", {"mode": "topk", "topk": 5}),
    ("topk3_mix.5", {"mode": "topk", "topk": 3, "mix": 0.5}),
    ("topk3_evt.3", {"mode": "topk", "topk": 3, "evt_beta": 0.3}),
    ("topk3_evt1.0", {"mode": "topk", "topk": 3, "evt_beta": 1.0}),
    ("max_evt1.0", {"mode": "max", "evt_beta": 1.0}),
]


def main():
    Ftr, ytr, _ = get_features(True, "main", limit=20000, input_size=96)
    Fte, yte, _ = get_features(False, "main", limit=8000, input_size=96)

    seeds = [0, 1, 2, 3, 4]
    orders = [("ABC", ["A", "B", "C"]), ("CBA", ["C", "B", "A"])]

    print("=" * 104)
    print("E-12 多种子 × 序列顺序 × 推理变体")
    print("=" * 104)

    records = []
    t0 = time.time()
    for order_name, order in orders:
        for seed in seeds:
            brain, tests = build_sequence(Ftr, ytr, order, seed)
            idx = build_index_from_brain(brain, include_children=True)
            first_task = order[0]

            for vname, kw in VARIANTS:
                acc = {}
                for t in ["A", "B", "C"]:
                    acc[t] = idx.accuracy(*tests[t], **kw)
                acc["macro"] = (acc["A"] + acc["B"] + acc["C"]) / 3
                records.append({
                    "order": order_name, "seed": seed, "variant": vname,
                    "A": round(acc["A"], 4), "B": round(acc["B"], 4), "C": round(acc["C"], 4),
                    "macro": round(acc["macro"], 4),
                    "first_task": first_task,
                    "acc_first_task": round(acc[first_task], 4),
                    "n_stars": idx.M.shape[0],
                    "kwargs": kw,
                })
            print(f"  [{order_name} seed={seed}] stars={idx.M.shape[0]} "
                  f"max_A={records[-len(VARIANTS)]['A']:.4f} "
                  f"topk3_A={records[-len(VARIANTS)+1]['A']:.4f}")
    print(f"\n总耗时 {time.time()-t0:.1f}s，{len(records)} 条记录")

    # 汇总
    print("\n" + "=" * 104)
    print("汇总：均值 ± 标准差（5 seeds × 2 orders = 10 次）")
    print("=" * 104)
    print(f"{'变体':<16}{'A 均值':<12}{'A std':<9}{'B 均值':<12}{'C 均值':<12}{'macro':<12}{'std':<9}")
    print("-" * 104)

    summary = {}
    for vname, kw in VARIANTS:
        rs = [r for r in records if r["variant"] == vname]
        A = np.array([r["A"] for r in rs])
        B = np.array([r["B"] for r in rs])
        C = np.array([r["C"] for r in rs])
        M = np.array([r["macro"] for r in rs])
        summary[vname] = {
            "A_mean": float(A.mean()), "A_std": float(A.std()),
            "B_mean": float(B.mean()), "C_mean": float(C.mean()),
            "macro_mean": float(M.mean()), "macro_std": float(M.std()),
            "A_min": float(A.min()), "A_max": float(A.max()),
            "n": len(rs),
        }
        print(f"{vname:<16}{A.mean():<12.4f}{A.std():<9.4f}{B.mean():<12.4f}"
              f"{C.mean():<12.4f}{M.mean():<12.4f}{M.std():<9.4f}")

    print("-" * 104)
    base = summary["max"]
    best = max(summary.items(), key=lambda kv: kv[1]["A_mean"])
    print(f"\n基线 max:      A={base['A_mean']:.4f} ± {base['A_std']:.4f}  "
          f"macro={base['macro_mean']:.4f} ± {base['macro_std']:.4f}")
    print(f"最优 {best[0]:<12} A={best[1]['A_mean']:.4f} ± {best[1]['A_std']:.4f}  "
          f"macro={best[1]['macro_mean']:.4f}")
    diff = best[1]["A_mean"] - base["A_mean"]
    pooled_std = np.sqrt(best[1]["A_std"] ** 2 + base["A_std"] ** 2)
    print(f"\n改善 {diff:+.4f}，合并标准差 {pooled_std:.4f}，"
          f"效应量 {diff/pooled_std if pooled_std else 0:+.2f} σ")

    # 判据：A 均值是否 > 0.90 且 A_min 也 > 0.85
    print("\n" + "=" * 104)
    print("判据检验（A > 0.90）")
    print("=" * 104)
    for vname, s in sorted(summary.items(), key=lambda kv: -kv[1]["A_mean"]):
        ok_mean = s["A_mean"] > 0.90
        ok_min = s["A_min"] > 0.85
        print(f"  {vname:<16} A_mean={s['A_mean']:.4f} ({'通过' if ok_mean else '未过'})  "
              f"A_min={s['A_min']:.4f} ({'稳定' if ok_min else '不稳'})  "
              f"range=[{s['A_min']:.3f}, {s['A_max']:.3f}]")

    # 顺序效应
    print("\n" + "=" * 104)
    print("顺序效应：后学的任务是否更吃亏")
    print("=" * 104)
    for order_name, _ in orders:
        rs = [r for r in records if r["variant"] == "topk3" and r["order"] == order_name]
        print(f"  顺序 {order_name}: 第一个任务 acc={np.mean([r['acc_first_task'] for r in rs]):.4f}, "
              f"A={np.mean([r['A'] for r in rs]):.4f}, "
              f"B={np.mean([r['B'] for r in rs]):.4f}, "
              f"C={np.mean([r['C'] for r in rs]):.4f}")

    out = {"experiment": "E-12", "seeds": seeds,
           "orders": [o[0] for o in orders], "threshold": 0.705,
           "per_task": [TR, TE],
           "summary": summary,
           "records": records,
           "best": {"variant": best[0], **best[1],
                    "diff_vs_base": round(diff, 4),
                    "effect_size_sigma": round(float(diff / pooled_std), 3) if pooled_std else None}}
    with open(os.path.join(RESULTS, "e12_multiseed.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e12_multiseed.json')}")


if __name__ == "__main__":
    main()
