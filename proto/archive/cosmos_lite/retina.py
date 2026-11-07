"""
MNIST idx 解析 + MobileNetV2（不依赖 torchvision）。

为什么手写 MobileNetV2：
  本机没有 torchvision，且沙箱下 pip 无法清理临时目录。
  但官方预训练权重可从 download.pytorch.org 直接下载，
  按原架构重建后 load_state_dict 即可，效果等同。

架构对照：mobilenet_v2 (torchvision.models.mobilenet_v2, weights=IMAGENET1K_V1)
  - 特征提取到最后的 Conv2dNormActivation(1280) + AdaptiveAvgPool
  - 丢弃 classifier
"""
from __future__ import annotations

import gzip
import os
import struct
import urllib.request
from typing import Optional

import numpy as np

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
WEIGHTS_URL = "https://download.pytorch.org/models/mobilenet_v2-b0353104.pth"
WEIGHTS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mobilenet_v2.pth")


# ---------------------------------------------------------------------------
# MNIST idx 读取
# ---------------------------------------------------------------------------

def _read_idx(path: str) -> np.ndarray:
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rb") as f:
        magic = struct.unpack(">I", f.read(4))[0]
        n_dims = magic & 0xFF
        dims = struct.unpack(">" + "I" * n_dims, f.read(4 * n_dims))
        data = np.frombuffer(f.read(), dtype=np.uint8)
        return data.reshape(dims)


def load_mnist(train: bool = True) -> tuple[np.ndarray, np.ndarray]:
    """返回 (X [N,28,28] uint8, y [N] int)，无需 torchvision。"""
    prefix = "train" if train else "t10k"
    img_path = os.path.join(DATA_DIR, f"{prefix}-images-idx3-ubyte.gz")
    lbl_path = os.path.join(DATA_DIR, f"{prefix}-labels-idx1-ubyte.gz")
    if not os.path.exists(img_path):
        _download(f"{prefix}-images-idx3-ubyte.gz")
        _download(f"{prefix}-labels-idx1-ubyte.gz")
    X = _read_idx(img_path)
    y = _read_idx(lbl_path)
    return X, y


def _download(name: str):
    os.makedirs(DATA_DIR, exist_ok=True)
    url = "https://ossci-datasets.s3.amazonaws.com/mnist/" + name
    dst = os.path.join(DATA_DIR, name)
    print(f"下载 {name} ...")
    urllib.request.urlretrieve(url, dst)
    print(f"  OK {os.path.getsize(dst)} bytes")


# ---------------------------------------------------------------------------
# MobileNetV2（手写，加载 torchvision 官方权重）
# ---------------------------------------------------------------------------

def build_mobilenet_v2_extractor():
    """返回一个 eval 模式、输出 1280 维特征的 torch 模块。"""
    import torch
    import torch.nn as nn

    class ConvBNReLU(nn.Sequential):
        def __init__(self, inp, oup, k=3, s=1, groups=1):
            pad = (k - 1) // 2
            super().__init__(
                nn.Conv2d(inp, oup, k, s, pad, groups=groups, bias=False),
                nn.BatchNorm2d(oup),
                nn.ReLU6(inplace=True),
            )

    class InvertedResidual(nn.Module):
        def __init__(self, inp, oup, stride, expand_ratio):
            super().__init__()
            self.use_res = stride == 1 and inp == oup
            hidden = int(round(inp * expand_ratio))
            layers = []
            if expand_ratio != 1:
                layers.append(ConvBNReLU(inp, hidden, k=1))
            layers.extend([
                ConvBNReLU(hidden, hidden, k=3, s=stride, groups=hidden),
                nn.Conv2d(hidden, oup, 1, 1, 0, bias=False),
                nn.BatchNorm2d(oup),
            ])
            self.conv = nn.Sequential(*layers)

        def forward(self, x):
            return x + self.conv(x) if self.use_res else self.conv(x)

    # torchvision mobilenet_v2 的 inverted_residual_setting：[t, c, n, s]
    # 注意：这是**完整**列表，第一项 [1,16,1,1] 也要进循环（对应 features.1）。
    # 之前手动 append 了它又留在循环里，导致 features.2 起整体错位一格 → 115 层权重没加载上。
    cfg = [
        [1, 16, 1, 1], [6, 24, 2, 2], [6, 32, 3, 2], [6, 64, 4, 2],
        [6, 96, 3, 1], [6, 160, 3, 2], [6, 320, 1, 1],
    ]

    class MobileNetV2Features(nn.Module):
        def __init__(self):
            super().__init__()
            layers = [ConvBNReLU(3, 32, k=3, s=2)]        # features.0
            inp = 32
            for t, c, n, s in cfg:                         # features.1 .. features.17
                for i in range(n):
                    stride = s if i == 0 else 1
                    layers.append(InvertedResidual(inp, c, stride, t))
                    inp = c
            layers.append(ConvBNReLU(inp, 1280, k=1))      # features.18
            self.features = nn.Sequential(*layers)
            self.pool = nn.AdaptiveAvgPool2d(1)
            self.feat_dim = 1280

        def forward(self, x):
            x = self.features(x)
            x = self.pool(x)
            return torch.flatten(x, 1)

    model = MobileNetV2Features()

    # 加载官方权重
    if not os.path.exists(WEIGHTS_PATH):
        print("下载 MobileNetV2 预训练权重 ...")
        urllib.request.urlretrieve(WEIGHTS_URL, WEIGHTS_PATH)
        print(f"  OK {os.path.getsize(WEIGHTS_PATH)} bytes")

    state = torch.load(WEIGHTS_PATH, map_location="cpu", weights_only=True)
    # 官方 state_dict 的 key 前缀是 "features."，且包含 classifier.*，需过滤
    ours = model.state_dict()
    loadable, skipped = {}, []
    for k, v in state.items():
        if not k.startswith("features."):
            skipped.append(k)
            continue
        new_k = k.replace("features.", "features.", 1)
        if new_k in ours and ours[new_k].shape == v.shape:
            loadable[new_k] = v
        else:
            skipped.append(k)

    missing = [k for k in ours if k not in loadable]
    model.load_state_dict(loadable, strict=False)
    model.eval()
    return model, {"loaded": len(loadable), "skipped": len(skipped),
                   "missing": missing, "feat_dim": model.feat_dim}


class Retina:
    """
    冻结的感知器。eval + no_grad：表征不漂移，遗忘的根源不存在。
    设计意图第 7 条。
    """

    # ImageNet 标准归一化
    MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
    STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

    def __init__(self, input_size: int = 96, batch_size: int = 32, verbose: bool = True):
        import torch
        self.torch = torch
        self.input_size = input_size
        self.batch_size = batch_size
        self.model, info = build_mobilenet_v2_extractor()
        if verbose:
            print(f"[Retina] MobileNetV2 权重载入: {info['loaded']} 层, "
                  f"缺失 {len(info['missing'])} 层, feat_dim={info['feat_dim']}")
            if info["missing"]:
                print(f"[Retina] 缺失示例: {info['missing'][:5]}")

    def _prep(self, X: np.ndarray) -> "np.ndarray":
        """X: [N,28,28] uint8 → [N,3,S,S] float32 (ImageNet 归一化)"""
        import torch
        n = len(X)
        arr = X.astype(np.float32) / 255.0                      # [N,28,28]
        arr = np.repeat(arr[:, None, :, :], 3, axis=1)          # [N,3,28,28]
        t = torch.from_numpy(arr)
        # 上采样到网络输入尺寸
        t = torch.nn.functional.interpolate(
            t, size=(self.input_size, self.input_size),
            mode="bilinear", align_corners=False)
        mean = torch.tensor(self.MEAN).view(1, 3, 1, 1)
        std = torch.tensor(self.STD).view(1, 3, 1, 1)
        return ((t - mean) / std).numpy()

    def extract(self, X: np.ndarray, progress_every: int = 20) -> np.ndarray:
        """批量提取特征，返回 [N,1280] float32，逐行 L2 归一化。"""
        import torch
        prepared = self._prep(X)
        out = np.empty((len(X), self.model.feat_dim), dtype=np.float32)
        n_batches = (len(X) + self.batch_size - 1) // self.batch_size
        with torch.no_grad():
            for i in range(n_batches):
                lo = i * self.batch_size
                hi = min(lo + self.batch_size, len(X))
                batch = torch.from_numpy(prepared[lo:hi])
                feats = self.model(batch).numpy()
                # L2 归一化
                norms = np.linalg.norm(feats, axis=1, keepdims=True)
                norms[norms == 0] = 1.0
                out[lo:hi] = feats / norms
                if progress_every and (i + 1) % progress_every == 0:
                    print(f"  {hi}/{len(X)}", flush=True)
        return out


# ---------------------------------------------------------------------------
# 特征缓存
# ---------------------------------------------------------------------------

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")


def cache_path(tag: str) -> str:
    return os.path.join(CACHE_DIR, f"{tag}.npz")


def get_features(train: bool, tag: str, limit: Optional[int] = None,
                 input_size: int = 96) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    提取（或读缓存）特征。返回 (X_feat [N,1280], y [N] str, X_raw [N,28,28] uint8)
    """
    path = cache_path(f"{tag}_{'train' if train else 'test'}_{input_size}"
                      + (f"_n{limit}" if limit else ""))
    os.makedirs(CACHE_DIR, exist_ok=True)

    X, y = load_mnist(train)
    if limit is not None:
        X, y = X[:limit], y[:limit]

    if os.path.exists(path):
        z = np.load(path)
        print(f"[cache] 读到 {path}  ({len(z['feat'])} 条)")
        return z["feat"], z["y"].astype(str), z["raw"]

    print(f"[feat] 提取 {len(X)} 条，input_size={input_size} ...")
    import time
    t0 = time.time()
    retina = Retina(input_size=input_size)
    feats = retina.extract(X)
    dt = time.time() - t0
    print(f"[feat] 完成，{dt:.1f}s ({len(X)/max(dt,1e-9):.1f} 张/秒)")

    np.savez_compressed(path, feat=feats, y=y.astype(np.int64), raw=X)
    return feats, y.astype(str), X


if __name__ == "__main__":
    import time
    print("=== MNIST ===")
    Xtr, ytr = load_mnist(True)
    print(f"train {Xtr.shape} {ytr.shape} 标签分布 {np.bincount(ytr)}")
    Xte, yte = load_mnist(False)
    print(f"test  {Xte.shape}")

    print("\n=== Retina 速度测试（120 张） ===")
    r = Retina(input_size=96)
    t0 = time.time()
    f = r.extract(Xtr[:120], progress_every=0)
    dt = time.time() - t0
    print(f"{dt:.2f}s / 120 张 = {dt/120*1000:.1f} ms/张")
    print(f"特征形状 {f.shape}, 范数 {np.linalg.norm(f, axis=1)[:3]}")

    print("\n=== 判别力初检 ===")
    f300 = r.extract(Xtr[:300], progress_every=0)
    y300 = ytr[:300]
    same, diff = [], []
    for i in range(0, 60, 2):
        for j in range(i + 1, 60):
            s = float(np.dot(f300[i], f300[j]))
            (same if y300[i] == y300[j] else diff).append(s)
    print(f"同类别 cos: 均值 {np.mean(same):.3f}  标准差 {np.std(same):.3f}")
    print(f"异类别 cos: 均值 {np.mean(diff):.3f}  标准差 {np.std(diff):.3f}")
    print(f"可分性 (均值差): {np.mean(same) - np.mean(diff):+.3f}")
