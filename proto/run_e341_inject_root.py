"""E-341 查清自动注入重复的根因（直查 SQLite audit_log）

E-336 发现：自动注入 26 次，绝大多数用同一句旧 query
  '你能不能去找一下能让你自己重启的插件...'
E-337 读错了数据源（去 plugin-debug.jsonl 找 inject），得到 0 条。

inject 实际写在 SQLite 的 audit_log 表（cmd_inject_log）。
本次直查该表，并核对 extra 字段里的 sid。
"""
import sys
import os
import json
import time
import warnings
import sqlite3
from collections import Counter

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
DB = os.path.join(MEM, "dsh_mem.db")

print("=" * 78)
print("E-341 自动注入重复根因（audit_log 直查）")
print("=" * 78)

con = sqlite3.connect(DB)

# ---------- ① inject 记录总览 ----------
print("\n① inject 记录")
rows = con.execute(
    "SELECT seq, at, sid, ep_id, msg, extra FROM audit_log "
    "WHERE kind='inject' ORDER BY seq").fetchall()
print(f"   总条数: {len(rows)}")

sids = Counter(str(r[2] or "")[:30] for r in rows)
print(f"\n   按 sid 分布:")
for sid, n in sids.most_common():
    print(f"     {n:>3} 次  sid={sid}")

# ---------- ② 每条的内容 ----------
print("\n② 每条 inject 的内容（query 来自 extra）")
for seq, at, sid, ep_id, msg, extra in rows[-20:]:
    t = time.strftime("%H:%M:%S", time.localtime(at)) if at else "?"
    ex = {}
    try:
        ex = json.loads(extra or "{}")
    except Exception:
        pass
    q = str(ex.get("query") or msg or "")[:45]
    print(f"   seq={seq:<4} {t}  sid={str(sid or '')[:18]:<18} query='{q}'")

# ---------- ③ 关键：是不是同一 sid 反复注入 ----------
print("\n③ 同一 sid 是否反复注入")
main_sid, main_n = sids.most_common(1)[0] if sids else ("", 0)
print(f"   最多的 sid: {main_sid}  ({main_n} 次)")

if main_n > 1:
    print("   ⚠ 同一 sid 注入多次 → st.injected 被重置")

    # 看时间跨度
    main_rows = [r for r in rows if str(r[2] or "")[:30] == main_sid]
    ts = [r[1] for r in main_rows if r[1]]
    if ts:
        span = max(ts) - min(ts)
        print(f"   时间跨度: {span/60:.1f} 分钟")
        gaps = [ts[i+1] - ts[i] for i in range(len(ts)-1)]
        print(f"   相邻间隔(秒): {[round(g) for g in gaps[:15]]}")

# ---------- ④ 对照：assemble 触发次数 ----------
print("\n④ 对照 assemble 次数（debug 日志）")

log_path = os.path.join(MEM, "plugin-debug.jsonl")
n_assemble_enter = 0
n_assemble = 0
if os.path.exists(log_path):
    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - 2000000))
        for line in f:
            try:
                e = json.loads(line)
                if e.get("kind") == "assemble-enter":
                    n_assemble_enter += 1
                elif e.get("kind") == "assemble":
                    n_assemble += 1
            except Exception:
                pass
print(f"   assemble-enter: {n_assemble_enter}")
print(f"   assemble(实际注入): {n_assemble}")

# ---------- ⑤ 计算注入率 ----------
print("\n⑤ 注入率")
if n_assemble_enter > 0:
    rate = len(rows) / n_assemble_enter
    print(f"   inject / assemble-enter = {len(rows)}/{n_assemble_enter} = {rate*100:.1f}%")
    print("""
   如果注入率接近 100%，说明**每次 assemble 都在注入**，
   与"只注入一次"的设计矛盾 → st.injected 完全没起作用。
""")

# ---------- ⑥ 查 cmd_inject_log 的调用方 ----------
print("\n⑥ cmd_inject_log 的实现与调用时机")
import re
cli = open(os.path.join(MEM, "cli_mcp.py"), encoding="utf-8").read()
i = cli.find("def cmd_inject_log")
if i >= 0:
    print(cli[i:i+700])

idx = open(os.path.join(os.path.dirname(_HERE), "dsh-plugin", "index.js"),
           encoding="utf-8").read()
# 找 inject_log 调用点
for m in re.finditer(r"inject_log", idx):
    seg = idx[max(0, m.start()-250):m.start()+250]
    print("\n   调用点片段:")
    for line in seg.split("\n"):
        if "inject_log" in line or "injected" in line or "callKernel" in line:
            print(f"     {line.strip()[:100]}")
    break

con.close()

# ---------- 结论 ----------
print("\n" + "=" * 78)
print("结论")
print("=" * 78)

verdict = "unknown"
if main_n > 1:
    verdict = "injected_reset"
    print(f"""
  ❌ 确认：同一 sid（{main_sid}）注入了 {main_n} 次

  st.injected 被反复重置，导致"只注入一次"的设计失效。
  每次都重新检索 + 注入 5 条记忆 → 这正是"耗 token"的直接来源之一。

  可能原因（需继续查）：
    A. 插件重载（selfreload）重建了 apply() 作用域
    B. sid 每次不同（但按 sid 统计显示是同一个）
    C. sessState 被 agent/disposed 清除
""")
else:
    verdict = "multiple_sessions"
    print("""
  ✅ 每个 sid 只注入 1 次，多次注入来自不同 session
     （设计本身没问题）
""")

out = {
    "experiment": "E-341",
    "inject_total": len(rows),
    "n_sids": len(sids),
    "max_per_sid": main_n,
    "assemble_enter": n_assemble_enter,
    "verdict": verdict,
}
p = os.path.join(_HERE, "results", "e341_inject_root.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e341_inject_root.json")
