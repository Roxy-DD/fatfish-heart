"""
E-13 误差分解：内在难度 / 跨任务干扰 / 类内衰减

核心问题（来自 E-12 复盘）：
  C 作为第一个任务（零遗忘）也只有 0.8560，而 A 有 0.9040。
  → C 的缺口里有多少是"类本身难分"，有多少是"被别的任务抢走"？

四组对照：
  ① ceiling     10 类一次性联合训练，不分任务 → 内在可分性天花板
  ② incremental  A→B→C，预测 unrestricted      → 当前完整表现
  ③ oracle       A→B→C，只在所属任务标签内选   → 剥离跨任务干扰
  ④ within       A→B→C，只在所属任务内训练     → 剥离跨任务训练造成的影响

分解：
  跨任务干扰 = ② − ③
  类内衰减   = ③ − ①
  内在上限   = ①
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
SEEDS = [0, 1, 2, 3, 4]
KW = {"mode": "topk", "topk": 3}


def split(F, y, labels, ntr, nte, seed, cap=None):
    rng = np.random.default_rng(seed)
    idx = np.array([i for i, lab in enumerate(y) if lab in labels])
    rng.shuffle(idx)
    if cap:
        idx = idx[:cap]
    return F[idx[:ntr]], y[idx[:ntr]], F[idx[ntr:ntr + nte]], y[idx[ntr:ntr + nte]]


def build(Ftr, ytr, order, seed, threshold=0.705, per_task=TR):
    b = CorpusCallosum(threshold=threshold, seed=seed)
    chunks, tests = {}, {}
    for name in order:
        Xtr, ytr_t, Xte, yte_t = split(Ftr, ytr, TASKS[name], per_task, TE, seed)
        chunks[name] = (Xtr, ytr_t)
        tests[name] = (Xte, yte_t)
    for name in order:
        Xtr, ytr_t = chunks[name]
        for xi, yi in zip(Xtr, ytr_t):
            b.memorize(xi, yi)
    return b, tests


def acc_restricted(idx: FlatIndex, X, y, allowed: set[str]) -> float:
    """只在 allowed 标签内选最大（oracle 任务标签）"""
    pos = [i for i, u in enumerate(idx.uniq) if u in allowed]
    if len(X) == 0:
        return 0.0
    S = idx.scores(X, **KW)[:, pos]
    pred = np.array([idx.uniq[p] for p in pos], dtype=object)[np.argmax(S, axis=1)]
    return float(np.mean(pred == y))


def main():
    Ftr, ytr, _ = get_features(True, "main", limit=20000, input_size=96)
    Fte, yte, _ = get_features(False, "main", limit=8000, input_size=96)
    t0 = time.time()

    print("=" * 96)
    print("E-13 误差分解（topk3, T=0.705, 5 seeds）")
    print("=" * 96)

    rows = []
    for seed in SEEDS:
        rec = {"seed": seed}

        # ① ceiling：10 类一次性联合训练（每类 900，与增量总样本量对齐）
        ALL = set("0123456789")
        Xtr, ytr_t, Xte, yte_t = split(Ftr, ytr, ALL, 900, 1000, seed, cap=9000)
        b_all = CorpusCallosum(threshold=0.705, seed=seed)
        for xi, yi in zip(Xtr, ytr_t):
            b_all.memorize(xi, yi)
        idx_all = build_index_from_brain(b_all, include_children=True)
        ceiling = {}
        for t, labs in TASKS.items():
            m = np.array([str(l) in labs for l in yte_t])
            if m.sum():
                ceiling[t] = idx_all.accuracy(Xte[m], yte_t[m], **KW)
        rec["ceiling"] = ceiling

        # ② incremental unrestricted
        b, tests = build(Ftr, ytr, ["A", "B", "C"], seed)
        idx = build_index_from_brain(b, include_children=True)
        inc = {t: idx.accuracy(*tests[t], **KW) for t in TASKS}
        rec["incremental"] = inc

        # ③ oracle：只在所属任务标签内
        orc = {t: acc_restricted(idx, *tests[t], TASKS[t]) for t in TASKS}
        rec["oracle"] = orc

        # ④ within：每个任务单独训练、单独评估（无跨任务影响，但样本量与增量相同）
        wit = {}
        for t in TASKS:
            bt, tt = build(Ftr, ytr, [t], seed)
            idxt = build_index_from_brain(bt, include_children=True)
            wit[t] = idxt.accuracy(*tt[t], **KW)
        rec["within_only"] = wit

        rows.append(rec)
        print(f"  seed={seed}  ceiling A/B/C="
              f"{ceiling.get('A', 0):.3f}/{ceiling.get('B', 0):.3f}/{ceiling.get('C', 0):.3f}  "
              f"incremental="
              f"{inc['A']:.3f}/{inc['B']:.3f}/{inc['C']:.3f}  "
              f"oracle="
              f"{orc['A']:.3f}/{orc['B']:.3f}/{orc['C']:.3f}")

    print(f"\n耗时 {time.time()-t0:.0f}s")

    def agg(key, t):
        return np.array([r[key][t] for r in rows])

    print("\n" + "=" * 96)
    print("分解结果（均值 ± std, n=5）")
    print("=" * 96)
    print(f"{'条件':<22}{'A':<16}{'B':<16}{'C':<16}{'macro':<16}")
    print("-" * 96)

    summary = {}
    for cond in ["ceiling", "within_only", "incremental", "oracle"]:
        vals = {}
        for t in ["A", "B", "C"]:
            a = agg(cond, t)
            vals[t] = {"mean": float(a.mean()), "std": float(a.std())}
        vals["macro"] = {"mean": float(np.mean([vals[t]["mean"] for t in "ABC"]))}
        summary[cond] = vals
        print(f"{cond:<22}" + "".join(
            f"{vals[t]['mean']:.3f}±{vals[t]['std']:.3f}  " for t in ["A", "B", "C"]))
        print(f"{'':22}macro={vals['macro']['mean']:.4f}")

    print("-" * 96)

    # 三分分解
    print("\n" + "=" * 96)
    print("误差归因（百分点，正 = 损失）")
    print("=" * 96)
    print(f"{'缺口来源':<26}{'A':<12}{'B':<12}{'C':<12}{'macro':<12}")
    print("-" * 96)
    decomp = {}
    for label, hi, lo in [
        ("内在难度 (1 - ceiling)", "one", "ceiling"),
        ("跨任务干扰 (oracle-incremental)", "oracle", "incremental"),
        ("类内衰减 (ceiling-oracle)", "ceiling", "oracle"),
    ]:
        if label.startswith("内在"):
            vals = {t: (1 - summary["ceiling"][t]["mean"]) * 100 for t in "ABC"}
        elif label.startswith("跨任务"):
            vals = {t: (summary["oracle"][t]["mean"] - summary["incremental"][t]["mean"]) * 100
                    for t in "ABC"}
        else:
            vals = {t: (summary["ceiling"][t]["mean"] - summary["oracle"][t]["mean"]) * 100
                    for t in "ABC"}
        vals["macro"] = float(np.mean([vals[t] for t in "ABC"]))
        decomp[label] = vals
        print(f"{label:<26}" + "".join(f"{vals[t]:<12.2f}" for t in "ABC")
              + f"{vals['macro']:<12.2f}")

    print("-" * 96)

    # 关键判定
    print("\n" + "=" * 96)
    print("判定")
    print("=" * 96)
    c_ceiling = summary["ceiling"]["C"]["mean"]
    c_inc = summary["incremental"]["C"]["mean"]
    c_orc = summary["oracle"]["C"]["mean"]
    a_ceiling = summary["ceiling"]["A"]["mean"]
    a_inc = summary["incremental"]["A"]["mean"]

    print(f"  C 天花板={c_ceiling:.4f}, 增量={c_inc:.4f}, oracle={c_orc:.4f}")
    print(f"  → C 的增量缺口 (1-{c_inc:.4f}) = {100*(1-c_inc):.1f}pp，其中内在难度 "
          f"{100*(1-c_ceiling):.1f}pp 占 {100*(1-c_ceiling)/max(100*(1-c_inc),1e-9):.0%}")
    print(f"  A 天花板={a_ceiling:.4f}, 增量={a_inc:.4f}")

    verdict = {
        "C_ceiling": round(c_ceiling, 4),
        "C_incremental": round(c_inc, 4),
        "C_oracle": round(c_orc, 4),
        "C_intrinsic_share": round(float(100*(1-c_ceiling)/max(100*(1-c_inc), 1e-9)), 3),
        "cross_task_interference_pp": round(float(decomp["跨任务干扰 (oracle-incremental)"]["macro"]), 2),
        "within_task_decay_pp": round(float(decomp["类内衰减 (ceiling-oracle)"]["macro"]), 2),
        "A_ceiling": round(a_ceiling, 4),
    }
    print(f"\n  ★ 跨任务干扰(macro) = {verdict['cross_task_interference_pp']:+.2f}pp")
    print(f"  ★ 类内衰减(macro)   = {verdict['within_task_decay_pp']:+.2f}pp")
    print(f"  ★ C 的内在难度占比   = {verdict['C_intrinsic_share']:.0%}")

    out = {"experiment": "E-13", "seeds": SEEDS, "kwargs": KW,
           "per_task": [TR, TE],
           "summary": summary, "decomposition": decomp,
           "per_seed": rows, "verdict": verdict}
    with open(os.path.join(RESULTS, "e13_decomposition.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e13_decomposition.json')}")


if __name__ == "__main__":
    main()
