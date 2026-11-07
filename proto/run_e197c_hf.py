"""
E-197c 用 huggingface_hub.hf_hub_download 单文件下载，带多次重试

之前 snapshot_download 和 urllib 都在大文件上 SSL 中断。
hf_hub_download 内部用 requests + 断点续传，可能更稳。
"""
import os
import sys
import time

DEST = r"C:\Users\xuee\Desktop\肥鱼之心\models\bge-reranker-v2-m3"
os.makedirs(DEST, exist_ok=True)

from huggingface_hub import hf_hub_download

FILES = [
    "model.safetensors",
    "sentencepiece.bpe.model",
    "special_tokens_map.json",
]

for fn in FILES:
    print(f"\n下载 {fn} ...")
    for attempt in range(8):
        try:
            p = hf_hub_download(
                repo_id="BAAI/bge-reranker-v2-m3",
                filename=fn,
                local_dir=DEST,
                resume_download=True,
            )
            sz = os.path.getsize(p)
            print(f"  成功: {sz:,} bytes")
            break
        except Exception as e:
            print(f"  尝试 {attempt+1} 失败: {type(e).__name__} {str(e)[:70]}")
            time.sleep(10)

print("\n最终文件:")
for fn in FILES:
    p = os.path.join(DEST, fn)
    if os.path.exists(p):
        print(f"  {fn}: {os.path.getsize(p):,} bytes")
    else:
        print(f"  {fn}: 缺失")
