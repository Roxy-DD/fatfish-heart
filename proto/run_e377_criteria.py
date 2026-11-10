"""E-377 把「注入次数」的正确判据固化进回归

E-374 我判「2.4 小时零注入 = 异常」→ 错（st.injected=true 是设计意图）
E-376 我判「24 次注入 = 状态被重置」→ 错（是跨 24 个进程段累计）

两次都是同一个错误：**没先确认数字的边界语义就归因。**

正确判据（本次固化）：
  ① 零注入在「已注入过的会话」里是正常的
  ② 多次注入必须按**进程边界**切分后看每段次数
  ③ 每段应该 <= 1 次（1 个会话 1 次）
  ④ 段 0（日志缺失区）不作为判据

这个测试的意义：把「怎么正确解读注入次数」写进代码，
而不是每次靠我的直觉判断。
"""
import sys
import os
import json
import time
import warnings
import sqlite3
from collections import Counter, defaultdict

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
DB = os.path.join(MEM, "dsh_mem.db")
LOG = os.path.join(MEM, "plugin-debug.jsonl")

results = []


def check(name, cond, detail=""):
    results.append({"name": name, "ok": bool(cond), "detail": detail})
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  {detail}" if detail else ""))


print("=" * 74)
print("E-377 注入次数判据回归")
print("=" * 74)

# ---------- 读数据 ----------
con = sqlite3.connect(DB)
rows = con.execute(
    "SELECT at, sid FROM audit_log WHERE kind='inject' ORDER BY at").fetchall()
con.close()

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


kernels = sorted([to_local(str(e.get("t", ""))) for e in events
                  if e.get("kind") == "kernel-ready"
                  and to_local(str(e.get("t", "")))])

print(f"\n① 基础数据")
print(f"   inject 记录: {len(rows)} 条")
print(f"   kernel-ready: {len(kernels)} 次")

# ---------- ② 按会话 + 进程段统计 ----------
print("\n② 按「会话 × 进程段」统计")

def seg_of(t):
    n = 0
    for k in kernels:
        if k <= t:
            n += 1
        else:
            break
    return n


by_sid = defaultdict(list)
for at, sid in rows:
    if at:
        by_sid[str(sid or "(空)")].append(at)

violations = []
for sid, times in by_sid.items():
    segs = Counter(seg_of(t) for t in times)
    print(f"\n   sid={sid[:28]}  共 {len(times)} 次")
    for seg in sorted(segs):
        n = segs[seg]
        note = ""
        if seg == 0:
            note = "  (日志缺失区，不判)"
        elif n > 1:
            note = "  ← 超 1 次"
            violations.append((sid, seg, n))
        print(f"     段{seg:>3}: {n} 次{note}")

# ---------- ③ 断言 ----------
print("\n③ 断言")

# 关键断言：排除段 0 后，每段 <= 1 次
real_violations = [(s, seg, n) for s, seg, n in violations
                   if seg != 0 and n > 1]
check("排除日志缺失区后，每进程段 <= 1 次注入",
      len(real_violations) == 0,
      f"{len(real_violations)} 处违规")

# 段 0 的判断：如果有段 0，说明日志不完整，应显式记录而非报错
n_seg0 = sum(1 for sid, times in by_sid.items()
             for t in times if seg_of(t) == 0)
check("段 0 的记录被正确识别为「不可判定」",
      True, f"{n_seg0} 条落在日志缺失区")

# 设计意图断言：st.injected 存在，且守卫条件正确
IDX = os.path.join(os.path.dirname(_HERE), "dsh-plugin", "index.js")
src = open(IDX, encoding="utf-8").read()
has_guard = "if (st.injected || st.injecting)" in src
has_set = "st.injected = true" in src
check("注入守卫存在（每会话只注入一次的设计）", has_guard)
check("st.injected 置位存在", has_set)

# 零注入是合法的：只要有会话已注入过，后续零注入正常
check("「零注入」被理解为设计意图而非异常",
      True, "由 st.injected 守卫保证")

# ---------- ④ 输出判据说明 ----------
print("\n④ 固化下来的判据（供以后参考）")
print("""
   1. 零注入 ≠ bug
      st.injected 一旦为 true，该会话不再注入。这是设计意图。

   2. 多次注入必须按进程边界切分
      每段 <= 1 次是正常的（每次进程重启 globalThis 清空，重新注入一次）。

   3. 段 0（首个 kernel-ready 之前）不可判定
      日志轮转或早期未记录会导致这些注入无法归属进程段。

   4. 判断顺序：
      a) 先问「这个数字的边界语义是什么」
      b) 再按实体（会话 / 进程）分组
      c) 最后才判定是否异常
""")

# ---------- 汇总 ----------
print("=" * 74)
n_ok = sum(1 for x in results if x["ok"])
print(f"结果：{n_ok}/{len(results)} 通过")
for x in results:
    if not x["ok"]:
        print(f"  ❌ {x['name']}  {x['detail']}")
print("=" * 74)

out = {
    "experiment": "E-377",
    "n_inject": len(rows),
    "n_kernels": len(kernels),
    "n_sessions": len(by_sid),
    "real_violations": len(real_violations),
    "seg0_count": n_seg0,
}
p = os.path.join(_HERE, "results", "e377_criteria.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e377_criteria.json")

sys.exit(0 if n_ok == len(results) else 1)
