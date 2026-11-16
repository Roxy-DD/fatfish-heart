"""E-203b requests 断点续传下载 fp16 model.onnx"""
import os
import time
import requests

DEST = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models", "bge-reranker-v2-m3-fp16")
os.makedirs(DEST, exist_ok=True)
LOCAL = os.path.join(DEST, "model.onnx")
URL = "https://huggingface.co/onnx-community/bge-reranker-v2-m3-ONNX/resolve/main/onnx/model_fp16.onnx"
EXPECTED = 1136212994  # 约 1.14GB

def main():
    print("=" * 94)
    print("E-203b 断点续传下载 fp16 model.onnx")
    print("=" * 94)
    t0 = time.perf_counter()
    last_log = 0

    for attempt in range(400):
        existing = os.path.getsize(LOCAL) if os.path.exists(LOCAL) else 0
        if existing >= EXPECTED * 0.999:
            print(f"\n  ✅ 完整: {existing:,}")
            break
        headers = {"User-Agent": "research", "Range": f"bytes={existing}-"}
        try:
            with requests.get(URL, headers=headers, stream=True,
                              timeout=(30, 180)) as r:
                if r.status_code in (200, 206):
                    with open(LOCAL, "ab" if existing > 0 else "wb") as f:
                        for chunk in r.iter_content(chunk_size=1024*1024):
                            if chunk:
                                f.write(chunk)
                    new_sz = os.path.getsize(LOCAL)
                    if new_sz - last_log >= 50*1024*1024 or attempt % 15 == 0:
                        print(f"    尝试{attempt+1}: {new_sz:,}/{EXPECTED:,} "
                              f"({new_sz/EXPECTED:.1%})")
                        last_log = new_sz
        except Exception as e:
            if attempt % 20 == 0:
                print(f"    尝试{attempt+1}: {type(e).__name__}")
            time.sleep(3)

    sz = os.path.getsize(LOCAL) if os.path.exists(LOCAL) else 0
    dt = time.perf_counter() - t0
    print(f"\n  最终 {sz:,} bytes, 耗时 {dt:.0f}s")
    if sz >= EXPECTED * 0.999:
        print("  ✅ 完整")
    else:
        print(f"  ❌ 缺 {EXPECTED-sz:,}")

if __name__ == "__main__":
    main()
