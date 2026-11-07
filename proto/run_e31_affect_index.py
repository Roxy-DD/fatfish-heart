"""
E-31 情绪作为记忆的索引/UUID

用户假设（2026-10-25）：
  "我们回忆的时候，很多时候是回忆到了那种情绪或者感受，然后才回忆出那个记忆的。
   情绪感觉就像是一种记忆的编号，或者是它的 UUID。
   记忆要不要留存、要不要有重要性，好像和你的感受非常相关，
   好像真的和有没有用、有没有价值之类的没有非常大的关系。"

文献印证（已搜到，待实测）：
  · mood-dependent memory：情绪状态一致时，回忆同一批材料更容易
    → Bower 1978 原话：''the mood at the time of retrieval serves as a cue
      to distinguish what words were encountered in what list''
    → 这正是"情绪=编号"的心理学版本
  · mood-congruent memory 稳定可复现（多篇综述确认）
  · eCMR 模型：情绪是"绑定记忆与上下文"的调制过程

本实验把假设做成可运行的机制，并测三个问题：
  ① 「情绪先于内容」的检索顺序，能不能比"直接比内容"更好？
  ② 「情绪」应该多少维？（太粗分不开，太细则不是"编号"）
  ③ 情绪做索引后，检索量能降到多少（这才是"UUID"真正的价值）

设计：
  给每条记忆额外绑一个低维"情绪向量"（用真实数据推，不是编造）：
    情绪 = 对 HOG 特征做一组粗粒度的全局统计
    （墨水密度 / 笔画粗细 / 重心偏移 / 长宽比 / 边缘强度）
  检索：先按情绪筛出候选，再在候选内比内容。
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

from retina import load_mnist
from run_e23_features import f_hog
from run_e27_alignment import center_img

RESULTS = os.path.join(_HERE, "results")
THR = 0.560


def affect_feat(X, dims=None):
    """
    从原始图像推"情绪向量"（低维全局统计）。
    这些量都**和"内容是什么"无关，只和"整体感觉"有关**：
      0 墨水密度（多少黑）
      1 笔画粗细（边缘强度）
      2 重心纵向偏移
      3 重心横向偏移
      4 长宽比
      5 上下对称性
      6 左右对称性
    dims 用于只取前几维。
    """
    Xf = X.astype(np.float32) / 255.0
    n = len(Xf)
    f = np.zeros((n, 7), np.float32)
    for k in range(n):
        img = Xf[k]
        tot = img.sum()
        ys, xs = np.nonzero(img > 0.1)
        if tot <= 0 or len(ys) == 0:
            continue
        f[k, 0] = tot / img.size                      # 墨水密度
        # 边缘强度
        gx = np.abs(np.diff(img, axis=1)).sum()
        gy = np.abs(np.diff(img, axis=0)).sum()
        f[k, 1] = (gx + gy) / (img.size * 2)
        f[k, 2] = ys.mean() / img.shape[0] - 0.5      # 纵向偏移
        f[k, 3] = xs.mean() / img.shape[1] - 0.5      # 横向偏移
        f[k, 4] = (ys.max() - ys.min() + 1) / max(1, xs.max() - xs.min() + 1)
        # 上下对称（翻转后与自身的相关）
        up = img[: img.shape[0] // 2]
        dn = img[::-1][: img.shape[0] // 2]
        f[k, 5] = float(np.dot(up.ravel(), dn.ravel()) /
                        (np.linalg.norm(up) * np.linalg.norm(dn) + 1e-9))
        # 左右对称
        lf = img[:, : img.shape[1] // 2]
        rt = img[:, ::-1][:, : img.shape[1] // 2]
        f[k, 6] = float(np.dot(lf.ravel(), rt.ravel()) /
                        (np.linalg.norm(lf) * np.linalg.norm(rt) + 1e-9))
    if dims is not None:
        f = f[:, :dims]
    # 每个维度标准化（让它们可比）
    mu = f.mean(axis=0, keepdims=True)
    sd = f.std(axis=0, keepdims=True) + 1e-6
    return (f - mu) / sd, mu, sd


def l2(v):
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    n[n == 0] = 1.0
    return v / n


def build_store(F, A, y, n, seed, threshold=THR):
    """F=内容特征，A=情绪向量（已标准化）"""
    rng = np.random.default_rng(seed)
    idx = np.arange(len(y))
    rng.shuffle(idx)
    idx = idx[:n]
    M, L, MA, mass = [], [], [], []
    for i in idx:
        x, a, lab = F[i], A[i], str(y[i])
        if M:
            sim = np.array(M, np.float32) @ x
            k = int(np.argmax(sim))
            if L[k] == lab and sim[k] > threshold:
                if mass[k] > 5:
                    M.append(x.copy()); L.append(lab); MA.append(a.copy()); mass.append(1)
                else:
                    rate = 1.0 / (mass[k] + 1)
                    nv = M[k] * (1 - rate) + x * rate
                    nn = np.linalg.norm(nv)
                    M[k] = nv / nn if nn > 0 else nv
                    mass[k] += 1
                continue
        M.append(x.copy()); L.append(lab); MA.append(a.copy()); mass.append(1)
    return (np.array(M, np.float32), L, np.array(MA, np.float32))


def predict_full(M, L, X, topk=3):
    uniq = sorted(set(L))
    lid = np.array([uniq.index(l) for l in L], np.int32)
    masks = [np.where(lid == i)[0] for i in range(len(uniq))]
    S = X @ M.T
    sc = np.empty((len(X), len(uniq)), np.float32)
    for j, idx in enumerate(masks):
        v = S[:, idx]
        kk = min(topk, v.shape[1])
        sc[:, j] = np.partition(v, -kk, axis=1)[:, -kk:].mean(axis=1)
    return np.array(uniq, dtype=object)[np.argmax(sc, axis=1)]


def predict_affect_indexed(M, L, MA, X, A, topk=3, cand_ratio=0.2):
    """
    情绪先行的检索：
      1) 先按情绪距离，从所有星里筛出候选（比例 cand_ratio）
      2) 只在候选里比内容
    返回 (预测, 平均扫描量)
    """
    uniq = sorted(set(L))
    lid = np.array([uniq.index(l) for l in L], np.int32)
    masks = [np.where(lid == i)[0] for i in range(len(uniq))]
    n_star = M.shape[0]
    n_cand = max(8, int(n_star * cand_ratio))
    preds, scan = [], 0
    for i in range(len(X)):
        # 情绪距离
        da = np.linalg.norm(MA - A[i], axis=1)
        cand = np.argpartition(da, n_cand)[:n_cand]
        scan += len(cand)
        S = M[cand] @ X[i]
        cand_l = [L[j] for j in cand]
        sc = {}
        for u in uniq:
            vals = [S[t] for t, l in enumerate(cand_l) if l == u]
            if vals:
                kk = min(topk, len(vals))
                sc[u] = float(np.mean(np.partition(np.array(vals), -kk)[-kk:]))
        preds.append(max(sc, key=sc.get) if sc else "?")
    return np.array(preds, dtype=object), scan / len(X)


def acc(pred, y):
    yv = np.array([str(v) for v in y], dtype=object)
    return float(np.mean(pred == yv))


def main():
    Xtr, ytr = load_mnist(True)
    Xte, yte = load_mnist(False)
    Xtr, ytr = Xtr[:12000], ytr[:12000]
    Xte, yte = Xte[:4000], yte[:4000]
    t0 = time.time()

    print("=" * 104)
    print("E-31 情绪作为记忆索引/UUID")
    print("=" * 104)

    print("\n构造特征...")
    Ctr = center_img(Xtr)
    Cte = center_img(Xte)
    Ftr, Fte = f_hog(Ctr), f_hog(Cte)
    Atr_raw, mu_a, sd_a = affect_feat(Ctr)
    Ate_raw = (affect_feat(Cte)[0] * 0 + 0)  # placeholder
    a_te, _, _ = affect_feat(Cte)
    # 用训练集的均值/方差统一标准化
    Ate = (affect_feat(Cte, dims=None)[0])   # 已标准化（各自数据集）
    print(f"  内容特征 {Ftr.shape}  情绪向量 {Atr_raw.shape}（7 维全局统计）")

    N = 4000
    seed = 0
    M, L, MA = build_store(Ftr, Atr_raw, ytr, N, seed)
    print(f"  星数 {len(L)}")

    # ---- ① 情绪 7 维的整体分布（它能不能分开类？） ----
    print("\n" + "-" * 104)
    print("① 情绪向量本身有没有区分力？（各类的情绪均值）")
    print("-" * 104)
    labs = sorted(set(str(v) for v in ytr))
    print(f"{'维度含义':<16}", end="")
    for l in labs:
        print(f"{l:>7}", end="")
    print()
    names = ["墨水密度", "边缘强度", "纵向偏移", "横向偏移", "长宽比", "上下对称", "左右对称"]
    for d in range(7):
        print(f"{names[d]:<16}", end="")
        for l in labs:
            m = Atr_raw[np.array([str(v) == l for v in ytr])][:, d].mean()
            print(f"{m:>7.2f}", end="")
        print()
    print("\n  → 若各类在某一维差异明显，说明该维能当'编号'用")

    # ---- ② 情绪先行 vs 直接比内容 ----
    print("\n" + "-" * 104)
    print("② 检索顺序对比：情绪先行 vs 直接比内容")
    print("-" * 104)
    pred_full = predict_full(M, L, Fte)
    a_full = acc(pred_full, yte)
    print(f"  直接比内容（全扫描）: acc={a_full:.4f}  扫描 {len(L)} 颗/次")

    rows = []
    for dims in [2, 3, 5, 7]:
        # 只取前 dims 维情绪
        Atr_d = Atr_raw[:, :dims]
        Ate_d = Ate[:, :dims]
        M, L, MA = build_store(Ftr, Atr_d, ytr, N, seed)
        for cr in [0.05, 0.1, 0.2, 0.5]:
            pred, scan = predict_affect_indexed(M, L, MA, Fte, Ate_d, cand_ratio=cr)
            a = acc(pred, yte)
            rows.append({"affect_dim": dims, "cand_ratio": cr,
                         "acc": round(a, 4), "scan_per_query": round(scan, 1),
                         "scan_reduction": round(1 - scan / len(L), 3),
                         "acc_delta_vs_full": round(a - a_full, 4)})
            print(f"  情绪{dims}维 候选{cr*100:>4.0f}%: acc={a:.4f} "
                  f"({a-a_full:+.4f})  扫描 {scan:>6.1f} 颗/次 "
                  f"(省 {100*(1-scan/len(L)):.1f}%)")

    print(f"\n耗时 {time.time()-t0:.0f}s")

    # ---- 汇总 ----
    print("\n" + "=" * 104)
    print("汇总：情绪维度 × 候选比例")
    print("=" * 104)
    print(f"{'情绪维':<8}{'候选%':<8}{'acc':<10}{'相对全扫描':<12}{'扫描量':<10}{'省下':<10}")
    print("-" * 104)
    for r in rows:
        print(f"{r['affect_dim']:<8d}{int(r['cand_ratio']*100):<8d}{r['acc']:<10.4f}"
              f"{r['acc_delta_vs_full']:<+12.4f}{r['scan_per_query']:<10.1f}"
              f"{r['scan_reduction']*100:<10.1f}%")
    print("-" * 104)

    # 找"精度损失 < 1pp 时，扫描量最少"
    ok = [r for r in rows if r["acc_delta_vs_full"] > -0.01]
    if ok:
        best = min(ok, key=lambda r: r["scan_per_query"])
        print(f"\n★ 精度损失 <1pp 且扫描最少: 情绪{best['affect_dim']}维 "
              f"候选{int(best['cand_ratio']*100)}% → acc={best['acc']:.4f} "
              f"({best['acc_delta_vs_full']:+.4f}), 扫描 {best['scan_per_query']:.0f} 颗 "
              f"(省 {best['scan_reduction']*100:.0f}%)")
    else:
        best = None
        print("\n★ 没有任何配置能在 1pp 内保住精度")

    out = {"experiment": "E-31", "hypothesis": "情绪=记忆的索引/UUID",
           "affect_dims": 7, "affect_names": names,
           "n_train": N, "n_stars": len(L),
           "full_scan_acc": round(a_full, 4),
           "configs": rows, "best": best,
           "elapsed_sec": round(time.time() - t0, 1)}
    with open(os.path.join(RESULTS, "e31_affect_index.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e31_affect_index.json')}")


if __name__ == "__main__":
    main()
