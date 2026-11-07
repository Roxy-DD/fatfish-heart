"""E-270 查清检索为什么 7347ms

E-269 体检发现 max(msSearch) = 7347ms。
但历史上测过 2000 条记忆检索 26.6ms。
必须先分清：
  a) 首次调用含 BGE 模型加载（正常，一次性）
  b) 真实退化（需修）
"""
import sys
import os
import json
import time
import io

_HERE = os.path.dirname(os.path.abspath(__file__))
_MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, _MEM)

print("=" * 76)
print("E-270 查清检索耗时")
print("=" * 76)

# ============ ① 看 debug log 的所有 assemble 记录 ============
debug_log = os.path.join(_MEM, "plugin-debug.jsonl")
print("\n① debug log 里的 assemble 记录")

records = []
if os.path.exists(debug_log):
    with io.open(debug_log, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                e = json.loads(line)
                if e.get("kind") == "assemble":
                    records.append(e)
            except Exception:
                continue

print(f"   共 {len(records)} 条 assemble 记录")
print(f"\n   {'时间':<24} {'msSearch':>10} {'msFallback':>12} {'hits':>5}")
for r in records[-20:]:
    t = r.get("t", "")[:19]
    ms = r.get("msSearch", "?")
    mf = r.get("msFallback", "?")
    hits = r.get("hits", "?")
    flag = " ⚠" if isinstance(ms, (int, float)) and ms > 1000 else ""
    print(f"   {t:<24} {str(ms):>10} {str(mf):>12} {str(hits):>5}{flag}")

# 统计分布
vals = [r.get("msSearch") for r in records
        if isinstance(r.get("msSearch"), (int, float))]
if vals:
    vals_sorted = sorted(vals)
    print(f"\n   统计: n={len(vals)}")
    print(f"     最小 {vals_sorted[0]:.0f}ms")
    print(f"     中位 {vals_sorted[len(vals)//2]:.0f}ms")
    print(f"     最大 {vals_sorted[-1]:.0f}ms")
    print(f"     平均 {sum(vals)/len(vals):.0f}ms")
    slow = [v for v in vals if v > 1000]
    print(f"     >1000ms 的次数: {len(slow)}/{len(vals)}")

# ============ ② 实测：冷启动 vs 热启动 ============
print("\n② 实测：冷启动 vs 热启动")
from store_v2 import MemoryStore

DB = os.path.join(_MEM, "dsh_mem.db")

# 冷启动（新进程内首次）
st = MemoryStore(DB)
t0 = time.perf_counter()
r1 = st.search("记忆系统的检索通道", top=5)
ms1 = (time.perf_counter() - t0) * 1000
print(f"   第 1 次（含模型加载）: {ms1:.0f} ms  → {len(r1)} 条")

# 热启动
times = []
for i in range(5):
    t0 = time.perf_counter()
    st.search("版本回退机制", top=5)
    times.append((time.perf_counter() - t0) * 1000)

print(f"   后续 5 次: " + ", ".join(f"{t:.0f}" for t in times) + " ms")
print(f"   热启动平均: {sum(times)/len(times):.0f} ms")

# ============ ③ 拆解首次调用的耗时构成 ============
print("\n③ 首次调用的耗时构成")
st2 = MemoryStore(DB)

t0 = time.perf_counter()
from store_v1 import Embedder
emb = Embedder.get()
ms_embed_init = (time.perf_counter() - t0) * 1000
print(f"   Embedder.get()（模型加载）: {ms_embed_init:.0f} ms")

t0 = time.perf_counter()
emb.encode("预热")
ms_encode1 = (time.perf_counter() - t0) * 1000
print(f"   首次 encode: {ms_encode1:.0f} ms")

t0 = time.perf_counter()
emb.encode("预热")
ms_encode2 = (time.perf_counter() - t0) * 1000
print(f"   第二次 encode: {ms_encode2:.0f} ms")

t0 = time.perf_counter()
st2._load_vectors(include_invalid=False)
ms_vec = (time.perf_counter() - t0) * 1000
print(f"   _load_vectors: {ms_vec:.0f} ms")

st.con.close()
st2.con.close()

# ============ 结论 ============
print("\n" + "=" * 76)
print("结论")
print("=" * 76)

print(f"""
  7347ms 是否可解释：
    如果 ms1（首次）≈ 7000ms 且后续 < 100ms
    → 是模型加载造成的一次性开销，内核已预热（kernel_server 有 embed warmup）
    → 不需要修，但要确认内核预热生效

  但如果热启动也慢 → 真实退化，必须修
""")

out = {
    "experiment": "E-270",
    "assemble_records": len(records),
    "ms_search_values": vals[-30:] if vals else [],
    "cold_ms": round(ms1, 1),
    "warm_avg_ms": round(sum(times)/len(times), 1) if times else None,
    "embed_init_ms": round(ms_embed_init, 1),
    "encode_first_ms": round(ms_encode1, 1),
    "encode_second_ms": round(ms_encode2, 1),
    "load_vectors_ms": round(ms_vec, 1),
}
p = os.path.join(_HERE, "results", "e270_search_latency.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e270_search_latency.json")
