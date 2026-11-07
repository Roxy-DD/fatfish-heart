"""E-204 测量当前 embedding 向量化延迟（CPU，bge-small-zh）"""
import sys
import time

sys.path.insert(0, r"C:\Users\xuee\Desktop\肥鱼之心\proto\cosmos_mem")
from store_v1 import Embedder

emb = Embedder.get()

texts = ["我电脑的登录密码是8848", "爷爷去世了那阵子很痛苦"] * 25  # 50 条
print(f"编码 {len(texts)} 条文本...")

# 预热
emb.encode(["warmup"])

t0 = time.perf_counter()
vecs = emb.encode(texts)
dt = time.perf_counter() - t0

print(f"50 条文本向量化: {dt*1000:.0f} ms")
print(f"平均: {dt*1000/len(texts):.1f} ms/条")
print(f"vec shape: {vecs.shape}")
