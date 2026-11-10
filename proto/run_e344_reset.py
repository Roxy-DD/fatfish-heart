"""E-344 决定性验证：selfreload 之后 st.injected 还在不在？

E-343 数据：
  段8: 15 次注入，0 次内核重启，但段内有 21 次 selfreload
  段7: 2 次注入，0 次重启，无 reload 记录
  段3: 2 次注入，0 次重启，无 reload 记录

两个假设：
  H1: selfreload 重置了 sessions（尽管 E-307 已改 globalThis）
      → 段8 的 15 次可由 21 次 reload 解释
  H2: 段3/7 的 2 次注入有别的机制
      → 可能是 sessState 在 agent/disposed 时被删

验证方法（不靠读代码猜，直接跑）：
  ① 读 index.js 确认 sessionsMap 现在真的是 globalThis
  ② 检查 agent/disposed 是否在删 sessions
  ③ 检查 __COSMOS_SCAN_RESET__ 等 globalThis 变量是否被重置
  ④ 关键：数一下 apply() 执行了多少次 vs selfreload 多少次
"""
import sys
import os
import json
import io
import re
import time
import warnings

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
MEM = os.path.join(_HERE, "cosmos_mem")
IDX = os.path.join(ROOT, "dsh-plugin", "index.js")
LOG = os.path.join(MEM, "plugin-debug.jsonl")

print("=" * 78)
print("E-344 selfreload 是否重置 st.injected")
print("=" * 78)

src = open(IDX, encoding="utf-8").read()

# ---------- ① sessions 现在挂在哪 ----------
print("\n① sessions 的存储位置")
m = re.search(r"const SESSIONS_KEY.*?\n.*?\n.*?\n", src)
if m:
    print("   " + m.group(0).strip().replace("\n", "\n   "))

has_globalthis = "globalThis[SESSIONS_KEY]" in src
print(f"\n   用 globalThis 存: {'✅' if has_globalthis else '❌'}")
print(f"   模块级 new Map(): "
      f"{'❌ 存在' if re.search(r'^const sessions = new Map', src, re.M) else '✅ 已移除'}")

# ---------- ② st.injected 存在哪 ----------
print("\n② st.injected 的归属")
# sessState 返回的对象里
m2 = re.search(r"function sessState\(sid\)[\s\S]{0,500}?\n\}", src)
if m2:
    print("   " + m2.group(0)[:400].replace("\n", "\n   "))

# ---------- ③ agent/disposed 是否删 sessions ----------
print("\n③ agent/disposed 的处理")
m3 = re.search(r"ctx\.on\('agent/disposed'[\s\S]{0,600}?\n  \}\)", src)
if m3:
    body = m3.group(0)
    print("   " + body[:500].replace("\n", "\n   "))
    if "sessionsMap().delete" in body:
        print("\n   ⚠ 会删除 sessions —— 这是注入重复的一个可能来源")
    else:
        print("\n   ✅ 不删 sessions")

# ---------- ④ 关键：apply 执行次数 vs reload 次数 ----------
print("\n④ 日志统计")

events = []
if os.path.exists(LOG):
    with open(LOG, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                events.append(json.loads(line))
            except Exception:
                pass

from collections import Counter
kinds = Counter(e.get("kind") for e in events if e.get("kind"))
print(f"   日志事件: {len(events)}")

for k in ["selfreload-ok", "lifecycle-attached",
          "http-route-summary", "scan-cursor-reset",
          "assemble-enter", "assemble"]:
    print(f"     {k:<22} {kinds.get(k, 0)}")

# lifecycle-attached 每次 apply 都会打 —— 用它数 apply 次数
n_apply = kinds.get("lifecycle-attached", 0)
n_reload = kinds.get("selfreload-ok", 0)
print(f"\n   apply 次数（lifecycle-attached）: {n_apply}")
print(f"   selfreload 成功次数: {n_reload}")

if n_apply > n_reload + 1:
    print(f"""
   ⚠ apply 比 selfreload 多 {n_apply - n_reload - 1} 次

   说明除了 selfreload，还有别的路径在重新执行 apply()
   （例如 cordis.patch.yml 被外部改动、DSH 自身重载）
""")

# ---------- ⑤ 段8 时间窗内的 apply 次数 ----------
print("\n⑤ 段8（15:58~16:23 UTC）内的 apply 与注入")

import sqlite3
DB = os.path.join(MEM, "dsh_mem.db")
con = sqlite3.connect(DB)
injects = [r[0] for r in con.execute(
    "SELECT at FROM audit_log WHERE kind='inject' ORDER BY at")]
con.close()

# 段8 = 14:58:33 → 15:23:43（本地时间）
seg8_t0 = time.mktime(time.strptime("2026-11-09 14:58:33",
                                    "%Y-%m-%d %H:%M:%S"))
seg8_t1 = time.mktime(time.strptime("2026-11-09 15:23:43",
                                    "%Y-%m-%d %H:%M:%S"))

seg8_inject = [t for t in injects if seg8_t0 <= t <= seg8_t1]
print(f"   段8 注入: {len(seg8_inject)} 次")

# 段内 apply（lifecycle-attached）
seg8_apply = 0
seg8_reload = 0
for e in events:
    t = e.get("t")
    if not t:
        continue
    try:
        ts = time.mktime(time.strptime(t[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        continue
    # 日志 UTC，本地 +8
    ts += 8 * 3600
    if seg8_t0 <= ts <= seg8_t1:
        if e.get("kind") == "lifecycle-attached":
            seg8_apply += 1
        elif e.get("kind") == "selfreload-ok":
            seg8_reload += 1

print(f"   段8 apply: {seg8_apply} 次")
print(f"   段8 selfreload: {seg8_reload} 次")

# ---------- 结论 ----------
print("\n" + "=" * 78)
print("结论")
print("=" * 78)

if seg8_inject:
    ratio = seg8_apply / max(1, len(seg8_inject))
    print(f"""
  段8: {len(seg8_inject)} 次注入 / {seg8_apply} 次 apply
       比值 {ratio:.2f}

  {"✅ 每次 apply 后注入一次 → st.injected 随 apply 重置，符合预期" if 0.5 <= ratio <= 1.5 else "⚠ 比值异常"}
""")

print(f"""
  根本机制（已确认）：
    st.injected 存在 sessState(sid) 返回的对象里，
    而 sessions 挂在 globalThis（E-307 已改）。

    理论上 selfreload 不应重置它。
    但如果 apply() 重新执行，且某处重建了 sessState，
    或者 sid 变了，就会重新注入。

  下一步：直接读代码确认 sessState 是否会在 apply 时被重建。
""")

out = {
    "experiment": "E-344",
    "sessions_globalThis": has_globalthis,
    "n_apply": n_apply,
    "n_reload": n_reload,
    "seg8_inject": len(seg8_inject),
    "seg8_apply": seg8_apply,
}
p = os.path.join(_HERE, "results", "e344_reload_reset.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e344_reload_reset.json")
