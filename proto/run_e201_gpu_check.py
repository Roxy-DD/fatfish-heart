"""E-201 验证 onnxruntime-gpu 能否识别 GPU 并加载 int8 模型"""
import sys
import time

sys.path.insert(0, r"C:\Users\xuee\Desktop\肥鱼之心\proto\cosmos_mem")

import onnxruntime as ort

print("onnxruntime:", ort.__version__)
print("providers:", ort.get_available_providers())

# 检查 GPU 是否可用
gpu_ok = "CUDAExecutionProvider" in ort.get_available_providers()
print("CUDAExecutionProvider 可用:", gpu_ok)

if gpu_ok:
    MODEL_DIR = r"C:\Users\xuee\Desktop\肥鱼之心\models\bge-reranker-v2-m3-int8"
    sess = ort.InferenceSession(
        MODEL_DIR + r"\model.onnx",
        providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
    print("GPU 加载成功, 实际 provider:", sess.get_providers())
else:
    print("❌ CUDAExecutionProvider 不可用")
