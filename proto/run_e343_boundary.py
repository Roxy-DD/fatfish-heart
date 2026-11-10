"""E-343 深查：段 3/6/7 各 2 次注入，是同一进程内还是跨进程？

E-342 判定「无 selfreload 仍多次注入 → 可能是真 bug」，
但这个判定有漏洞：我的分段阈值是 10 分钟，
**短时 DSH 重启（<10min）会被切进同一段**，误判为同进程。

要验证：那几段是否真的换了进程？

可用信号：
  ① assemble-enter 的 logLen 突变（新进程 log 从小的 seq 开始）
  ② kernel-ready 记录（每次内核启动都打）
  ③ 端口变更
  ④ plugin-debug 日志里 apply() 的指纹变化（__COSMOS_RELOAD_MARKER__）

最硬的信号：kernel-ready。内核是常驻子进程，
插件重载不一定重启内核，但 DSH 主进程重启必然重启内核。
"""
import sys
import os
import json
import time
import warnings

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
LOG = os.path.join(MEM, "plugin-debug.jsonl")

print("=" * 78)
print("E-343 段 3/6/7 是否跨进程")
print("=" * 78)

# ---------- 读全量日志事件 ----------
events = []
if os.path.exists(LOG):
    with open(LOG, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                e = json.loads(line)
                t = e.get("t")
                if not t:
                    continue
                ts = time.mktime(time.strptime(t[:19], "%Y-%m-%dT%H:%M:%S"))
                events.append((ts, e))
            except Exception:
                pass

print(f"\n① 日志事件总数: {len(events)}")

# ---------- kernel-ready 作为进程启动信号 ----------
kernels = [(ts, e) for ts, e in events if e.get("kind") == "kernel-ready"]
print(f"\n② kernel-ready（内核启动）: {len(kernels)} 次")
for ts, e in kernels[-12:]:
    print(f"     {time.strftime('%m-%d %H:%M:%S', time.localtime(ts))}  "
          f"ms={e.get('ms')}")

# ---------- 关注段 3/6/7 的时间窗 ----------
# 从 E-342 结果手动取（更可靠的是重新算一次）
import sqlite3
DB = os.path.join(MEM, "dsh_mem.db")
con = sqlite3.connect(DB)
injects = con.execute(
    "SELECT at FROM audit_log WHERE kind='inject' ORDER BY at").fetchall()
con.close()
injects = [r[0] for r in injects]

GAP = 600
segs = []
cur = [injects[0]]
for i in range(1, len(injects)):
    if injects[i] - injects[i-1] > GAP:
        segs.append(cur); cur = [injects[i]]
    else:
        cur.append(injects[i])
segs.append(cur)

print(f"\n③ 重新切分: {len(segs)} 段")

# 只看多次注入的段
print("\n④ 多次注入的段：段内是否有 kernel 重启")
for i, seg in enumerate(segs, 1):
    if len(seg) <= 1:
        continue
    t0, t1 = seg[0], seg[-1]
    # 段内（含段开始前 60 秒，因为 kernel 可能在注入前启动）
    k_in = [ts for ts, e in kernels if t0 - 60 <= ts <= t1 + 30]
    print(f"\n   段{i}: {len(seg)} 次注入  "
          f"{time.strftime('%H:%M:%S', time.localtime(t0))} → "
          f"{time.strftime('%H:%M:%S', time.localtime(t1))}")
    print(f"     段内 kernel-ready: {len(k_in)} 次")
    for ts in k_in:
        print(f"       {time.strftime('%H:%M:%S', time.localtime(ts))}")
    # 判定
    if len(k_in) >= len(seg) - 1:
        print(f"     → ✅ 由内核重启解释（跨进程，预期行为）")
    elif len(k_in) == 0:
        print(f"     → ⚠ 无内核重启，同一进程内多次注入")
    else:
        print(f"     → 🟡 部分解释（{len(k_in)} 次重启 vs {len(seg)} 次注入）")

# ---------- ⑤ 检查 assemble-enter 的 logLen 突变 ----------
print("\n⑤ logLen 突变（新 session 或新进程的迹象）")
scans = [(ts, e) for ts, e in events if e.get("kind") == "assemble-scan"]
print(f"   assemble-scan: {len(scans)} 条")

# 在多次注入段的时间窗内找 logLen 突变
for i, seg in enumerate(segs, 1):
    if len(seg) <= 1:
        continue
    t0, t1 = seg[0], seg[-1]
    win = [(ts, e) for ts, e in scans if t0 - 120 <= ts <= t1 + 60]
    if len(win) < 3:
        continue
    lens = [e.get("logLen") for ts, e in win
            if isinstance(e.get("logLen"), int)]
    drops = sum(1 for j in range(1, len(lens)) if lens[j] < lens[j-1] * 0.5)
    print(f"   段{i}: {len(win)} 条 scan, logLen {lens[:3]}..., 突降 {drops} 次")

# ---------- 结论 ----------
print("\n" + "=" * 78)
print("结论")
print("=" * 78)
print("""
  如果多次注入段内都有 kernel-ready →
    说明是 DSH 进程/内核重启导致的重新注入，
    「只注入一次」的设计成立，E-342 的判定是**误判**。

  如果某段内 2 次注入但 0 次内核重启 →
    那才是真正的"同一进程内重复注入"，需要继续查。
""")

out = {
    "experiment": "E-343",
    "kernel_ready_total": len(kernels),
    "n_segments": len(segs),
}
p = os.path.join(_HERE, "results", "e343_process_boundary.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e343_process_boundary.json")
