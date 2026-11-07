"""
E-198 探测 GGUF 量化版 cross-encoder 的确切位置与加载方式
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

RESULTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")


def probe_api(url, name):
    print(f"\n[{name}]")
    print(f"  {url[:90]}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "research"})
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.loads(r.read().decode("utf-8"))
        print(f"  ✅ id={d.get('id')}")
        siblings = d.get("siblings", [])
        print(f"  files ({len(siblings)}):")
        for s in siblings:
            fn = s.get("rfilename", "")
            if any(k in fn for k in ("gguf", "GGUF", "safetensors")):
                print(f"    {fn}")
        return True
    except Exception as e:
        print(f"  ❌ {type(e).__name__}: {str(e)[:80]}")
        return False


def main():
    print("=" * 94)
    print("E-198 探测 GGUF 量化版 cross-encoder")
    print("=" * 94)

    # 候选 repo
    candidates = [
        ("gpustack/bge-reranker-v2-m3-GGUF",
         "https://huggingface.co/api/models/gpustack/bge-reranker-v2-m3-GGUF"),
        ("second-state/BGE-Reranker-v2-m3-GGUF",
         "https://huggingface.co/api/models/second-state/BGE-Reranker-v2-m3-GGUF"),
        ("onnx-community/bge-reranker-v2-m3-ONNX",
         "https://huggingface.co/api/models/onnx-community/bge-reranker-v2-m3-ONNX"),
    ]

    ok = []
    for name, url in candidates:
        if probe_api(url, name):
            ok.append(name)

    print("\n" + "=" * 94)
    print("结论")
    print("=" * 94)
    print(f"\n  可达: {len(ok)}/{len(candidates)}")
    for n in ok:
        print(f"    ✅ {n}")

    # 检查本地 llama-cpp-python
    print(f"\n  本地加载 GGUF 的库:")
    for lib in ["llama_cpp", "llama_cpp_python"]:
        try:
            __import__(lib)
            print(f"    ✅ {lib} 可用")
        except ImportError:
            print(f"    ❌ {lib} 不可用")


if __name__ == "__main__":
    main()
