"""E-274 验证预热是否真的有效 + 能否进一步优化

E-273 结论：
  7347ms = 内核冷启动（加载 BGE 模型）时，插件 waitReady 等待的时间。
  这不是 bug —— 插件已经 waitReady(20s)，内核也确实预热了。

但要确认：
  ① 预热是否真的让"第一个真实请求"变快（vs 不预热）
  ② 预热文本长度是否影响效果
  ③ 能否用更彻底的方式预热（如跑多次）
"""
import sys
import os
import time
import json
import subprocess

_HERE = os.path.dirname(os.path.abspath(__file__))
_MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, _MEM)

DB = os.path.join(_MEM, "dsh_mem.db")
print("=" * 76)
print("E-274 验证预热效果")
print("=" * 76)

# ---------- ① 不预热：直接首次检索 ----------
print("\n① 不预热（直接首次检索）")
code_no_warm = f"""
import sys, time
sys.path.insert(0, r'{_MEM}')
from store_v2 import MemoryStore
st = MemoryStore(r'{DB}')
t0 = time.perf_counter()
r = st.search("首次检索测试", top=5)
ms = (time.perf_counter() - t0) * 1000
print(f"RESULT:{{ms:.0f}}")
st.con.close()
"""

# ---------- ② 预热一次：再首次检索 ----------
print("   运行中…")
code_warm1 = f"""
import sys, time
sys.path.insert(0, r'{_MEM}')
from store_v2 import MemoryStore
from store_v1 import Embedder
st = MemoryStore(r'{DB}')
t_w0 = time.perf_counter()
Embedder.get().encode("预热")
warm_ms = (time.perf_counter() - t_w0) * 1000
t0 = time.perf_counter()
r = st.search("首次检索测试", top=5)
ms = (time.perf_counter() - t0) * 1000
print(f"RESULT:{{ms:.0f}} WARM:{{warm_ms:.0f}}")
st.con.close()
"""

# ---------- ③ 预热 + 真实检索预热（更彻底）----------
code_warm2 = f"""
import sys, time
sys.path.insert(0, r'{_MEM}')
from store_v2 import MemoryStore
from store_v1 import Embedder
st = MemoryStore(r'{DB}')
t_w0 = time.perf_counter()
Embedder.get().encode("预热")
st.search("预热", top=1)
warm_ms = (time.perf_counter() - t_w0) * 1000
t0 = time.perf_counter()
r = st.search("首次检索测试", top=5)
ms = (time.perf_counter() - t0) * 1000
print(f"RESULT:{{ms:.0f}} WARM:{{warm_ms:.0f}}")
st.con.close()
"""

env = {**os.environ, "KMP_DUPLICATE_LIB_OK": "TRUE",
       "HF_HOME": r"C:\Users\xuee\Desktop\肥鱼之心\.hf",
       "PYTHONIOENCODING": "utf-8"}


def run_code(code, label):
    r = subprocess.run([sys.executable, "-u", "-c", code],
                       capture_output=True, text=True, env=env,
                       encoding="utf-8", errors="replace", timeout=180)
    out = (r.stdout or "") + (r.stderr or "")
    ms = None
    warm = None
    for line in out.splitlines():
        if line.startswith("RESULT:"):
            parts = line.split()
            ms = float(parts[0].split(":")[1])
            for p in parts[1:]:
                if p.startswith("WARM:"):
                    warm = float(p.split(":")[1])
    print(f"   {label}: 首次检索 {ms:.0f} ms" +
          (f"（预热耗时 {warm:.0f} ms）" if warm else ""))
    return ms, warm


print("② 预热一次再检索")
ms_b, w_b = run_code(code_warm1, "预热encode")
print("③ 预热+真实检索再检索")
ms_c, w_c = run_code(code_warm2, "预热+检索")

# ---------- 对比 ----------
print("\n" + "=" * 76)
print("结论")
print("=" * 76)

out = {
    "experiment": "E-274",
    "warm_encode": {"first_search_ms": ms_b, "warm_ms": w_b},
    "warm_encode_search": {"first_search_ms": ms_c, "warm_ms": w_c},
}
p = os.path.join(_HERE, "results", "e274_warmup.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e274_warmup.json")
