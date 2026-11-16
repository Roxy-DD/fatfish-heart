"""
E-46 线程数对编码延迟的影响（这是 E-43 与 E-45 差异的来源）

现象：
  E-43 测到 4.61 ms（脚本里设了 torch.set_num_threads(4)）
  E-45 测到 8.16 ms（默认 14 线程）
  同一模型、同一机器、同一文本 → 差 1.77 倍

假设：小 batch 推理时，线程同步开销 > 并行收益。
      线程越多，越慢。

本实验：
  扫描线程数 1,2,4,6,8,12,14,20
  对单条 / 批8 / 批32 三种情况分别测
  同时测 ONNX Runtime 的 intra_op_num_threads

这是最便宜的优化：不改模型、不改代码逻辑，只改一个参数。
"""
from __future__ import annotations

import json
import os
import sys
import time

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
os.environ["HF_HOME"] = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".hf")
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import numpy as np

MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models", "bge-small-zh")
RESULTS = os.path.join(_HERE, "results")
DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "tnews")


def load_texts(n=500):
    import pandas as pd
    paths = []
    for root, _, files in os.walk(DATA):
        for f in files:
            if f.endswith(".parquet"):
                paths.append(os.path.join(root, f))
    df = pd.concat([pd.read_parquet(p) for p in sorted(paths)], ignore_index=True)
    tcol = "sentence" if "sentence" in df.columns else df.columns[0]
    return df[tcol].astype(str).tolist()[:n]


def median_ms(fn, repeat=30, warmup=8):
    for _ in range(warmup):
        fn()
    ts = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t0) * 1000)
    a = np.array(ts)
    return float(np.median(a)), float(np.percentile(a, 10)), float(np.percentile(a, 90))


def main():
    import torch
    from transformers import AutoTokenizer, AutoModel

    print("=" * 92)
    print("E-46 线程数对编码延迟的影响")
    print("=" * 92)

    texts = load_texts(500)
    tok = AutoTokenizer.from_pretrained(MODEL_DIR)
    mdl = AutoModel.from_pretrained(MODEL_DIR).eval()

    def embed(batch):
        enc = tok(batch, padding=True, truncation=True, max_length=128, return_tensors="pt")
        with torch.no_grad():
            o = mdl(**enc)
        v = o.last_hidden_state[:, 0].numpy()
        n = np.linalg.norm(v, axis=1, keepdims=True); n[n == 0] = 1
        return v / n

    q1 = [texts[0]]
    b8 = texts[:8]
    b32 = texts[:32]

    print(f"\n{'线程':<8}{'单条ms':<12}{'批8每条ms':<14}{'批32每条ms':<14}{'吞吐条/秒'}")
    print("-" * 92)
    rows = []
    for nt in [1, 2, 4, 6, 8, 12, 14, 20]:
        torch.set_num_threads(nt)
        m1, p10, p90 = median_ms(lambda: embed(q1), repeat=30, warmup=8)
        m8, _, _ = median_ms(lambda: embed(b8), repeat=15, warmup=3)
        m32, _, _ = median_ms(lambda: embed(b32), repeat=10, warmup=2)
        thr = 1000.0 / (m8 / 8)      # 按批8 折算的吞吐
        rows.append({"threads": nt, "single_ms": round(m1, 3),
                     "single_p10": round(p10, 3), "single_p90": round(p90, 3),
                     "b8_per_ms": round(m8 / 8, 3),
                     "b32_per_ms": round(m32 / 32, 3),
                     "throughput": round(thr, 1)})
        print(f"{nt:<8}{m1:<12.2f}{m8/8:<14.2f}{m32/32:<14.2f}{thr:<10.0f}")
    print("-" * 92)

    best_single = min(rows, key=lambda r: r["single_ms"])
    best_thr = max(rows, key=lambda r: r["throughput"])
    print(f"  单条最快: {best_single['threads']} 线程 → {best_single['single_ms']:.2f} ms")
    print(f"  吞吐最高: {best_thr['threads']} 线程 → {best_thr['throughput']:.0f} 条/秒")

    # 对照：默认 14 线程 vs 最优
    d14 = next(r for r in rows if r["threads"] == 14)
    print(f"\n  14线程(默认) vs {best_single['threads']}线程(最优): "
          f"{d14['single_ms']:.2f} → {best_single['single_ms']:.2f} ms "
          f"（{d14['single_ms']/best_single['single_ms']:.2f}× 加速）")

    out = {"experiment": "E-46", "rows": rows,
           "best_single_threads": best_single["threads"],
           "best_single_ms": best_single["single_ms"],
           "best_throughput_threads": best_thr["threads"],
           "best_throughput": best_thr["throughput"],
           "speedup_vs_14": round(d14["single_ms"] / best_single["single_ms"], 2)}
    with open(os.path.join(RESULTS, "e46_threads.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e46_threads.json')}")


if __name__ == "__main__":
    main()
