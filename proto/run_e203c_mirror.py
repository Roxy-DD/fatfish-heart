"""E-203c 用 hf-mirror.com 镜像续传剩余部分（断点续传）"""
import os
import time
import requests

DEST = r"C:\Users\xuee\Desktop\肥鱼之心\models\bge-reranker-v2-m3-fp16"
LOCAL = os.path.join(DEST, "model.onnx")
# 用 hf-mirror 镜像
URL = "https://hf-mirror.com/onnx-community/bge-reranker-v2-m3-ONNX/resolve/main/onnx/model_fp16.onnx"
EXPECTED = 1136212994

def main():
    print("=" * 94)
    print("E-203c hf-mirror 镜像续传剩余部分")
    print("=" * 94)
    sess = requests.Session()
    # 直连镜像（镜像通常不需要代理，且可能代理反而慢）
    sess.trust_env = True
    t0 = time.perf_counter()
    last_log = 0

    for attempt in range(200):
        existing = os.path.getsize(LOCAL) if os.path.exists(LOCAL) else 0
        if existing >= EXPECTED * 0.999:
            print(f"\n  ✅ 完整: {existing:,}")
            break
        headers = {"User-Agent": "research", "Range": f"bytes={existing}-"}
        try:
            with sess.get(URL, headers=headers, stream=True,
                          timeout=(30, 120)) as r:
                if r.status_code in (200, 206):
                    with open(LOCAL, "ab" if existing > 0 else "wb") as f:
                        for chunk in r.iter_content(chunk_size=1024*1024):
                            if chunk:
                                f.write(chunk)
                    new_sz = os.path.getsize(LOCAL)
                    if new_sz - last_log >= 30*1024*1024 or attempt % 10 == 0:
                        print(f"    {new_sz:,}/{EXPECTED:,} ({new_sz/EXPECTED:.1%})")
                        last_log = new_sz
                else:
                    print(f"    HTTP {r.status_code}")
        except Exception as e:
            if attempt % 20 == 0:
                print(f"    尝试{attempt+1}: {type(e).__name__} {str(e)[:40]}")
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
