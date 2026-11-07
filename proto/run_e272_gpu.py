"""E-272 验证假设：后台 Python 任务与内核抢 GPU 导致检索变慢

E-271 发现：慢检索 21 次分散在 14 簇，平均 1.5 次/簇。
                           且 16:40 那条 7347ms 正好是我跑大量脚本的时段。

假设：我的后台 Python 任务（每个都加载 BGE 模型占 GPU）
      与内核进程抢显存/算力，导致检索变慢。

验证方法：
  ① 独占环境测检索（没有其他任务）
  ② 并发环境测检索（同时跑另一个加载模型的进程）
  ③ 对比两者差异
"""
import sys
import os
import time
import json
import subprocess
import threading

_HERE = os.path.dirname(os.path.abspath(__file__))
_MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, _MEM)

from store_v2 import MemoryStore
from store_v1 import Embedder

DB = os.path.join(_MEM, "dsh_mem.db")

print("=" * 76)
print("E-272 验证：后台任务是否抢 GPU 导致检索变慢")
print("=" * 76)

# ---------- ① 独占环境 ----------
print("\n① 独占环境（无其他任务）")
st = MemoryStore(DB)
emb = Embedder.get()
emb.encode("预热")  # 先加载好
st.search("预热", top=1)

times_alone = []
for i in range(10):
    t0 = time.perf_counter()
    st.search(f"记忆系统测试查询 {i}", top=5)
    times_alone.append((time.perf_counter() - t0) * 1000)

print(f"   10 次检索: " + ", ".join(f"{t:.0f}" for t in times_alone))
avg_alone = sum(times_alone) / len(times_alone)
print(f"   平均: {avg_alone:.0f} ms")

# ---------- ② 并发环境（起一个占 GPU 的进程）----------
print("\n② 并发环境（另起进程加载 BGE 模型抢 GPU）")

# 启动一个并发脚本
concurrent_script = os.path.join(_HERE, "work", "_gpu_hog.py")
os.makedirs(os.path.dirname(concurrent_script), exist_ok=True)
with open(concurrent_script, "w", encoding="utf-8") as f:
    f.write(f"""
import sys, time
sys.path.insert(0, r'{_MEM}')
from store_v1 import Embedder
emb = Embedder.get()
emb.encode("加载")
# 持续编码，占住 GPU
end = time.time() + 20
n = 0
while time.time() < end:
    emb.encode(f"压力测试文本 {{n}}")
    n += 1
print(f"完成 {{n}} 次编码")
""")

proc = subprocess.Popen(
    [sys.executable, concurrent_script],
    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    env={**os.environ, "KMP_DUPLICATE_LIB_OK": "TRUE",
         "HF_HOME": r"C:\Users\xuee\Desktop\肥鱼之心\.hf"},
)

# 等它加载并开始压力测试
time.sleep(8)

times_busy = []
for i in range(10):
    t0 = time.perf_counter()
    st.search(f"并发测试查询 {i}", top=5)
    times_busy.append((time.perf_counter() - t0) * 1000)

print(f"   10 次检索: " + ", ".join(f"{t:.0f}" for t in times_busy))
avg_busy = sum(times_busy) / len(times_busy)
print(f"   平均: {avg_busy:.0f} ms")

try:
    proc.wait(timeout=25)
except Exception:
    proc.kill()

# ---------- ③ 对比 ----------
print("\n" + "=" * 76)
print("对比结果")
print("=" * 76)

slowdown = avg_busy / avg_alone if avg_alone > 0 else 0
print(f"""
  独占: {avg_alone:>8.1f} ms
  并发: {avg_busy:>8.1f} ms
  变慢: {slowdown:>8.2f}x

  独占最大: {max(times_alone):.0f} ms
  并发最大: {max(times_busy):.0f} ms
""")

if slowdown > 2:
    print("  ⚠ 假设成立：并发任务显著拖慢检索")
    print("     → 我的后台脚本会拖慢真实使用")
    print("     → 修法：后台任务应避免重复加载模型，或使用轻量模式")
elif slowdown > 1.3:
    print("  🟡 有一定影响，但不是主因")
else:
    print("  ✅ 影响很小，慢检索另有原因")

st.con.close()

out = {
    "experiment": "E-272",
    "alone_avg_ms": round(avg_alone, 1),
    "busy_avg_ms": round(avg_busy, 1),
    "slowdown": round(slowdown, 2),
    "alone_times": [round(t, 1) for t in times_alone],
    "busy_times": [round(t, 1) for t in times_busy],
}
p = os.path.join(_HERE, "results", "e272_gpu_contention.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e272_gpu_contention.json")
