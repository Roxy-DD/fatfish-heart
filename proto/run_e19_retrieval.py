"""
E-19 检索次数是否代理「重要性」

背景（用户观察 2026-10-25，原话）：
  · "记忆的强度其实是看重复了多少次、用了多少次，而不是看它有多重要"
  · "我在记忆它的时候，就已经重复它很多遍了"
  · "刻骨铭心的回忆，是因为不断的回忆，不断地让你重复"
  · "对于遗忘的东西，当你再次重复的时候，记忆会很强烈"

推论：**不需要外部 importance 标签。重要性通过「被重复/被使用」自然表现出来。**

这直接挑战我在 E-6 / docs/06 里的结论：
  "只靠 mass/age 判不了重要性 —— 信息不足，任何规则都无法区分"

用户的反驳成立之处：mass/age 确实不够，但**「使用次数」是第三个内生量，而它一直没被记录**。
代码事实：`mass` 只在学习时 +1，**被检索命中的次数从未记录**。

检验设计（避免循环论证）：
  不能用「检索次数多」定义重要、再用检索次数剪枝（那是自证）。
  改用**独立的准确率损失**衡量：
    分别剪掉「高检索组」与「低检索组」，看谁剪掉后准确率掉得多。
  若高检索组掉得多 → 检索次数真的携带重要性信息。
  对照组：按 mass 分组、按 age 分组，做同样的检验。

不预设门槛。只回答：三个指标里，哪个剪掉后损失最大。
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
from retina import get_features

RESULTS = os.path.join(_HERE, "results")
TASKS = {"A": {"0", "1", "2"}, "B": {"3", "4", "5"}, "C": {"6", "7", "8", "9"}}
TR, TE = 900, 300


def l2(v):
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    n[n == 0] = 1.0
    return v / n


def split(F, y, labels, ntr, nte, seed):
    rng = np.random.default_rng(seed)
    idx = np.array([i for i, lab in enumerate(y) if lab in labels])
    rng.shuffle(idx)
    return F[idx[:ntr]], y[idx[:ntr]], F[idx[ntr:ntr + nte]], y[idx[ntr:ntr + nte]]


def learn_counting(Ftr, ytr, seed, tf):
    """
    学习 A→B→C。每次学习前先做一次「回忆」（平坦最近邻），
    命中的星 +1 —— 这就是「被使用一次」。评测不更新。
    """
    b = CorpusCallosum(threshold=0.705, seed=seed)
    rc: dict[int, int] = {}
    tests = {}
    for name in "ABC":
        Xtr, ytr_t, Xte, yte_t = split(Ftr, ytr, TASKS[name], TR, TE, seed)
        tests[name] = (Xte, yte_t)
        Xtr_t = tf(Xtr)
        stars, M = [], None
        for j, (xi, yi) in enumerate(zip(Xtr_t, ytr_t)):
            if j % 50 == 0:
                stars = b.all_stars()
                M = np.array([s.vector for s in stars], np.float32) if stars else None
            if M is not None and len(M):
                sim = M @ xi
                k = int(np.argmax(sim))
                if sim[k] > 0:
                    sid = id(stars[k])
                    rc[sid] = rc.get(sid, 0) + 1
            b.memorize(xi, yi)
    return b, rc, tests


def eval_subset(stars, tf, tests, exclude):
    """用平坦类内 top-3 评估；exclude 是 id(star) 集合。"""
    keep = [s for s in stars if id(s) not in exclude]
    if not keep:
        return {"A": 0.0, "B": 0.0, "C": 0.0, "macro": 0.0}
    M = np.array([s.vector for s in keep], np.float32)
    labels = np.array([str(s.label) for s in keep], dtype=object)
    uniq = sorted(set(labels.tolist()))
    lid = np.array([uniq.index(l) for l in labels], np.int32)
    masks = [np.where(lid == i)[0] for i in range(len(uniq))]
    uniq_arr = np.array(uniq, dtype=object)

    out = {}
    for t in "ABC":
        Xte, yte_t = tests[t]
        Xt = tf(Xte)
        S = Xt @ M.T
        sc = np.empty((len(Xt), len(uniq)), np.float32)
        for i, idx in enumerate(masks):
            v = S[:, idx]
            kk = min(3, v.shape[1])
            sc[:, i] = np.partition(v, -kk, axis=1)[:, -kk:].mean(axis=1)
        pred = uniq_arr[np.argmax(sc, axis=1)]
        y = np.array([str(v) for v in yte_t], dtype=object)
        out[t] = float(np.mean(pred == y))
    out["macro"] = float(np.mean([out[t] for t in "ABC"]))
    return out


def main():
    Ftr, ytr, _ = get_features(True, "main", limit=20000, input_size=96)
    Fte, yte, _ = get_features(False, "main", limit=8000, input_size=96)
    mu = Ftr[:9000].mean(axis=0)
    sd = Ftr[:9000].std(axis=0) + 1e-6
    zsc = lambda F: l2((F - mu) / sd)
    raw = lambda F: F

    seeds = [0, 1, 2]
    t0 = time.time()

    print("=" * 100)
    print("E-19 检索次数 / mass / age：哪个携带「重要性」信息？")
    print("=" * 100)

    all_rows = []
    for tfname, tf in [("raw", raw), ("zsc", zsc)]:
        for seed in seeds:
            b, rc, tests = learn_counting(Ftr, ytr, seed, tf)
            stars = b.all_stars()
            n = len(stars)

            counts = np.array([rc.get(id(s), 0) for s in stars], dtype=np.int32)
            masses = np.array([s.mass for s in stars], dtype=np.int32)
            ages = np.array([time.time() - s.last_seen for s in stars], dtype=np.float64)

            base = eval_subset(stars, tf, tests, set())

            # 分布特征
            def desc(a, name):
                nz = int((a > 0).sum())
                return (f"{name}: 非零 {nz}/{len(a)} ({100*nz/len(a):.0f}%), "
                        f"均值 {a.mean():.2f}, 最大 {a.max():.0f}, "
                        f"p50={np.percentile(a,50):.1f} p90={np.percentile(a,90):.1f}")

            print(f"\n--- {tfname} seed={seed} 星数={n} 基线macro={base['macro']:.4f} ---")
            print(f"  {desc(counts,'检索次数')}")
            print(f"  {desc(masses,'mass    ')}")
            print(f"  {desc(ages,  'age(秒) ')}")

            # 对每个指标，剪掉数量严格相同的「高」「低」两组
            # 修正上版缺陷：percentile 在离散值上会剪掉远超 10% 的星（mass=1 占多数），
            # 导致"剪得多所以掉得多"的伪相关。此处固定 n_cut。
            n_cut = max(1, int(round(n * 0.10)))
            rows = []
            for metric_name, arr in [("retrieval", counts), ("mass", masses), ("age", ages)]:
                order = np.argsort(arr, kind="stable")     # 小→大
                low_idx = order[:n_cut]
                high_idx = order[-n_cut:]
                low_ids = {id(stars[i]) for i in low_idx}
                high_ids = {id(stars[i]) for i in high_idx}
                # 真·同数量
                assert len(low_ids) == n_cut or len(low_ids) <= n_cut
                # 报告被剪掉的指标值区间与并列情况
                tie_low = int((arr == arr[low_idx[-1]]).sum())
                tie_high = int((arr == arr[high_idx[0]]).sum())

                r_high = eval_subset(stars, tf, tests, high_ids)
                r_low = eval_subset(stars, tf, tests, low_ids)

                d_high = r_high["macro"] - base["macro"]
                d_low = r_low["macro"] - base["macro"]

                rows.append({
                    "transform": tfname, "seed": seed, "metric": metric_name,
                    "n_cut": n_cut, "tie_at_low_boundary": tie_low,
                    "tie_at_high_boundary": tie_high,
                    "cut_high_macro": round(r_high["macro"], 4),
                    "cut_low_macro": round(r_low["macro"], 4),
                    "delta_cut_high": round(float(d_high), 4),
                    "delta_cut_low": round(float(d_low), 4),
                    "importance_gap": round(float(d_low - d_high), 4),
                    "base_macro": round(base["macro"], 4),
                })
                print(f"  {metric_name:<10} 各剪{n_cut:4d}颗 | "
                      f"剪高→{r_high['macro']:.4f}({d_high:+.4f})  "
                      f"剪低→{r_low['macro']:.4f}({d_low:+.4f})  "
                      f"别差={d_low-d_high:+.4f}  "
                      f"(边界并列: 低{int((arr==arr[low_idx[-1]]).sum())}颗/高{int((arr==arr[high_idx[0]]).sum())}颗)")

            all_rows.extend(rows)

    print(f"\n耗时 {time.time()-t0:.0f}s")

    # 汇总
    print("\n" + "=" * 100)
    print("汇总：同一指标下，剪高 vs 剪低的平均准确率变化（6 次配置）")
    print("=" * 100)
    print(f"{'指标':<12}{'剪高后Δ':<14}{'剪低后Δ':<14}{'重要性别差':<14}{'判读':<20}")
    print("-" * 100)
    summary = {}
    for metric in ["retrieval", "mass", "age"]:
        rs = [r for r in all_rows if r["metric"] == metric]
        dh = float(np.mean([r["delta_cut_high"] for r in rs]))
        dl = float(np.mean([r["delta_cut_low"] for r in rs]))
        gap = dl - dh
        if gap > 0.005:
            verdict = "携带重要性信息"
        elif gap > 0.001:
            verdict = "弱信息"
        else:
            verdict = "几乎无信息"
        summary[metric] = {"delta_cut_high": round(dh, 4), "delta_cut_low": round(dl, 4),
                           "importance_gap": round(gap, 4), "verdict": verdict}
        print(f"{metric:<12}{dh:<14.4f}{dl:<14.4f}{gap:<14.4f}{verdict:<20}")
    print("-" * 100)
    print("\n判读说明：")
    print("  delta_cut_high = 剪掉该指标最高的 10% 后，macro 变化（负 = 掉分）")
    print("  delta_cut_low  = 剪掉该指标最低的 10% 后，macro 变化")
    print("  重要性别差 = delta_cut_low − delta_cut_high")
    print("    差值为正且大 → 剪低分的几乎不掉、剪高分的掉得多 → 该指标能分辨重要")
    print("    差值为 0    → 剪谁都一样 → 该指标无区分力")

    best = max(summary.items(), key=lambda kv: kv[1]["importance_gap"])
    print(f"\n★ 区分力最强的指标: {best[0]} (差距 {best[1]['importance_gap']:+.4f})")

    out = {"experiment": "E-19", "seeds": seeds,
           "note": "检验检索次数/mass/age 是否携带重要性信息；用独立准确率损失而非自证",
           "rows": all_rows, "summary": summary,
           "best_metric": best[0], "elapsed_sec": round(time.time() - t0, 1)}
    with open(os.path.join(RESULTS, "e19_retrieval_count.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e19_retrieval_count.json')}")


if __name__ == "__main__":
    main()
