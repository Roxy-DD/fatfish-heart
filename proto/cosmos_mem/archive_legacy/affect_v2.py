"""
affect_v2 —— 用 BGE 原型锚定替换关键词法（E-64 验证 ρ=0.801）

E-64 结论：
  M1 关键词     ρ=+0.000（完全无效，情绪句全判 0）
  M2 原型锚定   ρ=+0.801（可用）
  M3 情绪词     ρ=+0.149

校准改进（解决 E-64 的"日常无关组偏高 3.30"问题）：
  不用 min-max 归一化（会把最低值拉到 0、把噪声放大），
  改用**绝对尺度**：affect = sigmoid((sh - sl - c) / T)
  c、T 由高低原型的中位差确定，不依赖当批数据。
"""
from __future__ import annotations

import os
import threading

_HERE = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "models", "bge-small-zh")

# 高唤醒原型：描述情境，刻意不含"害怕/开心"这类词
PROTO_HIGH = [
    "那一刻我整个人定在原地，手心全是汗",
    "周围的声音好像突然消失了，只听见自己的心跳",
    "腿在发抖，我一句话都说不出来",
    "我把东西摔在地上，转身就走",
    "眼泪一下就出来了，我控制不住",
    "所有人都看着我，我脑子里一片空白",
    "接到电话的那一刻，我坐在楼梯上很久没有起来",
    "那道疤到现在还在，每次看到都会想起那天",
]
PROTO_LOW = [
    "这份文档已经发到你的邮箱了",
    "会议改到周四上午十点",
    "仓库里还有两百件库存",
    "表格第三列需要填日期",
    "这个参数配置在配置文件里",
    "他今年三十四岁，住在城东",
    "上周二午饭吃的是番茄炒蛋",
    "那件衣服是深蓝色的",
]

_lock = threading.Lock()
_state = {"ready": False, "ch": None, "cl": None, "c": 0.0, "T": 0.05}


def _load():
    if _state["ready"]:
        return
    import numpy as np
    import torch
    from transformers import AutoTokenizer, AutoModel
    torch.set_num_threads(4)
    tok = AutoTokenizer.from_pretrained(MODEL_DIR)
    mdl = AutoModel.from_pretrained(MODEL_DIR).eval()

    def enc(texts):
        with torch.no_grad():
            e = tok(texts, padding=True, truncation=True, max_length=128,
                    return_tensors="pt")
            o = mdl(**e)
        v = o.last_hidden_state[:, 0].numpy().astype("float32")
        n = np.linalg.norm(v, axis=1, keepdims=True); n[n == 0] = 1
        return v / n

    Vh, Vl = enc(PROTO_HIGH), enc(PROTO_LOW)
    ch, cl = Vh.mean(0), Vl.mean(0)
    ch /= np.linalg.norm(ch); cl /= np.linalg.norm(cl)
    # 用原型自身的分布定中心与尺度（不依赖当批数据）
    sh = Vh @ ch; sl = Vl @ cl
    d_hi = float((sh - sl).mean())
    d_lo = float((enc(PROTO_LOW) @ ch - enc(PROTO_LOW) @ cl).mean())
    c = (d_hi + d_lo) / 2.0
    T = max(abs(d_hi - d_lo) / 4.0, 1e-3)
    _state.update({"ready": True, "ch": ch, "cl": cl, "c": c, "T": T})


def encode_raw(texts):
    """暴露编码器，供 store 复用（避免重复加载模型）"""
    import numpy as np
    import torch
    from transformers import AutoTokenizer, AutoModel
    _load()
    global _TOK, _MDL
    try:
        _TOK
    except NameError:
        _TOK = AutoTokenizer.from_pretrained(MODEL_DIR)
        _MDL = AutoModel.from_pretrained(MODEL_DIR).eval()
    if isinstance(texts, str):
        texts = [texts]
    with torch.no_grad():
        e = _TOK(texts, padding=True, truncation=True, max_length=128,
                 return_tensors="pt")
        o = _MDL(**e)
    v = o.last_hidden_state[:, 0].numpy().astype("float32")
    n = np.linalg.norm(v, axis=1, keepdims=True); n[n == 0] = 1
    return v / n


def affect_peak(text: str) -> float:
    """返回 0-1 的触发情绪强度"""
    import numpy as np
    with _lock:
        _load()
        V = encode_raw(text)
        sh = float((V @ _state["ch"])[0])
        sl = float((V @ _state["cl"])[0])
        d = sh - sl - _state["c"]
        # 绝对尺度 sigmoid
        x = d / _state["T"]
        x = max(-30.0, min(30.0, x))
        return float(round(1.0 / (1.0 + np.exp(-x)), 3))


def calibrate_info() -> dict:
    _load()
    return {"c": round(float(_state["c"]), 4),
            "T": round(float(_state["T"]), 4)}
