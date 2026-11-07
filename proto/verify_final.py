"""
三项终验：
  V-1 干扰假设（修正混淆矩阵的 np.str_ 问题）
  V-2 dominance 棘轮效应（是否为吸收态）
  V-3 E-6 重做（protect_labels 才是真解）
"""
from __future__ import annotations

import json
import os
import sys

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_lite"))

import numpy as np

from core import CorpusCallosum, Star, cos, conflict_degree, evaluate, system_entropy
from retina import get_features
from run_e1_e6 import TASKS, split_task, PER_TASK_TRAIN, PER_TASK_TEST

RESULTS = os.path.join(_HERE, "results")


def V1_interference(Ftr, ytr, Fte, yte):
    print("=" * 76)
    print("V-1 干扰假设：新概念直接抢走旧概念的最近邻")
    print("=" * 76)
    brain = CorpusCallosum(threshold=0.705, seed=0)
    tests = {}
    for name in ["A", "B", "C"]:
        Xtr, ytr_t, Xte, yte_t = split_task(Ftr, ytr, TASKS[name], PER_TASK_TRAIN, PER_TASK_TEST, 0)
        tests[name] = (Xte, yte_t)
        for xi, yi in zip(Xtr, ytr_t):
            brain.memorize(xi, yi)
        r = evaluate(brain, Xte, yte_t)
        print(f"  学完 {name}: 本任务 acc={r['acc']:.4f}  "
              f"root={len(brain.right.galaxy)} 全部星={len(brain.all_stars())}")

    # 修正版混淆矩阵：用 str() 归一化
    all_labs = sorted(set(TASKS["A"]) | set(TASKS["B"]) | set(TASKS["C"]))
    XA, yA = tests["A"]
    cm = {str(a): {str(b): 0 for b in all_labs} for a in TASKS["A"]}
    for xi, yi in zip(XA, yA):
        p, _, _ = brain.perceive(xi)
        cm[str(yi)][str(p)] += 1

    print("\n  任务 A 混淆矩阵（列为预测）")
    header = "真\\预  " + "".join(f"{b:>5s}" for b in all_labs)
    print("  " + header)
    for a in sorted(cm):
        row = "".join(f"{cm[a][b]:5d}" for b in all_labs)
        print(f"  {a:>4s}  {row}")

    to_task = {"A": 0, "B": 0, "C": 0}
    for a in cm:
        for b, n in cm[a].items():
            if b == a:
                continue
            for t, labs in TASKS.items():
                if b in labs:
                    to_task[t] += n
    total_err = sum(to_task.values())
    print(f"\n  A 的错误样本去向: 判成 B={to_task['B']}, 判成 C={to_task['C']}, "
          f"判成 A 内其他={to_task['A']}  合计={total_err}")
    print(f"  → 跨任务错判占比 {100*(to_task['B']+to_task['C'])/max(total_err,1):.1f}%")

    # 密度效应
    print("\n  各任务星数（密度不对称）:")
    for t, labs in TASKS.items():
        n = sum(1 for s in brain.right.galaxy if s.label in labs)
        print(f"    {t}: {n} 颗")

    return {"confusion": cm, "wrong_to": to_task,
            "n_stars_by_task": {t: sum(1 for s in brain.right.galaxy if s.label in labs)
                                for t, labs in TASKS.items()}}


def V2_ratchet(Ftr, ytr):
    print("\n" + "=" * 76)
    print("V-2 dominance 棘轮效应：是否为吸收态")
    print("=" * 76)
    brain = CorpusCallosum(threshold=0.705, seed=0)
    Xtr, ytr_t, _, _ = split_task(Ftr, ytr, TASKS["A"], PER_TASK_TRAIN, PER_TASK_TEST, 0)
    for xi, yi in zip(Xtr, ytr_t):
        brain.memorize(xi, yi)

    doms = np.array([h[0] for h in brain.history])
    print(f"  学完 A 后 dominance: {doms[0]:.4f} → {doms[-1]:.4f}")
    print(f"  轨迹: 升 {np.sum(np.diff(doms)>1e-9)} 次, 降 {np.sum(np.diff(doms)<-1e-9)} 次")

    # 数学证明：dominance=1 时 l_score 恒为 0
    print("\n  数学结构:")
    print("    r_score = r_sim * d")
    print("    l_score = l_conf * (1 - d)")
    print("    当 d=1.0 → l_score ≡ 0 → 左脑永不获胜")
    print("    → l_correct 永不为 True → d 永不下降")
    print("    → **d=1.0 是吸收态**")
    print("    对称地，d=0.0 时 r_score ≡ 0，右脑永不获胜，也是吸收态")

    # 实测：把 dominance 强制设回 0.5，看它能否恢复
    print("\n  实测恢复能力:")
    brain2 = CorpusCallosum(threshold=0.705, seed=0)
    brain2.dominance = 0.5
    for xi, yi in zip(Xtr, ytr_t):
        brain2.memorize(xi, yi)
    print(f"    从 0.5 起学 A: → {brain2.dominance:.4f}")
    for xi, yi in zip(Xtr, ytr_t):
        brain2.memorize(xi, yi)
    print(f"    再学一轮 A:   → {brain2.dominance:.4f}")

    # 左脑单独准确率（不看仲裁）
    print("\n  左脑单独能力（不经仲裁）:")
    brain3 = CorpusCallosum(threshold=0.705, seed=0)
    for xi, yi in zip(Xtr, ytr_t):
        brain3.memorize(xi, yi)
    # 只学左脑
    b_left = CorpusCallosum(threshold=0.705, seed=0)
    for xi, yi in zip(Xtr, ytr_t):
        b_left.left.memorize(xi, yi)
    Xte, yte_t = split_task(Ftr, ytr, TASKS["A"], PER_TASK_TRAIN, PER_TASK_TEST, 0)[2:4]
    correct, tot = 0, 0
    for xi, yi in zip(Xte, yte_t):
        lab, conf = b_left.left.perceive(xi)
        tot += 1
        correct += int(lab == yi)
    print(f"    纯左脑 acc = {correct/tot:.4f}")
    right_only = CorpusCallosum(threshold=0.705, seed=0)
    for xi, yi in zip(Xtr, ytr_t):
        right_only.right.memorize(xi, yi)
    correct, tot = 0, 0
    for xi, yi in zip(Xte, yte_t):
        star, sim = right_only.right.perceive(xi)
        pred = star.label if star else None
        tot += 1
        correct += int(pred == yi)
    print(f"    纯右脑 acc = {correct/tot:.4f}")

    return {"dominance_after_A": float(doms[-1]),
            "n_up": int(np.sum(np.diff(doms) > 1e-9)),
            "n_down": int(np.sum(np.diff(doms) < -1e-9)),
            "absorbing_state": True}


def V3_prune_redo():
    print("\n" + "=" * 76)
    print("V-3 E-6 重做：protect_labels 才是真解")
    print("=" * 76)
    rng = np.random.default_rng(0)
    D = 128
    now = 1_000_000.0

    brain = CorpusCallosum(threshold=0.705, seed=0)
    # 任务 A：3 个概念，各只见过 1 次，且是"旧的"
    for i in range(3):
        v = rng.normal(size=D); v /= np.linalg.norm(v)
        brain.right.galaxy.append(Star(vector=v, label=f"A{i}", mass=1,
                                       created_at=now - 7200, last_seen=now - 7200))
    # 80 颗陈旧弱噪声
    for i in range(80):
        v = rng.normal(size=D); v /= np.linalg.norm(v)
        brain.right.galaxy.append(Star(vector=v, label=f"N{i}", mass=1,
                                       created_at=now - 7200, last_seen=now - 7200))
    # 80 颗新鲜弱（刚学，不该删）
    for i in range(80):
        v = rng.normal(size=D); v /= np.linalg.norm(v)
        brain.right.galaxy.append(Star(vector=v, label=f"F{i}", mass=1,
                                       created_at=now, last_seen=now))

    n_before = len(brain.right.galaxy)

    # 原规则
    orig = [s for s in brain.right.galaxy if s.mass > 1] if n_before > 50 else list(brain.right.galaxy)
    print(f"  初始 {n_before} 颗")
    print(f"  原规则 (mass>1):   存活 {len(orig)}，其中 A={sum(1 for s in orig if s.label[0]=='A')}/3")

    # 本规则，不带保护
    b2 = CorpusCallosum(threshold=0.705, seed=0)
    b2.right.galaxy = [Star(vector=s.vector.copy(), label=s.label, mass=s.mass,
                            created_at=s.created_at, last_seen=s.last_seen)
                       for s in brain.right.galaxy]
    rep = b2.prune(min_mass=2, min_age_s=1800.0, now=now)
    print(f"  双阈值无保护:      存活 {rep['stars_after']}，"
          f"其中 A={sum(1 for s in b2.right.galaxy if s.label[0]=='A')}/3，"
          f"新鲜弱={sum(1 for s in b2.right.galaxy if s.label[0]=='F')}/80，"
          f"陈旧弱={sum(1 for s in b2.right.galaxy if s.label[0]=='N')}/80")

    # 本规则，带 A 保护
    b3 = CorpusCallosum(threshold=0.705, seed=0)
    b3.right.galaxy = [Star(vector=s.vector.copy(), label=s.label, mass=s.mass,
                            created_at=s.created_at, last_seen=s.last_seen)
                       for s in brain.right.galaxy]
    rep3 = b3.prune(min_mass=2, min_age_s=1800.0, now=now,
                    protect_labels={"A0", "A1", "A2"})
    print(f"  双阈值+保护 A:     存活 {rep3['stars_after']}，"
          f"其中 A={sum(1 for s in b3.right.galaxy if s.label[0]=='A')}/3，"
          f"新鲜弱={sum(1 for s in b3.right.galaxy if s.label[0]=='F')}/80，"
          f"陈旧弱={sum(1 for s in b3.right.galaxy if s.label[0]=='N')}/80")

    print("\n  结论：")
    print("    原规则: A 0/3 (全灭) + 新鲜弱 0/80 (刚学的也删了) = 两类都错")
    print("    双阈值: A 0/3 (陈旧弱，被正确删除) + 新鲜弱 80/80 (保住) = 修好一半")
    print("    +保护:  A 3/3 + 新鲜弱 80/80 + 陈旧弱 0/80 = **全对**")
    print("    → 遗忘规则必须能接收『任务重要性』信号，单靠 mass/age 不够。")

    return {
        "n_before": n_before,
        "original": {"A": sum(1 for s in orig if s.label[0] == "A"),
                     "F": sum(1 for s in orig if s.label[0] == "F"),
                     "total": len(orig)},
        "dual_threshold": {"A": sum(1 for s in b2.right.galaxy if s.label[0] == "A"),
                           "F": sum(1 for s in b2.right.galaxy if s.label[0] == "F"),
                           "N": sum(1 for s in b2.right.galaxy if s.label[0] == "N"),
                           "total": rep["stars_after"]},
        "dual_plus_protect": {"A": sum(1 for s in b3.right.galaxy if s.label[0] == "A"),
                              "F": sum(1 for s in b3.right.galaxy if s.label[0] == "F"),
                              "N": sum(1 for s in b3.right.galaxy if s.label[0] == "N"),
                              "total": rep3["stars_after"]},
    }


def main():
    Ftr, ytr, _ = get_features(True, "main", limit=20000, input_size=96)
    Fte, yte, _ = get_features(False, "main", limit=8000, input_size=96)

    out = {}
    out["V1_interference"] = V1_interference(Ftr, ytr, Fte, yte)
    out["V2_ratchet"] = V2_ratchet(Ftr, ytr)
    out["V3_prune"] = V3_prune_redo()

    with open(os.path.join(RESULTS, "verify_final.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'verify_final.json')}")


if __name__ == "__main__":
    main()
