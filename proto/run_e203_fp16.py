"""E-203 下载 fp16 版 ONNX（GPU 原生支持，替代 int8）"""
import os
import time
import urllib.request

DEST = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models", "bge-reranker-v2-m3-fp16")
REPO = "onnx-community/bge-reranker-v2-m3-ONNX"
os.makedirs(DEST, exist_ok=True)

# fp16 版文件
FILES = [
    ("onnx/model_fp16.onnx", "model.onnx"),
    ("config.json", "config.json"),
    ("tokenizer.json", "tokenizer.json"),
    ("tokenizer_config.json", "tokenizer_config.json"),
    ("special_tokens_map.json", "special_tokens_map.json"),
]

# 复用 int8 已下载的 tokenizer 文件（相同）
SRC_INT8 = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models", "bge-reranker-v2-m3-int8")

def download(url, dest, retries=8):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "research"})
            with urllib.request.urlopen(req, timeout=900) as r:
                data = r.read()
            with open(dest, "wb") as f:
                f.write(data)
            return len(data)
        except Exception as e:
            if attempt % 4 == 0:
                print(f"      尝试{attempt+1}: {type(e).__name__} {str(e)[:40]}")
            time.sleep(5)
    return 0


def main():
    print("=" * 94)
    print("E-203 下载 fp16 ONNX")
    print("=" * 94)

    t0 = time.perf_counter()
    for remote, local in FILES:
        dest = os.path.join(DEST, local)
        # tokenizer 文件从 int8 目录复制（相同文件）
        if local != "model.onnx" and os.path.exists(os.path.join(SRC_INT8, local)):
            import shutil
            shutil.copy(os.path.join(SRC_INT8, local), dest)
            print(f"  {local}: 从 int8 复制")
            continue
        if os.path.exists(dest) and os.path.getsize(dest) > 1000:
            print(f"  {local}: 已存在 {os.path.getsize(dest):,}")
            continue
        url = f"https://huggingface.co/{REPO}/resolve/main/{remote}"
        print(f"  下载 {local} ...", end=" ", flush=True)
        sz = download(url, dest)
        print(f"{sz:,} bytes")

    dt = time.perf_counter() - t0
    print(f"\n  耗时 {dt:.0f}s")
    print("  文件:")
    for _, local in FILES:
        p = os.path.join(DEST, local)
        print(f"    {local}: {os.path.getsize(p):,}" if os.path.exists(p) else f"    {local}: 缺失")


if __name__ == "__main__":
    main()
