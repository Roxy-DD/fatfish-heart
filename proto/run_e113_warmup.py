"""E-113 定位 7.2 秒预热的瓶颈：模型加载 vs 首次数值计算"""
import time, sys, os
sys.path.insert(0, r"C:\Users\xuee\Desktop\肥鱼之心\proto\cosmos_mem")
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

t_start = time.time()

print("=" * 70)
print("E-113 内核预热耗时分解")
print("=" * 70)

# 1. import 耗时
t0 = time.time()
import torch
t_torch = (time.time() - t0) * 1000
print(f"1. import torch          : {t_torch:8.0f} ms")

t0 = time.time()
from transformers import AutoTokenizer, AutoModel
t_tf = (time.time() - t0) * 1000
print(f"2. import transformers   : {t_tf:8.0f} ms")

# 3. tokenizer 加载
t0 = time.time()
from store_v1 import Embedder, MODEL_DIR
emb = Embedder.get()
tok = AutoTokenizer.from_pretrained(MODEL_DIR)
t_tok = (time.time() - t0) * 1000
print(f"3. tokenizer 加载        : {t_tok:8.0f} ms")

# 4. model 加载
t0 = time.time()
mdl = AutoModel.from_pretrained(MODEL_DIR).eval()
t_mdl = (time.time() - t0) * 1000
print(f"4. model 加载            : {t_mdl:8.0f} ms")

# 5. 首次 encode（含 warmup）
t0 = time.time()
v = emb.encode("预热测试")
t_enc1 = (time.time() - t0) * 1000
print(f"5. 首次 encode           : {t_enc1:8.0f} ms")

# 6. 第二次 encode（稳态）
t0 = time.time()
v = emb.encode("稳态测试")
t_enc2 = (time.time() - t0) * 1000
print(f"6. 稳态 encode           : {t_enc2:8.0f} ms")

# 7. 建 store（含向量矩阵加载）
t0 = time.time()
from store_v2 import MemoryStore
st = MemoryStore(r"C:\Users\xuee\Desktop\肥鱼之心\proto\cosmos_mem\dsh_mem.db")
t_store = (time.time() - t0) * 1000
print(f"7. MemoryStore 初始化    : {t_store:8.0f} ms")

# 8. 首次 search（含向量矩阵载入）
t0 = time.time()
st.search("预热", top=1)
t_s1 = (time.time() - t0) * 1000
print(f"8. 首次 search           : {t_s1:8.0f} ms")

# 9. 第二次 search
t0 = time.time()
st.search("稳态", top=1)
t_s2 = (time.time() - t0) * 1000
print(f"9. 稳态 search           : {t_s2:8.0f} ms")

print("-" * 70)
print(f"总计                     : {(time.time()-t_start)*1000:8.0f} ms")
print()
print("优化方向判断：")
print(f"  torch import     {t_torch:7.0f} ms  ← 无法避免（一次性）")
print(f"  model 加载       {t_mdl:7.0f} ms  ← 可尝试减小模型或延迟加载")
print(f"  首次 encode      {t_enc1:7.0f} ms  ← 可尝试 ONNX（之前实测快 1.75x）")
print(f"  向量矩阵载入     {t_s1:7.0f} ms  ← 可尝试增量/懒加载")
