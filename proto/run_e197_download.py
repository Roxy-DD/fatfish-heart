"""
E-197 下载 bge-reranker-v2-m3 cross-encoder 模型

用途：精排（rerank），替代 LLM 做相关性打分。
模型：BAAI/bge-reranker-v2-m3（多语种，支持中英文）
大小：约 1.1GB
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(_HERE, "results")
DEST = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models", "bge-reranker-v2-m3")

REPO = "BAAI/bge-reranker-v2-m3"
FILES = [
    "config.json",
    "model.safetensors",
    "tokenizer.json",
    "tokenizer_config.json",
    "sentencepiece.bpe.model",
    "special_tokens_map.json",
]


def download(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": "research"})
    with urllib.request.urlopen(req, timeout=600) as r:
        data = r.read()
    with open(dest, "wb") as f:
        f.write(data)
    return len(data)


def main():
    os.makedirs(DEST, exist_ok=True)
    print("=" * 94)
    print("E-197 下载 bge-reranker-v2-m3")
    print("=" * 94)

    t0 = time.perf_counter()
    total = 0
    for fn in FILES:
        url = f"https://huggingface.co/{REPO}/resolve/main/{fn}"
        dest = os.path.join(DEST, fn)
        if os.path.exists(dest) and os.path.getsize(dest) > 1000:
            sz = os.path.getsize(dest)
            print(f"  {fn}: 已存在 {sz:,} bytes")
            total += sz
            continue
        try:
            print(f"  下载 {fn} ...", end=" ")
            sz = download(url, dest)
            print(f"{sz:,} bytes")
            total += sz
        except Exception as e:
            print(f"失败: {e}")

    dt = time.perf_counter() - t0
    print(f"\n  总计 {total:,} bytes，耗时 {dt:.0f}s")

    out = {"experiment": "E-197", "dest": DEST,
           "total_bytes": total, "elapsed_s": round(dt, 1)}
    with open(os.path.join(RESULTS, "e197_download.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"写入 {os.path.join(RESULTS, 'e197_download.json')}")


if __name__ == "__main__":
    main()
