"""E-356 查：生命周期拦截为什么没拦住「说了要继续却停止」

现象：
  上一轮我最后一句是「接下来继续迭代」，
  然后回合结束了 —— 没有真的继续。

  系统里有 lifecycle.js 拦截 turn-stopping，
  应该检测到「任务未完成」并 steer 续跑。

要查：
  ① turn-stopping 是否触发了
  ② lifecycle 是否判断了「需不需要续跑」
  ③ 如果判断为「不需要」，依据是什么
  ④ 是否有 maxConsecutive / requireProgress 挡住了
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

print("=" * 78)
print("E-356 生命周期拦截审计")
print("=" * 78)

events = []
if os.path.exists(LOG):
    with open(LOG, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                events.append(json.loads(line))
            except Exception:
                pass

print(f"\n① 日志事件: {len(events)}")

# ---------- ② lifecycle 相关事件 ----------
print("\n② lifecycle 相关事件类型分布")
kinds = Counter(e.get("kind") for e in events if e.get("kind"))
lc_kinds = {k: v for k, v in kinds.items()
            if k and ("lifecycle" in k or "steer" in k or "turn" in k)}
for k, v in sorted(lc_kinds.items(), key=lambda x: -x[1]):
    print(f"   {k:<30} {v}")

if not lc_kinds:
    print("   ⚠ 没有任何 lifecycle 相关日志")

# ---------- ③ 最近的 lifecycle 决策 ----------
print("\n③ 最近的 lifecycle 决策记录")
lc = [e for e in events if e.get("kind")
      and ("lifecycle" in e.get("kind") or "steer" in e.get("kind"))]
print(f"   总数: {len(lc)}")
for e in lc[-20:]:
    t = str(e.get("t", ""))[:19]
    k = e.get("kind")
    rest = {kk: vv for kk, vv in e.items()
            if kk not in ("t", "kind")}
    s = json.dumps(rest, ensure_ascii=False)[:110]
    print(f"   {t}  {k:<26} {s}")

# ---------- ④ 找最后一次 turn-stopping ----------
print("\n④ turn-stopping 与 steer 的配对")
stops = [e for e in events if e.get("kind") == "lifecycle-turn-stopping"]
steers = [e for e in events if e.get("kind") == "lifecycle-steer"]
skips = [e for e in events if e.get("kind") == "lifecycle-skip"]

print(f"   turn-stopping 触发: {len(stops)}")
print(f"   steer 续跑:         {len(steers)}")
print(f"   skip 跳过:          {len(skips)}")

if skips:
    print("\n   最近的 skip 原因:")
    reasons = Counter(str(e.get("reason") or e.get("why") or "?")
                      for e in skips)
    for r, n in reasons.most_common():
        print(f"     {n:>3} 次  {r}")

# ---------- ⑤ 从 agent 事件看回合结束方式 ----------
print("\n⑤ agent 事件")
agent_kinds = {k: v for k, v in kinds.items()
               if k and k.startswith("agent")}
for k, v in sorted(agent_kinds.items(), key=lambda x: -x[1]):
    print(f"   {k:<30} {v}")

# ---------- ⑥ 判定 ----------
print("\n" + "=" * 78)
print("初步判定")
print("=" * 78)

print(f"""
  turn-stopping 触发 {len(stops)} 次
  steer 续跑         {len(steers)} 次
  skip 跳过          {len(skips)} 次

  {"⚠ 完全没有 lifecycle 日志 → 可能是日志 kind 名不同，或监听器没挂上" if not lc else ""}
""")

out = {
    "experiment": "E-356",
    "n_events": len(events),
    "lc_kinds": lc_kinds,
    "stops": len(stops),
    "steers": len(steers),
    "skips": len(skips),
}
p = os.path.join(_HERE, "results", "e356_why_stopped.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e356_why_stopped.json")
