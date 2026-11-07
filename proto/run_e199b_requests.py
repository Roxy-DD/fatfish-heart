"""
E-199b 用 requests 分块下载 model.onnx，支持断点续传

urllib 的 read() 一次性读 570MB 到内存，中途断连就全丢。
requests 支持 stream=True 分块写盘，配合 Range 头做断点续传。
"""
import os
import time

DEST = r"C:\Users\xuee\Desktop\肥鱼之心\models\bge-reranker-v2-m3-int8"
REPO = "onnx-community/bge-reranker-v2-m3-ONNX"
REMOTE = "onnx/model_int8.onnx"
LOCAL = os.path.join(DEST, "model.onnx")
URL = f"https://huggingface.co/{REPO}/resolve/main/{REMOTE}"

import requests


def download_with_resume(url, dest, max_retries=20):
    headers = {"User-Agent": "research"}
    existing = os.path.getsize(dest) if os.path.exists(dest) else 0
    if existing:
        headers["Range"] = f"bytes={existing}-"

    for attempt in range(max_retries):
        try:
            with requests.get(url, headers=headers, stream=True,
                              timeout=(30, 300)) as r:
                if r.status_code in (200, 206):
                    mode = "ab" if (r.status_code == 206 or existing > 0) else "wb"
                    with open(dest, mode) as f:
                        for chunk in r.iter_content(chunk_size=1024 * 1024):
                            if chunk:
                                f.write(chunk)
                    sz = os.path.getsize(dest)
                    print(f"    下载到 {sz:,} bytes")
                    return sz
                else:
                    print(f"    HTTP {r.status_code}")
        except Exception as e:
            print(f"    尝试 {attempt+1}: {type(e).__name__} {str(e)[:60]}")
            # 更新断点
            existing = os.path.getsize(dest) if os.path.exists(dest) else 0
            headers["Range"] = f"bytes={existing}-"
            time.sleep(10)
    return os.path.getsize(dest) if os.path.exists(dest) else 0


def main():
    print("=" * 94)
    print("E-199b requests 分块断点续传下载 model.onnx")
    print("=" * 94)

    t0 = time.perf_counter()
    sz = download_with_resume(URL, LOCAL)
    dt = time.perf_counter() - t0

    print(f"\n  最终: {sz:,} bytes, 耗时 {dt:.0f}s")
    # 570,714,895 是 int8 期望大小
    expected = 570714895
    print(f"  期望: {expected:,} bytes")
    if sz >= expected * 0.99:
        print("  ✅ 完整")
    else:
        print(f"  ❌ 不完整，还差 {expected - sz:,} bytes")


if __name__ == "__main__":
    main()
