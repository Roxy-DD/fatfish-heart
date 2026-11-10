"""E-376 真问题：同一会话注入 24 次，st.injected 被反复重置

E-375 数据：
  sid=session-7413467a  24 次注入  ← 设计上应 1 次
  sid=session-e7348c8d   4 次

「每个会话只注入一次」的设计失效了。

可能原因：
  1. agent/disposed 触发 → sessionsMap().delete(sid) → 状态丢失
  2. sid 变了（但日志显示是同一个 sid）
  3. 插件重载导致 sessState 重建（E-307 已改 globalThis，理论不会）
  4. DSH 进程重启（globalThis 清空，必然重置）

关键区分：
  · 如果 24 次分布在多次进程重启之间 → 正常（每次重启后重新注入一次）
  · 如果同一进程内注入多次 → 真 bug

用 kernel-ready 作为进程边界信号。
"""
import sys
import os
import json
import time
import warnings
import sqlite3

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
DB = os.path.join(MEM, "dsh_mem.db")
LOG = os.path.join(MEM, "plugin-debug.jsonl")

print("=" * 78)
print("E-376 同一会话 24 次注入：进程边界分析")
print("=" * 78)

# ---------- ① 取当前会话的所有注入时间 ----------
con = sqlite3.connect(DB)
rows = con.execute(
    "SELECT at, extra FROM audit_log WHERE kind='inject' "
    "AND sid LIKE '%7413467a%' ORDER BY at").fetchall()
con.close()

inject_times = [r[0] for r in rows if r[0]]
print(f"\n① 当前会话注入: {len(inject_times)} 次")
for t in inject_times[:5]:
    print(f"   {time.strftime('%m-%d %H:%M:%S', time.localtime(t))}")
print("   ...")
for t in inject_times[-5:]:
    print(f"   {time.strftime('%m-%d %H:%M:%S', time.localtime(t))}")

# ---------- ② kernel-ready 作为进程边界 ----------
print("\n② kernel-ready（内核启动 = 进程边界信号）")

events = []
if os.path.exists(LOG):
    with open(LOG, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                events.append(json.loads(line))
            except Exception:
                pass


def to_local(t):
    try:
        return time.mktime(time.strptime(t[:19], "%Y-%m-%dT%H:%M:%S")) + 8*3600
    except Exception:
        return None


kernels = []
for e in events:
    if e.get("kind") == "kernel-ready":
        ts = to_local(str(e.get("t", "")))
        if ts:
            kernels.append(ts)
kernels.sort()

print(f"   kernel-ready: {len(kernels)} 次")
for t in kernels[:5]:
    print(f"   {time.strftime('%m-%d %H:%M:%S', time.localtime(t))}")
if len(kernels) > 10:
    print(f"   ... 共 {len(kernels)} 次")

# ---------- ③ 按进程边界切分注入 ----------
print("\n③ 每次注入归属哪个进程段")

def seg_of(t):
    """返回该时间点属于第几个 kernel 段（0 = 第一个 kernel 之前）"""
    n = 0
    for k in kernels:
        if k <= t:
            n += 1
        else:
            break
    return n

from collections import Counter
seg_counts = Counter(seg_of(t) for t in inject_times)
print(f"   进程段数（按 kernel-ready 切）: {len(seg_counts)}")
print(f"\n   {'段':>4} {'注入次数':>8}  首个 kernel 时间")
print("   " + "-" * 50)
for seg in sorted(seg_counts):
    n = seg_counts[seg]
    if seg == 0:
        kt = "(首个 kernel 之前)"
    elif seg <= len(kernels):
        kt = time.strftime("%m-%d %H:%M:%S",
                           time.localtime(kernels[seg - 1]))
    else:
        kt = "?"
    print(f"   {seg:>4} {n:>8}  {kt}")

# ---------- ④ 判定 ----------
print("\n④ 判定")

max_per_seg = max(seg_counts.values()) if seg_counts else 0
print(f"   单段内最多注入: {max_per_seg} 次")

if max_per_seg <= 1:
    print(f"""
   ✅ 每个进程段内最多 1 次 → **设计成立**

   24 次是跨 {len(seg_counts)} 个进程段的历史累计。
   每次 DSH 重启后 globalThis 清空，重新注入一次，这是正确行为。
""")
    verdict = "by_design"
else:
    multi = {s: n for s, n in seg_counts.items() if n > 1}
    print(f"""
   ⚠ 有 {len(multi)} 个进程段内注入多次：{multi}

   这才可能是真 bug（同一进程内 st.injected 被重置）。
   需要进一步查：agent/disposed 是否在段内触发。
""")
    verdict = "real_bug"

# ---------- ⑤ 顺带检查另一个会话 ----------
print("\n⑤ 另一个会话的分布")
con = sqlite3.connect(DB)
rows2 = con.execute(
    "SELECT at FROM audit_log WHERE kind='inject' "
    "AND sid LIKE '%e7348c8d%' ORDER BY at").fetchall()
con.close()
t2 = [r[0] for r in rows2 if r[0]]
c2 = Counter(seg_of(t) for t in t2)
print(f"   session-e7348c8d: {len(t2)} 次，分布 {dict(c2)}")

out = {
    "experiment": "E-376",
    "n_kernels": len(kernels),
    "n_segments": len(seg_counts),
    "max_per_seg": max_per_seg,
    "verdict": verdict,
}
p = os.path.join(_HERE, "results", "e376_segments.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 results/e376_segments.json")
