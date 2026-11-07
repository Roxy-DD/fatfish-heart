"""E-273 7347ms 的真正原因

E-272 排除了 GPU 争用（只慢 1.27x）。

剩下可能：
  a) 内核进程重启 → 冷启动加载模型（一次性）
  b) 向量矩阵需要重建（记忆数量变化后）
  c) SQLite 锁等待
  d) 磁盘 IO 冷缓存
"""
import sys
import os
import json
import io
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, _MEM)

print("=" * 76)
print("E-273 7347ms 的真正原因")
print("=" * 76)

# ---------- ① 看内核进程启动记录 ----------
debug_log = os.path.join(_MEM, "plugin-debug.jsonl")
print("\n① 内核进程启动记录")

kernel_starts = []
with io.open(debug_log, "r", encoding="utf-8", errors="replace") as f:
    for line in f:
        try:
            e = json.loads(line)
            k = e.get("kind", "")
            if "kernel" in k.lower() or "spawn" in k.lower() or "ready" in k.lower():
                kernel_starts.append(e)
        except Exception:
            continue

print(f"   相关记录: {len(kernel_starts)}")
for e in kernel_starts[-10:]:
    print(f"     {e.get('t','')[:19]} {e.get('kind')} {str(e)[:100]}")

# ---------- ② 检查 7347ms 前后的事件 ----------
print("\n② 7347ms 前后的事件序列")

all_events = []
with io.open(debug_log, "r", encoding="utf-8", errors="replace") as f:
    for line in f:
        try:
            all_events.append(json.loads(line))
        except Exception:
            continue

# 找 7347 那条
target_idx = None
for i, e in enumerate(all_events):
    if e.get("kind") == "assemble" and e.get("msSearch") == 7347:
        target_idx = i
        break

if target_idx is not None:
    print(f"   找到目标事件（索引 {target_idx}，共 {len(all_events)} 条）")
    print(f"\n   前后 15 条事件：")
    for i in range(max(0, target_idx - 10), min(len(all_events), target_idx + 6)):
        e = all_events[i]
        mark = " ★" if i == target_idx else "  "
        t = e.get("t", "")[:19]
        k = e.get("kind", "?")
        extra = ""
        if k == "assemble":
            extra = f"msSearch={e.get('msSearch')} hits={e.get('hits')}"
        elif "error" in str(e).lower():
            extra = str(e)[:80]
        print(f"   {mark}{t} {k:<22} {extra}")
else:
    print("   未找到")

# ---------- ③ 实测：内核重启后首次检索 ----------
print("\n③ 实测：模拟内核重启后的首次检索")

from store_v2 import MemoryStore
DB = os.path.join(_MEM, "dsh_mem.db")

# 新进程（当前进程）首次
st = MemoryStore(DB)
t0 = time.perf_counter()
r = st.search("首次检索测试", top=5)
first_ms = (time.perf_counter() - t0) * 1000
print(f"   本进程首次检索: {first_ms:.0f} ms")

# 第二次
t0 = time.perf_counter()
st.search("第二次检索测试", top=5)
second_ms = (time.perf_counter() - t0) * 1000
print(f"   第二次: {second_ms:.0f} ms")

st.con.close()

# ---------- ④ 检查是否有向量重建 ----------
print("\n④ 向量加载耗时（含可能的 npz 重建）")
st2 = MemoryStore(DB)
t0 = time.perf_counter()
st2._load_vectors(include_invalid=False)
load_ms = (time.perf_counter() - t0) * 1000
print(f"   _load_vectors: {load_ms:.0f} ms")
print(f"   向量数: {len(st2._vec) if st2._vec is not None else 0}")
st2.con.close()

# ---------- 结论 ----------
print("\n" + "=" * 76)
print("结论")
print("=" * 76)

print(f"""
  实测首次检索: {first_ms:.0f} ms
  实测第二次:   {second_ms:.0f} ms
  向量加载:     {load_ms:.0f} ms

  如果 first_ms ≈ 7000ms 而 second_ms ≈ 10ms：
    → 7347ms 就是「新进程首次加载 BGE 模型」的一次性开销
    → 内核预热能覆盖，但插件的 callKernel 超时/重连会绕过预热

  真正要确认的是：内核预热是否真的在每次 spawn 后都生效。
""")

out = {
    "experiment": "E-273",
    "first_search_ms": round(first_ms, 1),
    "second_search_ms": round(second_ms, 1),
    "load_vectors_ms": round(load_ms, 1),
    "kernel_start_events": len(kernel_starts),
}
p = os.path.join(_HERE, "results", "e273_7347_cause.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e273_7347_cause.json")
