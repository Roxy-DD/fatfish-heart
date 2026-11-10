"""E-374 严重问题：修复后 2.5 小时零注入记录

E-361 当时标注「数据不足」，现在过了 2.5 小时、几十轮对话，
仍然**一条新注入都没有**。

这不是数据不足，是真问题：
  · st.injected 在某处被永久置为 true，导致再也不会注入
  · 或者 inject_log 调用路径断了
  · 或者 st.injecting 卡住（E-348 加的并发锁没释放！）

★ 高优先级怀疑：E-348 加的 st.injecting 锁
   注入中途如果抛异常或提前 return，锁没释放
   → guarded 条件 `if (st.injected || st.injecting)` 永久为 true
   → 再也不会注入

查证。
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
print("E-374 为什么 2.5 小时零注入")
print("=" * 78)

# ---------- ① 确认时间差 ----------
print("\n① 时间差确认")

con = sqlite3.connect(DB)
row = con.execute(
    "SELECT MAX(at), COUNT(*) FROM audit_log WHERE kind='inject'").fetchone()
con.close()

last_t, total = row
now = time.time()
print(f"   最后注入: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(last_t))}")
print(f"   现在:     {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now))}")
print(f"   间隔:     {(now - last_t)/3600:.2f} 小时")
print(f"   inject 总数: {total}")

# ---------- ② 这段时间有多少 assemble-enter（说明有对话）----------
print("\n② 这段时间的对话活动")

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


n_enter_after = 0
n_assemble_after = 0
for e in events:
    ts = to_local(str(e.get("t", "")))
    if ts and ts > last_t:
        if e.get("kind") == "assemble-enter":
            n_enter_after += 1
        elif e.get("kind") == "assemble":
            n_assemble_after += 1

print(f"   最后注入之后的 assemble-enter: {n_enter_after} 次")
print(f"   最后注入之后的 assemble:      {n_assemble_after} 次")

if n_enter_after > 50:
    print(f"   → 有大量对话活动，但零注入 → **确认异常**")

# ---------- ③ 关键：检查 guarded 条件 ----------
print("\n③ 检查注入守卫逻辑（index.js）")

IDX = os.path.join(os.path.dirname(_HERE), "dsh-plugin", "index.js")
src = open(IDX, encoding="utf-8").read()

# 找 guard 行
import re
for m in re.finditer(r"if \(st\.injected \|\| st\.injecting\)", src):
    line_no = src[:m.start()].count("\n") + 1
    print(f"   找到守卫 行 {line_no}")

# 找所有 return 路径，看是否在设置 injecting 之后
i = src.find("st.injecting = true")
if i > 0:
    seg = src[i:i + 6000]
    line_start = src[:i].count("\n") + 1
    print(f"\n   st.injecting = true 在行 {line_start}")
    # 找 seg 里的 return
    print(f"\n   之后 6000 字内的 return 语句：")
    for m2 in re.finditer(r"return\b[^\n]*", seg):
        ln = line_start + seg[:m2.start()].count("\n")
        txt = m2.group(0).strip()[:70]
        print(f"     行 {ln}: {txt}")

# 找 st.injecting = false 的位置
print("\n   st.injecting = false 的位置：")
for m3 in re.finditer(r"st\.injecting = false", src):
    ln = src[:m3.start()].count("\n") + 1
    print(f"     行 {ln}")

# ---------- ④ 检查 catch 分支 ----------
print("\n④ catch 分支是否释放锁")
i2 = src.find("assemble-error")
if i2 > 0:
    seg2 = src[max(0, i2 - 400):i2 + 800]
    print("   " + seg2.replace("\n", "\n   ")[:900])

# ---------- ⑤ 结论 ----------
print("\n" + "=" * 78)
print("结论")
print("=" * 78)

print(f"""
  {(now - last_t)/3600:.1f} 小时零注入
  期间 assemble-enter: {n_enter_after} 次

  {"❌ 确认异常：有对话但零注入" if n_enter_after > 50 else "数据不足"}

  ★ 最可能的原因（按可能性排序）：
    1. st.injecting 卡在 true（E-348 加的并发锁，某条路径没释放）
    2. st.injected 被永久置 true（正常设计，但那样应该有 1 次注入）
    3. inject_log 调用被异常吞掉

  注意：如果是原因 1，那是我自己在 E-348 引入的回归。
""")

out = {
    "experiment": "E-374",
    "last_inject": last_t,
    "hours_since": round((now - last_t) / 3600, 2),
    "n_enter_after": n_enter_after,
    "n_assemble_after": n_assemble_after,
    "total_inject": total,
}
p = os.path.join(_HERE, "results", "e374_no_inject.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e374_no_inject.json")
