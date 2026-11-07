"""E-206 fp16 在 GPU 上的延迟测量（对比 int8 1830ms / CPU 1598ms）"""
import sys
import time

sys.path.insert(0, r"C:\Users\xuee\Desktop\肥鱼之心\proto\cosmos_mem")
from rerank_v1 import CrossEncoderReranker

# fp16 模型
rk = CrossEncoderReranker(r"C:\Users\xuee\Desktop\肥鱼之心\models\bge-reranker-v2-m3-fp16")
_ = rk.score("warmup", ["hello world", "another"])
print("provider:", rk._sess.get_providers())

q = "When did Melanie paint a sunrise?"
cands = [f"candidate {i}: some memory content about Melanie activities" 
         for i in range(50)]

# 预热 GPU
_ = rk.score(q, cands)

t0 = time.perf_counter()
for _ in range(10):
    s = rk.score(q, cands)
dt = (time.perf_counter() - t0) / 10

print(f"\n50 候选一次打分（fp16 GPU）: {dt*1000:.0f} ms")
print(f"对比 int8 GPU: 1830 ms")
print(f"对比 CPU:      1598 ms")
print(f"对比 LLM 精排: ~800 ms")
print(f"244 题估算: {dt*244/60:.1f} 分钟")
