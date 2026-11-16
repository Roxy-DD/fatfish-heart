"""
E-199e 直连下载 model.onnx（绕过代理 trust_env=False）

发现根因：requests 自动读系统代理 127.0.0.1:7897，大文件走代理不稳定。
直连（绕过代理）返回 200。改用 trust_env=False 强制直连 + 断点续传。
"""
import os
import time

DEST = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models", "bge-reranker-v2-m3-int8")
REPO = "onnx-community/bge-reranker-v2-m3-ONNX"
REMOTE = "onnx/model_int8.onnx"
LOCAL = os.path.join(DEST, "model.onnx")
URL = f"https://huggingface.co/{REPO}/resolve/main/{REMOTE}"
EXPECTED = 570714895

import requests


def main():
    print("=" * 94)
    print("E-199e 直连下载（trust_env=False 绕过代理）")
    print("=" * 94)

    t0 = time.perf_counter()
    last_log = 0

    for attempt in range(300):
        existing = os.path.getsize(LOCAL) if os.path.exists(LOCAL) else 0
        if existing >= EXPECTED * 0.999:
            print(f"\n  ✅ 下载完整: {existing:,} bytes")
            break

        headers = {"User-Agent": "research",
                   "Range": f"bytes={existing}-"}
        try:
            # trust_env=False 强制不走系统代理
            with requests.get(URL, headers=headers, stream=True,
                              timeout=(30, 180), trust_env=False) as r:
                if r.status_code in (200, 206):
                    with open(LOCAL, "ab" if existing > 0 else "wb") as f:
                        for chunk in r.iter_content(chunk_size=1024 * 1024):
                            if chunk:
                                f.write(chunk)
                    new_sz = os.path.getsize(LOCAL)
                    if new_sz - last_log >= 20 * 1024 * 1024 or attempt % 10 == 0:
                        print(f"    尝试{attempt+1}: {new_sz:,}/{EXPECTED:,} "
                              f"({new_sz/EXPECTED:.1%})")
                        last_log = new_sz
                else:
                    print(f"    HTTP {r.status_code}")
        except Exception as e:
            if attempt % 20 == 0:
                print(f"    尝试{attempt+1}: {type(e).__name__} {str(e)[:40]}")
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
