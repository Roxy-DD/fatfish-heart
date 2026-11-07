"""
E-26 补 E-25 的缺陷 + 从数据直接推出的改进

E-25 的缺陷：
  shift 是"整体平移图像"，平移后部分数字被裁掉
  → −16.5pp 里有多少是"定位错位"，有多少是"信息丢失"，分不清

本实验三个对照，逐步分离：
  ① shift_crop    原样平移（会裁切）——复现 E-25
  ② shift_pad     先扩边再平移（不裁切）——分离"裁切"因素
  ③ shift_center  先质心对齐再平移——测"中心化能否抵消位移"

再加两个从 E-25 直接推出的改进（不照搬文献，从数据推）：
  ④ 质心对齐 + HOG（训练和测试都对齐）
  ⑤ 纯质心偏移特征（把"位置"显式作为一个特征）

按用户规则：不预设门槛，只看哪个变好。
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

RESULTS = os.path.join(_HERE, "results")
THR = 0.560


def l2(v):
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    n[n == 0] = 1.0
    return v / n


def shift_crop(X, dy, dx):
    out = np.zeros_like(X)
    h, w = X.shape[1], X.shape[2]
    out[:, max(0, dy):min(h, h + dy), max(0, dx):min(w, w + dx)] = \
        X[:, max(0, -dy):min(h, h - dy), max(0, -dx):min(w, w - dx)]
    return out


def shift_pad(X, dy, dx):
    """先补边再平移，保证信息不丢"""
    P = np.pad(X, ((0, 0), (4, 4), (4, 4)), mode="constant")
    return shift_crop(P, dy, dx)[:, 4:-4, 4:-4]


def center(X):
    """把每个数字平移到质心居中（零阶+一阶矩对齐）"""
    out = np.zeros_like(X)
    h, w = X.shape[1], X.shape[2]
    for k in range(len(X)):
        ys, xs = np.nonzero(X[k] > 30)
        if len(ys) == 0:
            out[k] = X[k]
            continue
        cy, cx = ys.mean(), xs.mean()
        dy = int(round(h / 2 - cy))
        dx = int(round(w / 2 - cx))
        out[k] = shift_crop(X[k:k + 1], dy, dx)[0]
    return out


def centroid_feat(X):
    """质心偏移 + 质量，作为显式位置特征（2 维）"""
    h, w = X.shape[1], X.shape[2]
    f = np.zeros((len(X), 3), np.float32)
    for k in range(len(X)):
        v = X[k].astype(np.float64)
        tot = v.sum()
        if tot == 0:
            continue
        ys, xs = np.mgrid[0:h, 0:w]
        f[k, 0] = (v * ys).sum() / tot / h     # 归一化质心 y
        f[k, 1] = (v * xs).sum() / tot / w
        f[k, 2] = tot / (255.0 * h * w)        # 墨水比例
    return f


def learn_store(F, y, n, seed, threshold, mitosis=5):
    rng = np.random.default_rng(seed)
    idx = np.arange(len(y))
    rng.shuffle(idx)
    idx = idx[:n]
    M, L, mass = [], [], []
    for i in idx:
        x = F[i]
        if M:
            Mx = np.array(M, np.float32)
            sim = Mx @ x
            k = int(np.argmax(sim))
            if L[k] == str(y[i]) and sim[k] > threshold:
                if mass[k] > mitosis:
                    M.append(x.copy()); L.append(str(y[i])); mass.append(1)
                else:
                    rate = 1.0 / (mass[k] + 1)
                    nv = M[k] * (1 - rate) + x * rate
                    nn = np.linalg.norm(nv)
                    M[k] = nv / nn if nn > 0 else nv
                    mass[k] += 1
                continue
        M.append(x.copy()); L.append(str(y[i])); mass.append(1)
    return np.array(M, np.float32), L


def acc_of(M, L, X, y, topk=3):
    Ls = [str(v) for v in L]
    uniq = sorted(set(Ls))
    lid = np.array([uniq.index(l) for l in Ls], np.int32)
    masks = [np.where(lid == i)[0] for i in range(len(uniq))]
    uarr = np.array(uniq, dtype=object)
    S = X @ M.T
    sc = np.empty((len(X), len(uniq)), np.float32)
    for j, idx in enumerate(masks):
        v = S[:, idx]
        kk = min(topk, v.shape[1])
        sc[:, j] = np.partition(v, -kk, axis=1)[:, -kk:].mean(axis=1)
    yv = np.array([str(v) for v in y], dtype=object)
    return float(np.mean(uarr[np.argmax(sc, axis=1)] == yv))


def main():
    Xtr, ytr = load_mnist(True)
    Xte, yte = load_mnist(False)
    t0 = time.time()
    N = 3000
    rng = np.random.default_rng(42)
    test_idx = rng.choice(4000, 2000, replace=False)
    Xt, yt = Xte[test_idx], yte[test_idx]

    print("=" * 100)
    print("E-26 分离『定位敏感』与『信息丢失』")
    print("=" * 100)

    # 训练特征（都是 original 域）
    print("构造训练特征...")
    F_hog = f_hog(Xtr)
    F_hog_c = f_hog(center(Xtr))
    print("  hog / hog_centered ok")

    # ---- 实验 1：三种 shift 方式，同一套星 ----
    print("\n" + "=" * 100)
    print("① 三种平移方式对比（训练用 original，测试用不同 shift）")
    print("=" * 100)
    M, L = learn_store(F_hog, ytr, N, 0, THR)
    print(f"星数 {len(M)}")
    print(f"{'测试域':<18}{'macro':<10}{'说明':<30}")
    print("-" * 100)
    rows1 = []
    variants = {
        "original": Xt,
        "shift_crop(2,-2)": shift_crop(Xt, 2, -2),
        "shift_pad(2,-2)": shift_pad(Xt, 2, -2),
        "shift_crop(4,0)": shift_crop(Xt, 4, 0),
        "shift_pad(4,0)": shift_pad(Xt, 4, 0),
        "shift_pad(0,4)": shift_pad(Xt, 0, 4),
    }
    for name, Xd in variants.items():
        a = acc_of(M, L, f_hog(Xd), yt)
        rows1.append({"variant": name, "macro": round(a, 4)})
        note = ""
        if "crop" in name:
            note = "会裁切图像（信息丢失）"
        elif "pad" in name:
            note = "不裁切（纯位移）"
        print(f"{name:<18}{a:<10.4f}{note:<30}")
    print("-" * 100)
    base = next(r["macro"] for r in rows1 if r["variant"] == "original")
    crop2 = next(r["macro"] for r in rows1 if r["variant"] == "shift_crop(2,-2)")
    pad2 = next(r["macro"] for r in rows1 if r["variant"] == "shift_pad(2,-2)")
    print(f"\n基线 {base:.4f}")
    print(f"  平移 2px，裁切:   {crop2:.4f} ({crop2-base:+.4f})")
    print(f"  平移 2px，不裁切: {pad2:.4f} ({pad2-base:+.4f})")
    print(f"  → 裁切造成的损失: {pad2-crop2:+.4f}")
    print(f"  → 纯位移造成的损失: {pad2-base:+.4f}")

    # ---- 实验 2：质心对齐能否抵消位移 ----
    print("\n" + "=" * 100)
    print("② 质心对齐：训练与测试都先对齐，再看位移是否还有影响")
    print("=" * 100)
    M_c, L_c = learn_store(F_hog_c, ytr, N, 0, THR)
    print(f"星数 {len(M_c)}")
    print(f"{'测试域（已对齐）':<22}{'macro':<10}{'相对 original':<16}")
    print("-" * 100)
    rows2 = []
    for name, Xd in variants.items():
        a = acc_of(M_c, L_c, f_hog(center(Xd)), yt)
        rows2.append({"variant": name, "macro": round(a, 4)})
        print(f"{name:<22}{a:<10.4f}{'':<16}")
    print("-" * 100)
    b2 = next(r["macro"] for r in rows2 if r["variant"] == "original")
    p2 = next(r["macro"] for r in rows2 if r["variant"] == "shift_pad(2,-2)")
    c2 = next(r["macro"] for r in rows2 if r["variant"] == "shift_crop(2,-2)")
    print(f"\n对齐后：original {b2:.4f}，shift_pad(2,-2) {p2:.4f} ({p2-b2:+.4f})")
    print(f"  对比未对齐时的纯位移损失 {pad2-base:+.4f}")
    gain = (p2 - b2) - (pad2 - base)
    print(f"  → 质心对齐挽回了 {gain:+.4f}")

    # ---- 实验 3：显式加入位置特征 ----
    print("\n" + "=" * 100)
    print("③ 把『位置』显式做成特征（质心 y/x + 墨水比例）")
    print("=" * 100)
    Ctr = centroid_feat(Xtr)
    print(f"  位置特征范围: y∈[{Ctr[:,0].min():.3f},{Ctr[:,0].max():.3f}] "
          f"x∈[{Ctr[:,1].min():.3f},{Ctr[:,1].max():.3f}] "
          f"ink∈[{Ctr[:,2].min():.4f},{Ctr[:,2].max():.4f}]")
    for wpos in [0.0, 0.05, 0.1, 0.2]:
        Fa = l2(np.hstack([F_hog, wpos * Ctr]).astype(np.float32))
        M_a, L_a = learn_store(Fa, ytr, N, 0, THR)
        res = {}
        for name, Xd in [("original", Xt), ("shift_pad", shift_pad(Xt, 2, -2))]:
            Fd = l2(np.hstack([f_hog(Xd), wpos * centroid_feat(Xd)]).astype(np.float32))
            res[name] = acc_of(M_a, L_a, Fd, yt)
        print(f"  w={wpos:<5.2f} original={res['original']:.4f}  "
              f"shift_pad={res['shift_pad']:.4f}  "
              f"位移损失={res['shift_pad']-res['original']:+.4f}")

    print(f"\n耗时 {time.time()-t0:.0f}s")

    out = {"experiment": "E-26", "note": "分离定位敏感与信息丢失",
           "shift_variants": rows1, "centered": rows2,
           "baseline": base, "crop_loss": round(crop2 - base, 4),
           "pad_loss": round(pad2 - base, 4),
           "crop_artifact": round(pad2 - crop2, 4),
           "center_recovery": round(gain, 4),
           "elapsed_sec": round(time.time() - t0, 1)}
    with open(os.path.join(RESULTS, "e26_shift_control.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e26_shift_control.json')}")


if __name__ == "__main__":
    main()
