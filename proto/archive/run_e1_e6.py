"""
E-1 增量学习 + E-3 压缩 + E-4 迁移 + E-5 双脑轨迹 + E-6 修剪误杀
一次性跑完，输出结构化 JSON。

判据（docs/04 试验设计.md）：
  E-1 学完 3 任务后，任务 1 准确率 > 90%
  E-3 做梦后 SE 降 > 50%，CD 降 > 30%
  E-4 导出/重载后预测一致率 = 100%
  E-5 dominance 双向变化都 > 0
  E-6 任务关键星存活率 = 100%
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_lite"))

import numpy as np

from core import (CorpusCallosum, Star, conflict_degree, evaluate,
                  system_entropy)
from retina import get_features

RESULTS = os.path.join(_HERE, "results")
WORK = os.path.join(_HERE, "work")
os.makedirs(RESULTS, exist_ok=True)
os.makedirs(WORK, exist_ok=True)

# 任务切分：A=0,1,2  B=3,4,5  C=6,7,8,9
TASKS = {
    "A": {"0", "1", "2"},
    "B": {"3", "4", "5"},
    "C": {"6", "7", "8", "9"},
}
PER_TASK_TRAIN = 900      # 每任务 900 张
PER_TASK_TEST = 300       # 每任务 300 张


def split_task(F, y, labels, n_train, n_test, seed=0):
    rng = np.random.default_rng(seed)
    idx = np.array([i for i, lab in enumerate(y) if lab in labels])
    rng.shuffle(idx)
    tr = idx[:n_train]
    te = idx[n_train:n_train + n_test]
    return F[tr], y[tr], F[te], y[te]


def load_all():
    Ftr, ytr, _ = get_features(True, "main", limit=20000, input_size=96)
    Fte, yte, _ = get_features(False, "main", limit=8000, input_size=96)
    return Ftr, ytr, Fte, yte


# ---------------------------------------------------------------------------
# E-1 / E-3 / E-5：顺序学习三个任务
# ---------------------------------------------------------------------------

def run_e1(Ftr, ytr, Fte, yte, threshold: float, cross_label: bool = False,
           prune_mode: str = "dual", seed: int = 0) -> dict:
    brain = CorpusCallosum(threshold=threshold, cross_label_merge=cross_label, seed=seed)
    log = {"threshold": threshold, "cross_label": cross_label,
           "prune_mode": prune_mode, "seed": seed, "tasks": []}

    seen_test = {}          # 累积测试集（用于"旧任务是否还记住"）
    t_start = time.time()

    for task_name in ["A", "B", "C"]:
        labels = TASKS[task_name]
        Xtr, ytr_t, Xte, yte_t = split_task(Ftr, ytr, labels, PER_TASK_TRAIN, PER_TASK_TEST, seed)
        seen_test[task_name] = (Xte, yte_t)

        # --- 学习 ---
        t0 = time.time()
        for xi, yi in zip(Xtr, ytr_t):
            brain.memorize(xi, yi)
        learn_s = time.time() - t0

        # --- 当前任务准确率 ---
        r_cur = evaluate(brain, Xte, yte_t)

        # --- 回测所有已学任务（灾难性遗忘的核心指标）---
        retention = {}
        for k, (Xk, yk) in seen_test.items():
            retention[k] = evaluate(brain, Xk, yk)["acc"]

        # --- 状态 ---
        n_stars = len(brain.all_stars())
        se = system_entropy(brain.right.galaxy)
        cd = conflict_degree(brain.right.galaxy)

        rec = {
            "task": task_name,
            "learn_seconds": round(learn_s, 2),
            "acc_current": round(r_cur["acc"], 4),
            "acc_per_label": {k: round(v, 4) for k, v in r_cur["per_label"].items()},
            "retention": {k: round(v, 4) for k, v in retention.items()},
            "n_stars": n_stars,
            "n_root": len(brain.right.galaxy),
            "SE": round(se, 6),
            "CD": round(cd, 6),
            "dominance": round(brain.dominance, 4),
            "counters": {"created": brain.right.n_created,
                         "reinforced": brain.right.n_reinforced,
                         "mitosis": brain.right.n_mitosis},
        }
        log["tasks"].append(rec)
        print(f"  [{task_name}] acc={rec['acc_current']:.4f} "
              f"retention={ {k: f'{v:.4f}' for k,v in retention.items()} } "
              f"stars={n_stars}(root {rec['n_root']}) SE={se:.4f} CD={cd:.4f} "
              f"dom={brain.dominance:.3f} {learn_s:.1f}s")

    log["total_seconds"] = round(time.time() - t_start, 2)
    log["final"] = {
        "acc_task_A": log["tasks"][2]["retention"].get("A", 0.0),
        "acc_task_B": log["tasks"][2]["retention"].get("B", 0.0),
        "acc_task_C": log["tasks"][2]["acc_current"],
        "n_stars": len(brain.all_stars()),
        "SE": system_entropy(brain.right.galaxy),
        "CD": conflict_degree(brain.right.galaxy),
    }

    # E-5: dominance 轨迹
    hist = np.array([h[0] for h in brain.history]) if brain.history else np.array([0.5])
    delta = np.diff(hist) if len(hist) > 1 else np.array([0.0])
    log["dominance_trace"] = {
        "start": float(hist[0]),
        "end": float(hist[-1]),
        "min": float(hist.min()),
        "max": float(hist.max()),
        "n_increase": int(np.sum(delta > 1e-9)),
        "n_decrease": int(np.sum(delta < -1e-9)),
        "bidirectional": bool(np.sum(delta > 1e-9) > 0 and np.sum(delta < -1e-9) > 0),
    }

    return brain, log


# ---------------------------------------------------------------------------
# E-3：压缩（做梦）
# ---------------------------------------------------------------------------

def run_e3(brain: CorpusCallosum, Fte, yte, noise_on_threshold: float = 0.0,
           seed: int = 0) -> dict:
    rng = np.random.default_rng(seed)
    se0 = system_entropy(brain.right.galaxy)
    cd0 = conflict_degree(brain.right.galaxy)
    n0 = len(brain.all_stars())

    acc_before = {}
    for k, labels in TASKS.items():
        idx = np.array([i for i, lab in enumerate(yte) if lab in labels])
        if len(idx):
            acc_before[k] = evaluate(brain, Fte[idx], yte[idx])["acc"]

    rep = brain.dream(noise_on_threshold=noise_on_threshold, rng=rng)

    se1 = system_entropy(brain.right.galaxy)
    cd1 = conflict_degree(brain.right.galaxy)
    n1 = len(brain.all_stars())

    acc_after = {}
    for k, labels in TASKS.items():
        idx = np.array([i for i, lab in enumerate(yte) if lab in labels])
        if len(idx):
            acc_after[k] = evaluate(brain, Fte[idx], yte[idx])["acc"]

    return {
        "dream_report": rep,
        "stars_before": n0, "stars_after": n1,
        "SE_before": round(se0, 6), "SE_after": round(se1, 6),
        "SE_change_pct": round((se1 - se0) / se0 * 100, 2) if se0 else None,
        "CD_before": round(cd0, 6), "CD_after": round(cd1, 6),
        "CD_change_pct": round((cd1 - cd0) / cd0 * 100, 2) if cd0 else None,
        "acc_before": {k: round(v, 4) for k, v in acc_before.items()},
        "acc_after": {k: round(v, 4) for k, v in acc_after.items()},
        "acc_delta": {k: round(acc_after[k] - acc_before[k], 4)
                      for k in acc_before if k in acc_after},
        "noise_on_threshold": noise_on_threshold,
    }


# ---------------------------------------------------------------------------
# E-4：迁移（跨进程）
# ---------------------------------------------------------------------------

def run_e4(brain: CorpusCallosum, Fte, yte, tag: str = "e4") -> dict:
    base = os.path.join(WORK, f"{tag}_brain.json")
    info = brain.save(base, extra={"purpose": "E-4 migration test"})

    # 记录 A 实例的预测（全部测试集，固定顺序）
    preds_a, sims_a = [], []
    for xi in Fte:
        p, s, _ = brain.perceive(xi)
        preds_a.append(p)
        sims_a.append(round(float(s), 6))

    # 子进程重载并预测（真正的跨进程，不共享内存）
    script = os.path.join(WORK, "_reload_and_predict.py")
    with open(script, "w", encoding="utf-8") as f:
        f.write(f'''
import os, sys, json
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
sys.path.insert(0, r"{os.path.join(_HERE, 'cosmos_lite')}")
import numpy as np
from core import CorpusCallosum
from retina import get_features

brain, meta = CorpusCallosum.load(r"{base}")
Fte, yte, _ = get_features(False, "main", limit=8000, input_size=96)

preds, sims = [], []
for xi in Fte:
    p, s, _ = brain.perceive(xi)
    preds.append(p)
    sims.append(round(float(s), 6))

out = {{
    "n_stars": len(brain.all_stars()),
    "dominance": brain.dominance,
    "preds": preds,
    "sims": sims,
    "left_knowledge_labels": sorted(brain.left.knowledge.keys()),
    "schema": meta["schema"],
}}
with open(r"{os.path.join(WORK, tag + '_reload.json')}", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False)
print("reload done", out["n_stars"], len(preds))
''')

    env = os.environ.copy()
    env["KMP_DUPLICATE_LIB_OK"] = "TRUE"
    proc = subprocess.run([sys.executable, script], capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        return {"error": "子进程失败", "stderr": proc.stderr[-2000:], "stdout": proc.stdout[-1000:]}

    with open(os.path.join(WORK, tag + "_reload.json"), "r", encoding="utf-8") as f:
        reloaded = json.load(f)

    preds_b = reloaded["preds"]
    sims_b = reloaded["sims"]

    match_pred = sum(1 for a, b in zip(preds_a, preds_b) if a == b)
    max_sim_diff = max(abs(a - b) for a, b in zip(sims_a, sims_b)) if sims_a else 0.0

    return {
        "saved": info,
        "n_stars_A": brain.all_stars().__len__(),
        "n_stars_B": reloaded["n_stars"],
        "n_predictions": len(preds_a),
        "pred_match": match_pred,
        "pred_match_rate": round(match_pred / len(preds_a), 6) if preds_a else None,
        "max_sim_diff": max_sim_diff,
        "dominance_A": brain.dominance,
        "dominance_B": reloaded["dominance"],
        "left_knowledge_recovered": len(reloaded["left_knowledge_labels"]),
        "schema": reloaded["schema"],
        "child_process_ok": True,
    }


# ---------------------------------------------------------------------------
# E-6：修剪误杀对照
# ---------------------------------------------------------------------------

def run_e6(threshold: float, seed: int = 0) -> dict:
    """
    构造：任务 A 的记忆"新鲜但弱"（mass=1），另有大量"老旧且弱"的噪声星。
    原实现 dream() 的修剪：if start_count > 50: keep mass > 1  → A 全灭。
    本实现 prune()：只在 weak AND stale 时删 → A 应存活。
    """
    rng = np.random.default_rng(seed)
    D = 128

    # 场景：任务 A 的 3 个概念，各只见过 1 次
    brain = CorpusCallosum(threshold=threshold, seed=seed)
    centers = {f"A{i}": rng.normal(size=D) for i in range(3)}
    now = time.time()
    for name, c in centers.items():
        x = c / np.linalg.norm(c)
        s = Star(vector=x, label=name, mass=1, created_at=now, last_seen=now)
        brain.right.galaxy.append(s)

    # 时间快进：A 的记忆变"旧"
    fast_forward = 3600.0
    for s in brain.right.galaxy:
        s.last_seen = now - fast_forward

    # 混入 80 颗"陈旧且弱"的噪声
    for i in range(80):
        v = rng.normal(size=D); v /= np.linalg.norm(v)
        brain.right.galaxy.append(Star(vector=v, label=f"N{i}", mass=1,
                                       created_at=now - 7200, last_seen=now - 7200))
    # 再混入 80 颗"新鲜但弱"的（刚学的，不该删）
    for i in range(80):
        v = rng.normal(size=D); v /= np.linalg.norm(v)
        brain.right.galaxy.append(Star(vector=v, label=f"F{i}", mass=1,
                                       created_at=now, last_seen=now))

    before_labels = {s.label for s in brain.right.galaxy}
    n_before = len(brain.right.galaxy)

    # 原实现规则（复现缺陷 B）
    orig_killed = 0
    for s in list(brain.right.galaxy):
        pass
    orig_alive = [s for s in brain.right.galaxy if s.mass > 1] if n_before > 50 else list(brain.right.galaxy)
    orig_A_alive = sum(1 for s in orig_alive if s.label.startswith("A"))

    # 本实现：双阈值
    rep = brain.prune(min_mass=2, min_age_s=1800.0, now=now)
    kept_labels = {s.label for s in brain.right.galaxy}
    A_alive = sum(1 for s in brain.right.galaxy if s.label.startswith("A"))
    F_alive = sum(1 for s in brain.right.galaxy if s.label.startswith("F"))
    N_alive = sum(1 for s in brain.right.galaxy if s.label.startswith("N"))

    return {
        "scenario": {"A_concepts": 3, "fresh_weak": 80, "stale_weak": 80},
        "n_before": n_before,
        "original_rule": {
            "rule": "if start_count > 50: keep mass > 1",
            "A_survived": orig_A_alive,
            "A_survival_rate": round(orig_A_alive / 3, 4),
            "total_survived": len(orig_alive),
        },
        "fixed_rule": {
            "rule": "prune only if mass < min_mass AND age >= min_age_s",
            "report": rep,
            "A_survived": A_alive,
            "A_survival_rate": round(A_alive / 3, 4),
            "fresh_weak_survived": F_alive,
            "stale_weak_survived": N_alive,
            "stale_weak_pruned": 80 - N_alive,
        },
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    run_id = time.strftime("%Y%m%d_%H%M%S")
    Ftr, ytr, Fte, yte = load_all()
    print(f"数据: train={Ftr.shape} test={Fte.shape}")

    calibrated = 0.705      # 来自 E-0
    default = 0.85          # 原实现硬编码值

    out = {"run_id": run_id, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "dataset": {"train": list(Ftr.shape), "test": list(Fte.shape),
                       "per_task_train": PER_TASK_TRAIN, "per_task_test": PER_TASK_TEST},
           "thresholds": {"calibrated": calibrated, "original_default": default}}

    print("\n" + "=" * 76)
    print(f"[E-1] 增量学习，阈值={calibrated}（E-0 标定值）")
    print("=" * 76)
    brain, e1 = run_e1(Ftr, ytr, Fte, yte, threshold=calibrated)
    out["E-1_incremental_calibrated"] = e1

    print("\n" + "=" * 76)
    print(f"[E-3] 压缩（做梦），噪声作用于阈值")
    print("=" * 76)
    e3 = run_e3(brain, Fte, yte)
    print(f"  SE {e3['SE_before']:.4f} → {e3['SE_after']:.4f} ({e3['SE_change_pct']:+.1f}%)")
    print(f"  CD {e3['CD_before']:.4f} → {e3['CD_after']:.4f} ({e3['CD_change_pct']:+.1f}%)")
    print(f"  星数 {e3['stars_before']} → {e3['stars_after']}")
    print(f"  准确率变化 {e3['acc_delta']}")
    out["E-3_dream"] = e3

    print("\n" + "=" * 76)
    print("[E-4] 迁移（跨进程重载）")
    print("=" * 76)
    e4 = run_e4(brain, Fte, yte)
    if "error" in e4:
        print(f"  [FAIL] {e4['error']}")
    else:
        print(f"  星数 {e4['n_stars_A']} → {e4['n_stars_B']}")
        print(f"  预测一致 {e4['pred_match']}/{e4['n_predictions']} = {e4['pred_match_rate']}")
        print(f"  dominance {e4['dominance_A']:.6f} → {e4['dominance_B']:.6f}")
    out["E-4_migration"] = e4

    print("\n" + "=" * 76)
    print("[E-5] 双脑 dominance 轨迹（已含在 E-1 记录中）")
    print("=" * 76)
    print(f"  {e1['dominance_trace']}")
    out["E-5_dominance"] = e1["dominance_trace"]

    print("\n" + "=" * 76)
    print("[E-6] 修剪误杀对照")
    print("=" * 76)
    e6 = run_e6(calibrated)
    print(f"  原规则 A 存活 {e6['original_rule']['A_survived']}/3 "
          f"({e6['original_rule']['A_survival_rate']:.0%})")
    print(f"  新规则 A 存活 {e6['fixed_rule']['A_survived']}/3 "
          f"({e6['fixed_rule']['A_survival_rate']:.0%})，"
          f"新鲜弱记忆存活 {e6['fixed_rule']['fresh_weak_survived']}/80，"
          f"陈旧弱记忆剪掉 {e6['fixed_rule']['stale_weak_pruned']}/80")
    out["E-6_pruning"] = e6

    # 对照组：原默认阈值 0.85
    print("\n" + "=" * 76)
    print(f"[对照] 同流程，阈值={default}（原实现硬编码值）")
    print("=" * 76)
    _, e1_default = run_e1(Ftr, ytr, Fte, yte, threshold=default)
    out["control_default_threshold"] = e1_default

    path = os.path.join(RESULTS, f"e1_e6_{run_id}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n结果: {path}")
    return out


if __name__ == "__main__":
    main()

