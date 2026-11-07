"""
E-11 终极诊断：为什么"平坦全星"比"根星/层级"好 17 个百分点？

两个已确认事实：
  flat_root_only = hier_recursive = 0.6767   （层级里，子星用不上）
  flat_all_stars            = 0.8467          （全星平坦，+17pp）
  子星 vs 父星 相似度均值 = 0.9148

推论：在 core.py 里，子星只是"同标签的向量副本"，但**数量翻了 3.6 倍**
      （411 root + 1017 children）。平坦检索时，每个概念拥有的"候选票"变多，
      而 top-k 聚合把票数优势转化成了准确率。

但要注意一个公平性问题：
  hier/flat_root 只用了 411 个向量，flat_all 用了 1428 个向量。
  这是**信息量不同**，不是"检索方式"不同！

必须做的公平对照：
  A. 只给 411 根星（原状）
  B. 给 411 root + 1017 children（全星）
  C. 给 411 root + 1017 个**随机复制**（不用真实子星，只增加数量）
     → 若 C 也涨，说明涨的是"数量/投票"效应，不是"子星内容"效应

这才是干净的因果分离。
"""
from __future__ import annotations

import json
import os
import sys

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_lite"))

import numpy as np

from core import CorpusCallosum
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


def eval_groups(groups: dict, X, y, k: int = 1):
    """groups: {label: [vectors]}；类内 top-k 均值"""
    mats = {k: (np.array(v, dtype=np.float32) if len(v) else np.zeros((1, len(X[0])), np.float32))
            for k, v in groups.items()}
    c = 0
    for xi, yi in zip(X, y):
        best_k, best = None, -2.0
        for lab, M in mats.items():
            v = M @ xi
            kk = min(k, len(v))
            sc = float(np.mean(np.partition(v, -kk)[-kk:]))
            if sc > best:
                best, best_k = sc, lab
        c += int(best_k == yi)
    return c / len(X)


def main():
    Ftr, ytr, _ = get_features(True, "main", limit=20000, input_size=96)
    Fte, yte, _ = get_features(False, "main", limit=8000, input_size=96)
    brain, tests = build(Ftr, ytr)
    rng = np.random.default_rng(42)

    roots = brain.right.galaxy
    root_ids = {id(s) for s in roots}
    children = [s for s in brain.all_stars() if id(s) not in root_ids]

    # 分组
    g_root, g_all = {}, {}
    for s in roots:
        g_root.setdefault(s.label, []).append(s.vector)
        g_all.setdefault(s.label, []).append(s.vector)
    for s in children:
        g_all.setdefault(s.label, []).append(s.vector)

    # C: 给每个 root 随机复制，副本数 = 该类真实子星数（模拟"仅增加数量"）
    g_dup = {}
    n_child_by_label = {lab: 0 for lab in g_root}
    for s in children:
        n_child_by_label[s.label] = n_child_by_label.get(s.label, 0) + 1
    for lab, vecs in g_root.items():
        g_dup[lab] = list(vecs)
        for _ in range(n_child_by_label.get(lab, 0)):
            g_dup[lab].append(vecs[rng.integers(len(vecs))].copy())

    # D: 给每个 root 加同分布噪声（不是复制，是扰动）
    g_noise = {}
    for lab, vecs in g_root.items():
        g_noise[lab] = list(vecs)
        for _ in range(n_child_by_label.get(lab, 0)):
            v = vecs[rng.integers(len(vecs))].copy()
            v = v + rng.normal(0, 0.05, v.shape)
            n = np.linalg.norm(v)
            g_noise[lab].append(v / n if n > 0 else v)

    print("=" * 100)
    print("E-11 因果分离：17pp 来自'子星内容'还是'候选数量'？")
    print("=" * 100)
    print(f"root={sum(len(v) for v in g_root.values())}  "
          f"children={sum(len(v) for v in g_all.values())-sum(len(v) for v in g_root.values())}  "
          f"all={sum(len(v) for v in g_all.values())}")

    setups = [
        ("root_only        (411)", g_root),
        ("all_stars        (1428)", g_all),
        ("root+随机复制    (1428)", g_dup),
        ("root+噪声扰动    (1428)", g_noise),
    ]

    rows = []
    for k in (1, 3, 5):
        print(f"\n--- 类内 top-{k} ---")
        print(f"{'设置':<26}{'A':<9}{'B':<9}{'C':<9}{'macro':<9}")
        print("-" * 100)
        for name, g in setups:
            A, B, C = eval_groups(g, *tests["A"], k=k), eval_groups(g, *tests["B"], k=k), eval_groups(g, *tests["C"], k=k)
            macro = (A + B + C) / 3
            print(f"{name:<26}{A:<9.4f}{B:<9.4f}{C:<9.4f}{macro:<9.4f}")
            rows.append({"k": k, "setup": name, "A": round(A, 4), "B": round(B, 4),
                         "C": round(C, 4), "macro": round(macro, 4)})

    print("-" * 100)

    # 关键判定
    def get(k, key):
        return next(r for r in rows if r["k"] == k and r["setup"].startswith(key))

    print("\n" + "=" * 100)
    print("因果判定（k=3）")
    print("=" * 100)
    r_only = get(3, "root_only")
    r_all = get(3, "all_stars")
    r_dup = get(3, "root+随机复制")
    r_noi = get(3, "root+噪声扰动")
    print(f"  仅 411 根星        : A={r_only['A']:.4f}  macro={r_only['macro']:.4f}")
    print(f"  + 真实子星 (1428)  : A={r_all['A']:.4f}  macro={r_all['macro']:.4f}  "
          f"→ {r_all['macro']-r_only['macro']:+.4f}")
    print(f"  + 随机复制 (1428)  : A={r_dup['A']:.4f}  macro={r_dup['macro']:.4f}  "
          f"→ {r_dup['macro']-r_only['macro']:+.4f}")
    print(f"  + 噪声扰动 (1428)  : A={r_noi['A']:.4f}  macro={r_noi['macro']:.4f}  "
          f"→ {r_noi['macro']-r_only['macro']:+.4f}")
    print()
    gain_real = r_all["macro"] - r_only["macro"]
    gain_dup = r_dup["macro"] - r_only["macro"]
    gain_noise = r_noi["macro"] - r_only["macro"]
    print(f"  真实子星增益 {gain_real:+.4f}  随机复制增益 {gain_dup:+.4f}  "
          f"噪声增益 {gain_noise:+.4f}")
    if abs(gain_real - gain_dup) < 0.02:
        verdict = "增益主要来自**候选数量**（投票效应），子星内容贡献很小"
    elif gain_real > gain_dup + 0.02:
        verdict = "真实子星确实提供了**额外信息**（不只是数量）"
    else:
        verdict = "子星内容带来了**负面**贡献（数量有好处但内容有害）"
    print(f"\n  ★ 判定：{verdict}")

    out = {"experiment": "E-11", "n_root": sum(len(v) for v in g_root.values()),
           "n_all": sum(len(v) for v in g_all.values()),
           "rows": rows,
           "verdict": {"gain_real": round(gain_real, 4), "gain_dup": round(gain_dup, 4),
                       "gain_noise": round(gain_noise, 4), "conclusion": verdict}}
    with open(os.path.join(RESULTS, "e11_causal.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e11_causal.json')}")


if __name__ == "__main__":
    main()
