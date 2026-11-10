"""E-347 确认：latest 为何是旧消息（而非空）

E-347 发现代码里有 `if (!latest) return result`，
理论上没消息就不注入。但实测注入了 10 次同一句旧 query。

矛盾点：
  recentUserMessages(log, 8, sid) 返回「最近 8 条用户消息」，
  新会话时这 8 条里装的是**历史消息**，不是本轮消息。
  所以 latest 非空 → 通过守卫 → 拿旧消息去检索。

关键问：log 里到底有没有「本轮」的消息？

DSH 的 assemble 在**模型调用前**触发。
如果是会话第一轮，用户的这句话应该已经在 log 里了。
除非：assemble 触发时消息还没进入 log。

验证：按 logLen 和 seq 看 assemble 发生时最新消息是什么。
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
print("E-347 latest 为何是旧消息")
print("=" * 78)

# ---------- ① 读 assemble-scan 记录 ----------
scans = []
if os.path.exists(LOG):
    with open(LOG, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                e = json.loads(line)
                if e.get("kind") == "assemble-scan":
                    scans.append(e)
            except Exception:
                pass

print(f"\n① assemble-scan: {len(scans)} 条")

# ---------- ② recentCount > 0 时的 latest ----------
print("\n② recentCount>0 时的 latest（说明真的提取到消息）")
got = [s for s in scans if s.get("recentCount", 0) > 0]
print(f"   有提取的: {len(got)} 次")
for s in got[-15:]:
    t = str(s.get("t", ""))[:19]
    n = s.get("recentCount")
    lat = str(s.get("latest", ""))[:50]
    print(f"     {t}  n={n:<3} '{lat}'")

# ---------- ③ recentCount==0 时发生了什么 ----------
print("\n③ recentCount==0 的比例")
n0 = len(scans) - len(got)
print(f"   为 0 的: {n0}/{len(scans)} = {n0/max(1,len(scans))*100:.1f}%")

# ---------- ④ 关键：注入时刻的 recentCount ----------
print("\n④ 对照注入时刻")
import sqlite3
DB = os.path.join(MEM, "dsh_mem.db")
con = sqlite3.connect(DB)
injects = con.execute(
    "SELECT at, msg FROM audit_log WHERE kind='inject' ORDER BY at").fetchall()
con.close()

print(f"   inject: {len(injects)} 次")

# 为每个 inject 找最近的 assemble-scan
def to_ts(t):
    try:
        return time.mktime(time.strptime(t[:19], "%Y-%m-%dT%H:%M:%S")) + 8*3600
    except Exception:
        return None

scan_ts = [(to_ts(s.get("t")), s) for s in scans if to_ts(s.get("t"))]

print(f"\n   {'注入时间':<20} {'最近scan的recentCount':>20} {'latest'}")
print("   " + "-" * 72)
mismatch = 0
for at, msg in injects[-12:]:
    if not at:
        continue
    # 找最近的 scan
    near = [s for ts, s in scan_ts if ts and abs(ts - at) < 30]
    if near:
        n = near[-1].get("recentCount")
        lat = str(near[-1].get("latest", ""))[:30]
        flag = "⚠" if n == 0 else " "
        if n == 0:
            mismatch += 1
        print(f"   {time.strftime('%m-%d %H:%M:%S', time.localtime(at)):<20} "
              f"{str(n):>20} {flag} '{lat}'")
    else:
        print(f"   {time.strftime('%m-%d %H:%M:%S', time.localtime(at)):<20} "
              f"{'无对应scan':>20}")

# ---------- ⑤ 分析 ----------
print("\n⑤ 分析")

print(f"""
   注入次数: {len(injects)}
   附近 30 秒内 scan 的 recentCount=0 的次数: {mismatch}

   ★ 关键矛盾：

   如果注入时 recentCount=0（latest 为空），
   代码里 `if (!latest) return result` 应该**直接返回不注入**。
   但实测注入了。

   只有两种可能：
     A. 注入发生在 recentCount>0 的轮次
        → 那 latest 是真实的，但可能是**历史消息**
     B. 代码路径与我读的不同（注入在别处触发）

   必须确认 B：搜 index.js 里所有 inject_log 调用点。
""")

# ---------- ⑥ 所有 inject_log 调用点 ----------
print("⑥ index.js 里所有 inject_log 调用点")
idx = open(os.path.join(os.path.dirname(_HERE), "dsh-plugin", "index.js"),
           encoding="utf-8").read()
import re
for m in re.finditer(r"inject_log", idx):
    start = max(0, m.start() - 400)
    seg = idx[start:m.start() + 200]
    # 找所在的条件上下文
    print("\n   --- 调用点 ---")
    lines = seg.split("\n")
    for line in lines[-14:]:
        s = line.rstrip()
        if s.strip():
            print(f"   {s[:110]}")

out = {
    "experiment": "E-347",
    "n_scans": len(scans),
    "n_recent_nonzero": len(got),
    "ratio_zero": round(n0 / max(1, len(scans)), 3),
    "mismatch": mismatch,
}
p = os.path.join(_HERE, "results", "e347_latest_source.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 results/e347_latest_source.json")
