"""诊断：MobileNetV2 权重加载为何缺 115 层，特征为何退化。"""
import os, sys
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import torch

from retina import WEIGHTS_PATH, build_mobilenet_v2_extractor

model, info = build_mobilenet_v2_extractor()

state = torch.load(WEIGHTS_PATH, map_location="cpu", weights_only=True)
print("=" * 72)
print(f"官方 state_dict: {len(state)} 个 key")
print(f"我的 model     : {len(model.state_dict())} 个 key")
print(f"载入 {info['loaded']}，缺失 {len(info['missing'])}")
print("=" * 72)

print("\n官方 key 前缀分布:")
from collections import Counter
pref = Counter(k.split(".")[0] for k in state)
print(" ", dict(pref))

print("\n官方 features 前 20 个 key:")
for k in list(state.keys())[:20]:
    print("  ", k, tuple(state[k].shape))

print("\n缺失的前 15 个（我的 model 有、官方没匹配上）:")
for k in info["missing"][:15]:
    print("  ", k, tuple(model.state_dict()[k].shape))

print("\n我的 features 前 20 个 key:")
for i, (k, v) in enumerate(model.state_dict().items()):
    if i >= 20: break
    print("  ", k, tuple(v.shape))

# 检查特征是否退化
print("\n" + "=" * 72)
print("特征输出检查")
print("=" * 72)
from retina import load_mnist, Retina
X, y = load_mnist(True)
r = Retina(input_size=96, verbose=False)
with torch.no_grad():
    prep = r._prep(X[:8])
    t = torch.from_numpy(prep)
    feat = r.model(t).numpy()
print("原始特征 [8,1280]:")
print("  逐样本范数:", np.round(np.linalg.norm(feat, axis=1), 4))
print("  全局 min/max:", float(feat.min()), float(feat.max()))
print("  逐维标准差均值:", float(np.mean(np.std(feat, axis=0))))
print("  样本间 cos 矩阵 (前3):")
n = feat / np.maximum(np.linalg.norm(feat, axis=1, keepdims=True), 1e-12)
print(np.round(n[:3] @ n[:3].T, 4))

# 逐层检查是否有全零输出
print("\n逐层输出检查（是否某层崩成全零）:")
with torch.no_grad():
    x = t
    for i, layer in enumerate(r.model.features):
        x = layer(x)
        if i % 4 == 0 or i >= len(r.model.features) - 3:
            print(f"  features[{i:2d}] {type(layer).__name__:20s} out={tuple(x.shape)} "
                  f"min={float(x.min()):+.4f} max={float(x.max()):+.4f} "
                  f"std={float(x.std()):.6f}")
