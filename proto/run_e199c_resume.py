"""
E-199c 继续断点续传 model.onnx（从 73MB 继续，最多 200 次重试）

断点续传已验证有效（73MB > 之前的 2MB），只是网络慢。
不重置文件，从断点继续，重试次数加大。
"""
import os
import time

DEST = r"C:\Users\xuee\Desktop\肥鱼之心\models\bge-reranker-v2-m3-int8"
REPO = "onnx-community/bge-reranker-v2-m3-ONNX"
REMOTE = "onnx/model_int8.onnx"
LOCAL = os.path.join(DEST, "model.onnx")
URL = f"https://huggingface.co/{REPO}/resolve/main/{REMOTE}"
EXPECTED = 570714895

import requests


def download_with_resume(url, dest, max_retries=200, expected=None):
    for attempt in range(max_retries):
        existing = os.path.getsize(dest) if os.path.exists(dest) else 0
        if expected and existing >= expected * 0.99:
            return existing

        headers = {"User-Agent": "research",
                   "Range": f"bytes={existing}-"}
        try:
            with requests.get(url, headers=headers, stream=True,
                              timeout=(30, 120)) as r:
                if r.status_code in (200, 206):
                    with open(dest, "ab" if existing > 0 else "wb") as f:
                        for chunk in r.iter_content(chunk_size=512 * 1024):
                            if chunk:
                                f.write(chunk)
                    sz = os.path.getsize(dest)
                    # 只在有进展时打印，减少日志
                    if attempt % 5 == 0 or sz > (existing + 5 * 1024 * 1024):
                        print(f"    尝试{attempt+1}: {sz:,}/{expected:,} "
                              f"({sz/expected:.1%})")
                else:
                    print(f"    HTTP {r.status_code}")
        except Exception as e:
            if attempt % 10 == 0:
                print(f"    尝试{attempt+1}: {type(e).__name__}")
            time.sleep(5)

    return os.path.getsize(dest) if os.path.exists(dest) else 0


def main():
    print("=" * 94)
    print("E-199c 继续断点续传（最多 200 次重试）")
    print("=" * 94)
    t0 = time.perf_counter()
    sz = download_with_resume(URL, LOCAL, expected=EXPECTED)
    dt = time.perf_counter() - t0
    print(f"\n  最终: {sz:,} bytes, 期望 {EXPECTED:,}")
    print(f"  耗时 {dt:.0f}s")
    if sz >= EXPECTED * 0.99:
        print("  ✅ 完整!")
    else:
        print(f"  ❌ 仍缺 {EXPECTED - sz:,} bytes")


if __name__ == "__main__":
    main()
