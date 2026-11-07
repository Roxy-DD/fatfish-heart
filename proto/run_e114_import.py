"""E-114 验证：用 onnxruntime 替代 transformers 能否消掉 4.7 秒 import 开销"""
import time, sys, os
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

print("=" * 70)
print("E-114 import 开销对比")
print("=" * 70)

# A. transformers 路径
t0 = time.time()
import torch
t_torch = (time.time() - t0) * 1000
t0 = time.time()
import transformers
t_tf = (time.time() - t0) * 1000
print(f"torch + transformers     : {t_torch + t_tf:8.0f} ms  "
      f"(torch {t_torch:.0f} + tf {t_tf:.0f})")

# B. onnxruntime 路径（同一进程已加载 torch，只能看增量）
t0 = time.time()
try:
    import onnxruntime as ort
    t_ort = (time.time() - t0) * 1000
    print(f"onnxruntime              : {t_ort:8.0f} ms")
    print(f"可用 providers: {ort.get_available_providers()}")
except ImportError:
    print("onnxruntime 未安装")
    t_ort = None

print()
print("结论：")
if t_ort:
    print(f"  transformers 总开销 {(t_torch+t_tf)/1000:.1f}s，onnxruntime 仅 {t_ort/1000:.2f}s")
    print(f"  若改用 ONNX，理论可省 {(t_torch+t_tf-t_ort)/1000:.1f}s")
    print(f"  → 但前提是已经导出 ONNX 模型（需要 tokenizer，仍要 transformers）")
print()
print("现实制约：")
print("  · ONNX 推理仍需 tokenizer，而 tokenizer 来自 transformers")
print("  · 除非自己实现 tokenizer，否则 transformers 的 import 开销无法避免")
print("  · 替代方案：换成更轻的 tokenizer 库，或让内核常驻不退（已实现）")
