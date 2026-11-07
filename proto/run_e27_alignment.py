"""
E-27 对齐的完整版：质心对齐 + 旋转对齐

E-26 证明质心对齐能挽回全部平移损失（+15.75pp）。
E-25 显示旋转仍掉 1.5–3.25pp，还没修。

本实验：
  ① 只质心对齐（当前最好）
  ② 质心 + 主轴旋转对齐（用二阶矩主轴方向校正）
  ③ 质心 + 多角度候选（测试时试几个角度，取最像的）
  ④ 只旋转对齐

主轴方向 = 用图像的二阶中心矩求主惯性轴，把主轴转到竖直。

按用户规则：不预设门槛，只看哪个变好、边界在哪。
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
from run_e26_shift_control import shift_crop, shift_pad, centroid_feat

RESULTS = os.path.join(_HERE, "results")
THR = 0.560


def l2(v):
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    n[n == 0] = 1.0
    return v / n


def center_img(X):
    out = np.zeros_like(X)
    h, w = X.shape[1], X.shape[2]
    for k in range(len(X)):
        ys, xs = np.nonzero(X[k] > 30)
        if len(ys) == 0:
            out[k] = X[k]
            continue
        dy = int(round(h / 2 - ys.mean()))
        dx = int(round(w / 2 - xs.mean()))
        out[k] = shift_crop(X[k:k + 1], dy, dx)[0]
    return out


def rotate_img(X, deg):
    """单张或批量绕中心旋转（最近邻）"""
    single = X.ndim == 2
    A = X[None] if single else X
    th = np.deg2rad(deg)
    c, s = np.cos(th), np.sin(th)
    h, w = A.shape[1], A.shape[2]
    cy, cx = (h - 1) / 2, (w - 1) / 2
    yy, xx = np.mgrid[0:h, 0:w]
    yy = yy - cy
    xx = xx - cx
    sy = (c * yy + s * xx + cy).round().astype(int)
    sx = (-s * yy + c * xx + cx).round().astype(int)
    ok = (sy >= 0) & (sy < h) & (sx >= 0) & (sx < w)
    out = np.zeros_like(A)
    out[:, ok] = A[:, sy[ok], sx[ok]]
    return out[0] if single else out


def principal_angle(X):
    """用二阶中心矩求主惯性轴角度（度）。返回每张图的角度。"""
    h, w = X.shape[1], X.shape[2]
    ys, xs = np.mgrid[0:h, 0:w]
    angs = np.zeros(len(X), np.float64)
    for k in range(len(X)):
        v = X[k].astype(np.float64)
        tot = v.sum()
        if tot <= 0:
            continue
        my = (v * ys).sum() / tot
        mx = (v * xs).sum() / tot
        dy = ys - my
        dx = xs - mx
        mu20 = (v * dy * dy).sum() / tot
        mu02 = (v * dx * dx).sum() / tot
        mu11 = (v * dy * dx).sum() / tot
        # 主轴角度（图像坐标，y 向下）
        ang = 0.5 * np.arctan2(2 * mu11, mu20 - mu02)
        angs[k] = np.rad2deg(ang)
    return angs


def align(X, mode="center"):
    """mode: none | center | center_rot | rot"""
    if mode == "none":
        return X
    if mode == "center":
        return center_img(X)
    if mode == "rot":
        angs = principal_angle(X)
        Xr = np.stack([rotate_img(X[k], -angs[k]) for k in range(len(X))])
        return Xr
    if mode == "center_rot":
        Xc = center_img(X)
        angs = principal_angle(Xc)
        return np.stack([rotate_img(Xc[k], -angs[k]) for k in range(len(X))])
    raise ValueError(mode)


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
    ti = rng.choice(4000, 2000, replace=False)
    Xt, yt = Xte[ti], yte[ti]

    # 测试域
    DOM = {
        "original": lambda X: X,
        "rot+12": lambda X: np.stack([rotate_img(x, 12) for x in X]),
        "rot-12": lambda X: np.stack([rotate_img(x, -12) for x in X]),
        "rot+25": lambda X: np.stack([rotate_img(x, 25) for x in X]),
        "shift2": lambda X: shift_pad(X, 2, -2),
        "shift4": lambda X: shift_pad(X, 4, 0),
        "thin": None,   # 复用 E-25 的实现
    }
    from run_e25_domain_shift import thin
    DOM["thin"] = thin

    modes = ["none", "center", "center_rot", "rot"]

    print("=" * 104)
    print("E-27 对齐方式 × 变换域（HOG128，3000 训练，2000 测试）")
    print("=" * 104)
    print(f"{'对齐方式':<14}", end="")
    for d in DOM:
        print(f"{d:<12}", end="")
    print()
    print("-" * 104)

    rows = []
    for mode in modes:
        print(f"{mode:<14}", end="")
        rec = {"mode": mode}
        # 训练特征
        Ftr = f_hog(align(Xtr, mode))
        M, L = learn_store(Ftr, ytr, N, 0, THR)
        for dname, dfn in DOM.items():
            Fd = f_hog(align(dfn(Xt), mode))
            a = acc_of(M, L, Fd, yt)
            rec[dname] = round(a, 4)
            print(f"{a:<12.4f}", end="")
        print(f"  (星 {len(M)})")
        rows.append(rec)
    print("-" * 104)

    # 逐域最优
    print("\n逐域最优对齐方式：")
    for d in DOM:
        best = max(rows, key=lambda r: r[d])
        orig = rows[0][d]
        print(f"  {d:<12} 最优={best['mode']:<12} {best[d]:.4f}  "
              f"(不对齐 {orig:.4f}, {best[d]-orig:+.4f})")

    print("\n按对齐方式看平均（含所有变换域）：")
    for r in rows:
        vals = [r[d] for d in DOM]
        print(f"  {r['mode']:<14} 均值={np.mean(vals):.4f}  "
              f"最差={min(vals):.4f}({DOM and [d for d in DOM if r[d]==min(vals)][0]})  "
              f"原始域={r['original']:.4f}")

    print(f"\n耗时 {time.time()-t0:.0f}s")

    out = {"experiment": "E-27", "feature": "hog128", "threshold": THR,
           "n_train": N, "n_test": len(Xt), "rows": rows,
           "elapsed_sec": round(time.time() - t0, 1)}
    with open(os.path.join(RESULTS, "e27_alignment.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e27_alignment.json')}")


if __name__ == "__main__":
    main()
