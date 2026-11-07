"""E-202 GPU 上 cross-encoder 延迟测量（对比 CPU 1598ms/50候选）"""
import sys
import time

sys.path.insert(0, r"C:\Users\xuee\Desktop\肥鱼之心\proto\cosmos_mem")
from rerank_v1 import CrossEncoderReranker

rk = CrossEncoderReranker()
# 触发加载
_ = rk.score("warmup", ["hello world", "another text"])
print("模型已加载，provider:", rk._sess.get_providers())

q = "When did Melanie paint a sunrise?"
cands = [f"candidate {i}: some memory content about Melanie and her activities" 
         for i in range(50)]

# 测 GPU 延迟
t0 = time.perf_counter()
for _ in range(10):
    s = rk.score(q, cands)
dt = (time.perf_counter() - t0) / 10

print(f"\n50 候选一次打分（GPU）: {dt*1000:.0f} ms")
print(f"对比 CPU: 1598 ms")
print(f"加速比: {1598/(dt*1000):.1f}x")
print(f"244 题总耗时估算: {dt*244/60:.1f} 分钟")
