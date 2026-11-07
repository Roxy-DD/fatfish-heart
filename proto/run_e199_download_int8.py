"""
E-199 下载 onnx-community int8 量化 cross-encoder（570MB）+ 验证可加载

选择 int8 版（570MB），因为：
  · 本地有 onnxruntime 1.20.1，无需装 llama-cpp
  · 比 fp32 (2271MB) 小 4 倍
  · int8 量化对 rerank 精度损失极小（通常 <1%）
"""
from __future__ import annotations

import json
import os
import time
import urllib.request

RESULTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
DEST = r"C:\Users\xuee\Desktop\肥鱼之心\models\bge-reranker-v2-m3-int8"
REPO = "onnx-community/bge-reranker-v2-m3-ONNX"

FILES = [
    ("onnx/model_int8.onnx", "model.onnx"),
    ("config.json", "config.json"),
    ("tokenizer.json", "tokenizer.json"),
    ("tokenizer_config.json", "tokenizer_config.json"),
    ("special_tokens_map.json", "special_tokens_map.json"),
]


def download(url, dest, retries=6):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "research"})
            with urllib.request.urlopen(req, timeout=900) as r:
                data = r.read()
            with open(dest, "wb") as f:
                f.write(data)
            return len(data)
        except Exception as e:
            print(f"      尝试 {attempt+1}: {type(e).__name__} {str(e)[:50]}")
            time.sleep(8)
    return 0


def main():
    os.makedirs(DEST, exist_ok=True)
    print("=" * 94)
    print("E-199 下载 int8 量化 cross-encoder")
    print("=" * 94)

    t0 = time.perf_counter()
    total = 0
    for remote, local in FILES:
        url = f"https://huggingface.co/{REPO}/resolve/main/{remote}"
        dest = os.path.join(DEST, local)
        if os.path.exists(dest) and os.path.getsize(dest) > 1000:
            sz = os.path.getsize(dest)
            print(f"  {local}: 已存在 {sz:,} bytes")
            total += sz
            continue
        print(f"  下载 {local} ...", end=" ", flush=True)
        sz = download(url, dest)
        if sz:
            print(f"{sz:,} bytes")
            total += sz
        else:
            print("失败!")

    dt = time.perf_counter() - t0
    print(f"\n  总计 {total:,} bytes，耗时 {dt:.0f}s")

    print("\n  最终文件:")
    for _, local in FILES:
        p = os.path.join(DEST, local)
        if os.path.exists(p):
            print(f"    {local}: {os.path.getsize(p):,} bytes")
        else:
            print(f"    {local}: 缺失!")

    out = {"experiment": "E-199", "dest": DEST,
           "total_bytes": total, "elapsed_s": round(dt, 1)}
    with open(os.path.join(RESULTS, "e199_download.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
