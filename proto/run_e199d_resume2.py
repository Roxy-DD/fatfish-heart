"""
E-199d 继续断点续传 model.onnx（从 104MB 继续，500 次重试）

断点续传已验证有效（2MB→73MB→104MB），只是网络慢。
不重置文件，从断点继续。每 20 次重试打印一次进度。
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


def main():
    print("=" * 94)
    print("E-199d 继续断点续传（500 次重试）")
    print("=" * 94)

    t0 = time.perf_counter()
    last_log = 0

    for attempt in range(500):
        existing = os.path.getsize(LOCAL) if os.path.exists(LOCAL) else 0
        if existing >= EXPECTED * 0.999:
            print(f"\n  ✅ 下载完整: {existing:,} bytes")
            break

        headers = {"User-Agent": "research",
                   "Range": f"bytes={existing}-"}
        try:
            with requests.get(URL, headers=headers, stream=True,
                              timeout=(30, 180)) as r:
                if r.status_code in (200, 206):
                    with open(LOCAL, "ab" if existing > 0 else "wb") as f:
                        for chunk in r.iter_content(chunk_size=1024 * 1024):
                            if chunk:
                                f.write(chunk)
                    # 每推进 10MB 或每 20 次打印
                    new_sz = os.path.getsize(LOCAL)
                    if new_sz - last_log >= 10 * 1024 * 1024 or attempt % 20 == 0:
                        print(f"    尝试{attempt+1}: {new_sz:,}/{EXPECTED:,} "
                              f"({new_sz/EXPECTED:.1%})")
                        last_log = new_sz
                else:
                    print(f"    HTTP {r.status_code}")
        except Exception as e:
            if attempt % 20 == 0:
                print(f"    尝试{attempt+1}: {type(e).__name__}")
            time.sleep(3)

    sz = os.path.getsize(LOCAL) if os.path.exists(LOCAL) else 0
    dt = time.perf_counter() - t0
    print(f"\n  最终: {sz:,} bytes, 期望 {EXPECTED:,}, 耗时 {dt:.0f}s")
    if sz >= EXPECTED * 0.999:
        print("  ✅ 完整!")
    else:
        print(f"  ❌ 仍缺 {EXPECTED - sz:,} bytes")


if __name__ == "__main__":
    main()
