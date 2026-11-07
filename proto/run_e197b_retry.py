"""
E-197b 重试下载缺失文件（分块 + 断点续传）

model.safetensors 2.27GB 之前中断，需要重试。
用 chunked download + 重试机制。
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(_HERE, "results")
DEST = r"C:\Users\xuee\Desktop\肥鱼之心\models\bge-reranker-v2-m3"
REPO = "BAAI/bge-reranker-v2-m3"

# 需要重新下载的文件
FILES = [
    ("model.safetensors", 2270987102),  # 约 2.27GB
    ("sentencepiece.bpe.model", None),
    ("special_tokens_map.json", None),
]


def download_chunked(url, dest, expected=None, retries=5):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "research"})
            with urllib.request.urlopen(req, timeout=900) as r:
                # 断点续传
                existing = os.path.getsize(dest) if os.path.exists(dest) else 0
                if existing > 0:
                    req2 = urllib.request.Request(
                        url, headers={"User-Agent": "research",
                                      "Range": f"bytes={existing}-"})
                    with urllib.request.urlopen(req2, timeout=900) as r2:
                        data = r2.read()
                    with open(dest, "ab") as f:
                        f.write(data)
                    sz = existing + len(data)
                else:
                    data = r.read()
                    with open(dest, "wb") as f:
                        f.write(data)
                    sz = len(data)

                if expected and sz < expected:
                    print(f"    不完整 {sz:,}/{expected:,}，重试...")
                    continue
                return sz
        except Exception as e:
            print(f"    尝试 {attempt+1} 失败: {type(e).__name__} "
                  f"{str(e)[:60]}")
            time.sleep(5)
    return 0


def main():
    os.makedirs(DEST, exist_ok=True)
    print("=" * 94)
    print("E-197b 重试下载缺失文件")
    print("=" * 94)

    t0 = time.perf_counter()
    for fn, expected in FILES:
        url = f"https://huggingface.co/{REPO}/resolve/main/{fn}"
        dest = os.path.join(DEST, fn)
        if os.path.exists(dest) and (expected is None or
                                     os.path.getsize(dest) >= expected):
            print(f"  {fn}: 已完整 {os.path.getsize(dest):,}")
            continue
        print(f"  下载 {fn} ...")
        sz = download_chunked(url, dest, expected)
        print(f"  {fn}: {sz:,} bytes")

    dt = time.perf_counter() - t0
    print(f"\n  耗时 {dt:.0f}s")

    # 校验
    print("\n  最终文件清单:")
    for fn, _ in FILES:
        p = os.path.join(DEST, fn)
        if os.path.exists(p):
            print(f"    {fn}: {os.path.getsize(p):,} bytes")
        else:
            print(f"    {fn}: 缺失!")


if __name__ == "__main__":
    main()
