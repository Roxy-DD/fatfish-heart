"""E-357 验证 lifecycle 修复的实际效果

E-356 修复已提交并重载（指纹 15:18:44 > 文件 mtime 15:17:20）。

验证要点：
  ① 新逻辑下，纯文本回合是否真的会被连续追 3 次
  ② no-progress 日志里是否出现 action='continue-anyway'
  ③ 旧行为是 action 无标记（直接 return），新行为应该带标记

如果能看到 continue-anyway，说明修复在真实环境生效。
"""
import sys
import os
import json
import time
import warnings
from collections import Counter

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
LOG = os.path.join(MEM, "plugin-debug.jsonl")

print("=" * 76)
print("E-357 验证 lifecycle 修复生效")
print("=" * 76)

events = []
if os.path.exists(LOG):
    with open(LOG, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                events.append(json.loads(line))
            except Exception:
                pass

print(f"\n① 日志事件: {len(events)}")

# ---------- ② lifecycle 事件时间线 ----------
lc = [e for e in events if e.get("kind")
      and "lifecycle" in str(e.get("kind"))]
print(f"\n② lifecycle 事件: {len(lc)} 条")
print(f"\n   {'时间':<20} {'kind':<26} 详情")
print("   " + "-" * 74)

for e in lc[-25:]:
    t = str(e.get("t", ""))[:19]
    k = str(e.get("kind"))
    rest = {kk: vv for kk, vv in e.items() if kk not in ("t", "kind")}
    s = json.dumps(rest, ensure_ascii=False)
    if len(s) > 55:
        s = s[:55] + "..."
    print(f"   {t:<20} {k:<26} {s}")

# ---------- ③ 关键：是否有 continue-anyway ----------
print("\n③ 关键验证：action 字段")

noprogs = [e for e in events if e.get("kind") == "lifecycle-no-progress"]
print(f"   no-progress 总数: {len(noprogs)}")

actions = Counter(str(e.get("action") or "(无标记-旧版)")
                  for e in noprogs)
print(f"   action 分布:")
for a, n in actions.most_common():
    print(f"     {n:>3}  {a}")

has_new = any(e.get("action") in ("continue-anyway", "give-up")
              for e in noprogs)
has_streak = any("streak" in e for e in noprogs)

print(f"\n   含新字段（action）: {'✅ 是' if has_new else '❌ 否'}")
print(f"   含 streak 字段:     {'✅ 是' if has_streak else '❌ 否'}")

# ---------- ④ 修复后（15:18:44 之后）的记录 ----------
print("\n④ 修复生效时间点之后的 lifecycle 记录")

def to_local(t):
    try:
        return time.mktime(time.strptime(t[:19], "%Y-%m-%dT%H:%M:%S")) + 8*3600
    except Exception:
        return None

FIX_TIME = time.mktime(time.strptime("2026-11-10 15:18:44",
                                     "%Y-%m-%d %H:%M:%S"))
after = []
for e in lc:
    ts = to_local(str(e.get("t", "")))
    if ts and ts > FIX_TIME:
        after.append((ts, e))

print(f"   修复后记录: {len(after)} 条")
for ts, e in after:
    print(f"     {time.strftime('%H:%M:%S', time.localtime(ts))}  "
          f"{e.get('kind')}  "
          f"{json.dumps({k: v for k, v in e.items() if k not in ('t','kind')}, ensure_ascii=False)[:60]}")

# ---------- ⑤ 判定 ----------
print("\n" + "=" * 76)
print("结论")
print("=" * 76)

if has_new:
    print("""
  ✅ 修复已生效

  no-progress 日志里出现了 action 字段，
  说明新代码（带 continue-anyway / give-up 分支）正在运行。
""")
    verdict = "active"
elif len(after) == 0:
    print("""
  🟡 还没触发新的 no-progress

  修复后还没有「无进展」的回合发生，
  所以看不到新日志。需要等下次纯文本回合才能验证。
""")
    verdict = "waiting"
else:
    print("""
  ⚠ 有修复后的记录但没有 action 字段

  可能是：新代码没生效，或日志格式没更新。
""")
    verdict = "not_active"

out = {
    "experiment": "E-357",
    "n_noprogs": len(noprogs),
    "actions": dict(actions),
    "has_action_field": has_new,
    "after_fix": len(after),
    "verdict": verdict,
}
p = os.path.join(_HERE, "results", "e357_lifecycle_verify.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e357_lifecycle_verify.json")
