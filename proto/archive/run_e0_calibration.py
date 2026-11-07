"""
E-0 阈值标定（前置实验）

为什么必须先做这一步：
  docs/03 缺陷与证据 指出原实现 resonance_threshold=0.85 是硬编码的，从未标定。
  本实验实测该特征空间下同类/异类的余弦分布，确定阈值可用区间。

  这直接决定 E-1/E-2/E-3 是否有意义：
  阈值若高于同类均值 → 全部新建 → 星数爆炸 → 压缩失效
  阈值若低于异类均值 → 概念混淆 → 准确率崩

输出：results/e0_calibration.json
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

from retina import get_features, load_mnist

RESULTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")


def raw_pixel_features(X: np.ndarray) -> np.ndarray:
    """784 维像素特征，L2 归一化。作为对照基线。"""
    v = X.reshape(len(X), -1).astype(np.float32) / 255.0
    n = np.linalg.norm(v, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return v / n


def pair_distributions(F: np.ndarray, y: np.ndarray, n_sample: int = 400,
                       seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """抽样计算同类对 / 异类对的余弦分布。"""
    rng = np.random.default_rng(seed)
    idx = rng.choice(len(F), min(n_sample, len(F)), replace=False)
    Fs, ys = F[idx], y[idx]
    sim = Fs @ Fs.T
    iu = np.triu_indices(len(Fs), k=1)
    sims = sim[iu]
    same = (ys[:, None] == ys[None, :])[iu]
    return sims[same], sims[~same]


def best_threshold(same: np.ndarray, diff: np.ndarray) -> dict:
    """在 [0,1] 上扫描，最大化 balanced accuracy。"""
    best = {"t": 0.5, "bacc": 0.0}
    for t in np.arange(0.30, 0.999, 0.005):
        # 预测"同一概念" = 相似度 > t
        tpr = float(np.mean(same > t))          # 同类判对
        tnr = float(np.mean(diff <= t))         # 异类判对
        bacc = 0.5 * (tpr + tnr)
        if bacc > best["bacc"]:
            best = {"t": float(t), "bacc": bacc, "tpr": tpr, "tnr": tnr}
    return best


def quantiles(a: np.ndarray) -> dict:
    return {f"p{q}": float(np.percentile(a, q)) for q in [1, 5, 25, 50, 75, 95, 99]}


def main(n_train: int = 6000, n_test: int = 2000):
    os.makedirs(RESULTS, exist_ok=True)
    out: dict = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                 "n_train": n_train, "n_test": n_test}

    print("=" * 76)
    print("E-0 阈值标定")
    print("=" * 76)

    # 1. MobileNetV2 特征
    print("\n[1] MobileNetV2 (1280d, 冻结)")
    t0 = time.time()
    Ftr, ytr, Xtr = get_features(True, "cal", limit=n_train, input_size=96)
    Fte, yte, Xte = get_features(False, "cal", limit=n_test, input_size=96)
    print(f"提取耗时 {time.time()-t0:.1f}s  train={Ftr.shape} test={Fte.shape}")

    same_m, diff_m = pair_distributions(Ftr, ytr)
    bt_m = best_threshold(same_m, diff_m)
    out["mobilenet"] = {
        "dim": int(Ftr.shape[1]),
        "same": {"mean": float(same_m.mean()), "std": float(same_m.std()), **quantiles(same_m)},
        "diff": {"mean": float(diff_m.mean()), "std": float(diff_m.std()), **quantiles(diff_m)},
        "gap": float(same_m.mean() - diff_m.mean()),
        "best_threshold": bt_m,
        "default_threshold_0.85": {
            "tpr": float(np.mean(same_m > 0.85)),
            "tnr": float(np.mean(diff_m <= 0.85)),
        },
    }

    print(f"  同类 cos: 均值 {same_m.mean():.4f} ± {same_m.std():.4f}  "
          f"[p5={np.percentile(same_m,5):.3f}, p95={np.percentile(same_m,95):.3f}]")
    print(f"  异类 cos: 均值 {diff_m.mean():.4f} ± {diff_m.std():.4f}  "
          f"[p5={np.percentile(diff_m,5):.3f}, p95={np.percentile(diff_m,95):.3f}]")
    print(f"  间隔: {same_m.mean()-diff_m.mean():+.4f}")
    print(f"  最优阈值: T={bt_m['t']:.3f}  balanced_acc={bt_m['bacc']:.4f}  "
          f"(TPR={bt_m['tpr']:.3f}, TNR={bt_m['tnr']:.3f})")
    print(f"  ★ 默认 0.85 的表现: TPR={np.mean(same_m>0.85):.4f}  "
          f"(即 {np.mean(same_m<=0.85)*100:.1f}% 的同类样本会被误判为新概念)")

    # 2. 像素基线
    print("\n[2] 原始像素 (784d) 作为对照基线")
    Ptr = raw_pixel_features(Xtr)
    same_p, diff_p = pair_distributions(Ptr, ytr)
    bt_p = best_threshold(same_p, diff_p)
    out["pixel"] = {
        "dim": 784,
        "same": {"mean": float(same_p.mean()), "std": float(same_p.std()), **quantiles(same_p)},
        "diff": {"mean": float(diff_p.mean()), "std": float(diff_p.std()), **quantiles(diff_p)},
        "gap": float(same_p.mean() - diff_p.mean()),
        "best_threshold": bt_p,
    }
    print(f"  同类 cos: 均值 {same_p.mean():.4f} ± {same_p.std():.4f}")
    print(f"  异类 cos: 均值 {diff_p.mean():.4f} ± {diff_p.std():.4f}")
    print(f"  间隔: {same_p.mean()-diff_p.mean():+.4f}")
    print(f"  最优阈值: T={bt_p['t']:.3f}  balanced_acc={bt_p['bacc']:.4f}")

    # 3. 对比
    print("\n" + "=" * 76)
    print("对比结论")
    print("=" * 76)
    print(f"{'特征':<18}{'维度':<8}{'同类均值':<12}{'异类均值':<12}{'间隔':<10}{'最优T':<10}{'bAcc':<8}")
    print("-" * 76)
    print(f"{'MobileNetV2':<18}{Ftr.shape[1]:<8d}{same_m.mean():<12.4f}{diff_m.mean():<12.4f}"
          f"{same_m.mean()-diff_m.mean():<10.4f}{bt_m['t']:<10.3f}{bt_m['bacc']:<8.4f}")
    print(f"{'RawPixel':<18}{784:<8d}{same_p.mean():<12.4f}{diff_p.mean():<12.4f}"
          f"{same_p.mean()-diff_p.mean():<10.4f}{bt_p['t']:<10.3f}{bt_p['bacc']:<8.4f}")

    better = "mobilenet" if bt_m["bacc"] > bt_p["bacc"] else "pixel"
    print(f"\n判定：{better} 特征可分性更好 (bAcc {max(bt_m['bacc'], bt_p['bacc']):.4f})")
    out["verdict"] = {
        "better_feature": better,
        "mobilenet_bacc": bt_m["bacc"],
        "pixel_bacc": bt_p["bacc"],
        "default_0.85_tpr": float(np.mean(same_m > 0.85)),
        "calibrated_threshold": bt_m["t"],
        "note": "默认 0.85 高于同类均值 → 绝大多数同类样本判为新概念 → 星数爆炸",
    }

    with open(os.path.join(RESULTS, "e0_calibration.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n结果写入 {os.path.join(RESULTS, 'e0_calibration.json')}")
    return out


if __name__ == "__main__":
    main()
