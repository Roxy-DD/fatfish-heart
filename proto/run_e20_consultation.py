"""
E-20 联合会诊（用户设计灵感 1）

用户原话：
  "当病人出现紧急危重情况时，会有急诊科进行联合会诊，根本不会说要给你分科。
   也就是遇到特殊情况、出现问题的时候，直接全部科室一起上"

翻译成机制：
  · 平时：一路证据快速判断（省事）—— 对应"普通门诊"
  · 可疑时（top1 与 top2 很接近）：**叫第二路独立证据来一起投票**
  · 不是"永远多路"（那会一直慢、一直吵）

关键区别（之前失败的原因）：
  E-14 的 margin 是在**同一路证据内**换算法 → 失败
  联合会诊是**引入第二路独立证据** → 未试过

两路证据（真的独立，不是同一特征的变体）：
  · 路 1：MobileNetV2 语义特征（1280 维），擅长"整体语义"
  · 路 2：原始像素（784 维），擅长"笔画的物理形状"
  两者相关性低，可能互补。

设计：
  consult(margin_threshold)：
    算路1的 topk3 分数 → 得 top1,top2
    若 gap < margin_threshold → 路2 也投票
    投票方式：两路各自 top1，若一致则采纳；若不一致，比较两路的"自信度"（gap 大小）
            → 更自信的那路赢（这就是"会诊时听更确定的科室"）

不预设门槛。只回答：
  · 会诊在多大比例样本上触发
  · 触发时救回多少、误伤多少
  · 与 base 相比 macro 升还是降
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
from retina import get_features, load_mnist

RESULTS = os.path.join(_HERE, "results")
TASKS = {"A": {"0", "1", "2"}, "B": {"3", "4", "5"}, "C": {"6", "7", "8", "9"}}
TR, TE = 900, 300


def l2(v):
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    n[n == 0] = 1.0
    return v / n


def split_idx(y, labels, ntr, nte, seed):
    rng = np.random.default_rng(seed)
    idx = np.array([i for i, lab in enumerate(y) if lab in labels])
    rng.shuffle(idx)
    return idx[:ntr], idx[ntr:ntr + nte]


def build_path(F, idx_tr, idx_te, y, seed, tf, threshold=0.705):
    """用给定特征建一条通路，返回 (星矩阵, 星标签, 测试X, 测试y)"""
    b = CorpusCallosum(threshold=threshold, seed=seed)
    for i in idx_tr:
        b.memorize(tf(F[i]), str(y[i]))
    stars = b.all_stars()
    M = np.array([s.vector for s in stars], np.float32)
    L = np.array([str(s.label) for s in stars], dtype=object)
    uniq = sorted(set(L.tolist()))
    lid = np.array([uniq.index(l) for l in L], np.int32)
    masks = [np.where(lid == i)[0] for i in range(len(uniq))]
    Xte = np.stack([tf(F[i]) for i in idx_te])
    yte = np.array([str(y[i]) for i in idx_te], dtype=object)
    return {"M": M, "uniq": uniq, "masks": masks, "X": Xte, "y": yte,
            "uniq_arr": np.array(uniq, dtype=object)}


def scores(path, X, k=3):
    S = X @ path["M"].T
    sc = np.empty((len(X), len(path["uniq"])), np.float32)
    for i, idx in enumerate(path["masks"]):
        v = S[:, idx]
        kk = min(k, v.shape[1])
        sc[:, i] = np.partition(v, -kk, axis=1)[:, -kk:].mean(axis=1)
    return sc


def top2_gap(sc):
    order = np.argsort(-sc, axis=1)
    a, b = order[:, 0], order[:, 1]
    gap = sc[np.arange(len(sc)), a] - sc[np.arange(len(sc)), b]
    return a, b, gap


def main():
    # 两路特征
    F_sem, y_tr, _ = get_features(True, "main", limit=20000, input_size=96)
    F_sem_te, y_te_all, _ = get_features(False, "main", limit=8000, input_size=96)
    mu = F_sem[:9000].mean(axis=0)
    sd = F_sem[:9000].std(axis=0) + 1e-6
    tf_sem = lambda F: l2((F - mu) / sd)

    # 路 2：原始像素（用 train/test 的原始图像）
    Xtr_raw, ytr_raw = load_mnist(True)
    Xte_raw, yte_raw = load_mnist(False)

    def tf_pix(F):
        """支持单张 [28,28] 与批量 [N,28,28] 两种输入"""
        a = np.asarray(F, dtype=np.float32)
        if a.ndim == 0:
            raise ValueError("空输入")
        if a.ndim <= 2:                      # 单张 [28,28]
            a = a.reshape(-1)
            return a / max(np.linalg.norm(a), 1e-9)
        v = a.reshape(len(a), -1) / 255.0    # 批量
        n = np.linalg.norm(v, axis=1, keepdims=True)
        n[n == 0] = 1.0
        return v / n

    # 为路径2准备：与路径1用同一批索引
    # get_features 的 F 顺序与 load_mnist 一致（都是前 N 条），所以索引可共用
    assert len(F_sem) <= len(Xtr_raw)

    seeds = [0, 1, 2]
    t0 = time.time()

    print("=" * 100)
    print("E-20 联合会诊：一路平时用，可疑时叫第二路投票")
    print("=" * 100)
    print(f"路1 = MobileNetV2 语义(1280d, zsc)   路2 = 原始像素(784d)")
    print()

    agg = []
    for seed in seeds:
        paths = {}
        for name in "ABC":
            itr, ite = split_idx(y_tr, TASKS[name], TR, TE, seed)
            p1 = build_path(F_sem, itr, ite, y_tr, seed, tf_sem)
            p2 = build_path(Xtr_raw, itr, ite, y_tr, seed, tf_pix)
            paths[name] = (p1, p2)

        for name in "ABC":
            p1, p2 = paths[name]
            sc1 = scores(p1, p1["X"])
            sc2 = scores(p2, p2["X"])
            a1, b1, gap1 = top2_gap(sc1)
            a2, b2, gap2 = top2_gap(sc2)

            pred1 = p1["uniq_arr"][a1]
            pred2 = p2["uniq_arr"][a2]
            base_acc = float(np.mean(pred1 == p1["y"]))
            p2_acc = float(np.mean(pred2 == p2["y"]))

            print(f"--- seed={seed} 任务{name}: 路1={base_acc:.4f}  路2={p2_acc:.4f} "
                  f"(两路单独) ---")

            for d in [0.0, 0.01, 0.02, 0.03, 0.05]:
                consult = gap1 < d
                n_consult = int(consult.sum())

                # 会诊决策：两路各自 top1
                # 一致 → 采纳（无变化）
                # 不一致 → 谁的 gap 更大听谁（更自信的科室）
                pred = pred1.copy()
                disagree = consult & (pred1 != pred2)
                if disagree.sum():
                    choose2 = disagree & (gap2 > gap1)
                    pred[choose2] = pred2[choose2]
                    # 其余维持路1

                acc = float(np.mean(pred == p1["y"]))
                # 救回 / 误伤
                saved = int((disagree & (pred2 == p1["y"]) & (pred2 != pred1)).sum())
                hurt = int((disagree & (pred1 == p1["y"]) & (pred2 != p1["y"])).sum())
                # 只统计最终被改判的
                changed = int(disagree.sum())

                row = {"seed": seed, "task": name, "delta_margin": d,
                       "consult_rate": round(n_consult / len(sc1), 4),
                       "n_consult": n_consult, "n_disagree": changed,
                       "saved": saved, "hurt": hurt,
                       "acc_path1": round(base_acc, 4), "acc_path2": round(p2_acc, 4),
                       "acc_consult": round(acc, 4),
                       "delta": round(acc - base_acc, 4)}
                agg.append(row)
                print(f"    d={d:.2f}: 触发{n_consult:4d}颗({n_consult/len(sc1)*100:5.1f}%) "
                      f"分歧{changed:3d} 救回{saved:2d} 误伤{hurt:2d} "
                      f"→ acc={acc:.4f} ({acc-base_acc:+.4f})")

    print(f"\n耗时 {time.time()-t0:.0f}s")

    # ---------- 汇总 ----------
    print("\n" + "=" * 100)
    print("汇总：不同会诊触发阈值下的平均效果（9 组：3 seeds × 3 任务）")
    print("=" * 100)
    print(f"{'d':<8}{'平均触发率':<12}{'平均分歧数':<12}{'救回':<8}{'误伤':<8}"
          f"{'平均Δ':<10}{'判定':<12}")
    print("-" * 100)
    summary = {}
    for d in [0.0, 0.01, 0.02, 0.03, 0.05]:
        rs = [r for r in agg if r["delta_margin"] == d]
        cr = float(np.mean([r["consult_rate"] for r in rs]))
        nd = float(np.mean([r["n_disagree"] for r in rs]))
        sv = float(np.mean([r["saved"] for r in rs]))
        ht = float(np.mean([r["hurt"] for r in rs]))
        dd = float(np.mean([r["delta"] for r in rs]))
        verdict = "有益" if dd > 0.002 else ("有害" if dd < -0.002 else "无差别")
        summary[str(d)] = {"consult_rate": round(cr, 4), "n_disagree": round(nd, 2),
                           "saved": round(sv, 2), "hurt": round(ht, 2),
                           "delta": round(dd, 4), "verdict": verdict}
        print(f"{d:<8.2f}{cr:<12.4f}{nd:<12.2f}{sv:<8.2f}{ht:<8.2f}{dd:<+10.4f}{verdict:<12}")
    print("-" * 100)

    # 两路各自的平均能力
    r_all = agg
    print(f"\n路1（语义）平均: {np.mean([r['acc_path1'] for r in r_all if r['delta_margin']==0.0]):.4f}")
    print(f"路2（像素）平均: {np.mean([r['acc_path2'] for r in r_all if r['delta_margin']==0.0]):.4f}")

    best_d = max(summary.items(), key=lambda kv: kv[1]["delta"])
    print(f"\n★ 最佳触发阈值 d={best_d[0]}: 平均Δ {best_d[1]['delta']:+.4f} "
          f"(触发率 {best_d[1]['consult_rate']:.1%}, 救回 {best_d[1]['saved']:.1f}, "
          f"误伤 {best_d[1]['hurt']:.1f})")

    # 净收益 = 救回 - 误伤
    print("\n净收益（救回 − 误伤）：")
    for d, s in summary.items():
        net = s["saved"] - s["hurt"]
        print(f"  d={d}: {net:+.1f} 颗")

    out = {"experiment": "E-20", "design": "联合会诊（conditional two-path consultation）",
           "path1": "MobileNetV2 semantic 1280d (zsc)", "path2": "raw pixel 784d",
           "seeds": seeds, "rows": agg, "summary": summary,
           "best_margin": best_d[0], "elapsed_sec": round(time.time() - t0, 1)}
    with open(os.path.join(RESULTS, "e20_consultation.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e20_consultation.json')}")


if __name__ == "__main__":
    main()
