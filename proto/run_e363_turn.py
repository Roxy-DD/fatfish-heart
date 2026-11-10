"""E-363 深查：turn-stopping 事件为什么没触发

E-362 关键证据：
  15:19:36  lifecycle-steer {turn:272}   ← 最后一次 lifecycle 记录
  15:21:38 ~ 15:54:01  assemble 62 次   ← 一直在跑
  但 15:19 之后 0 条 lifecycle 记录

可能原因：
  A. turn-stopping 事件没触发（DSH 内部条件不满足）
  B. 监听器被丢弃了（apply 重新执行但 ctx.on 没重挂）
  C. agent 对象换了（states WeakMap 按 agent 存，新 agent 无状态）
  D. 监听器抛异常被吞

关键区分：
  · 如果 turn-stopping 触发了但被拦 → 一定有 no-progress/max-reached 日志
  · 如果一条都没有 → **事件根本没到达监听器**

先看 DSH 源码里 turn-stopping 的触发条件（已在 E-290 验证过）。
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
IDX = os.path.join(os.path.dirname(_HERE), "dsh-plugin", "index.js")

print("=" * 78)
print("E-363 turn-stopping 为什么没触发")
print("=" * 78)

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


# ---------- ① 全部 lifecycle 事件时序 ----------
print("\n① 全部 lifecycle 事件（最后 10 条）")
lc = []
for e in events:
    ts = to_local(str(e.get("t", "")))
    if ts and "lifecycle" in str(e.get("kind", "")):
        lc.append((ts, e))
lc.sort(key=lambda x: x[0])
for ts, e in lc[-10:]:
    print(f"   {time.strftime('%m-%d %H:%M:%S', time.localtime(ts))}  "
          f"{e.get('kind')}")
print(f"\n   共 {len(lc)} 条，最后一条距今 "
      f"{(time.time() - lc[-1][0])/60:.0f} 分钟" if lc else "")

# ---------- ② attach 次数 vs apply 次数 ----------
print("\n② attach 与实际监听器挂载")
attachs = [x for x in lc if x[1].get("kind") == "lifecycle-attached"]
print(f"   lifecycle-attached: {len(attachs)} 次")
print("   最近 5 次:")
for ts, e in attachs[-5:]:
    print(f"     {time.strftime('%m-%d %H:%M:%S', time.localtime(ts))}")

# ---------- ③ 检查代码：attach 在哪调用 ----------
print("\n③ index.js 里 attach 的调用位置")
src = open(IDX, encoding="utf-8").read()
i = src.find("createLifecycleSupervisor")
if i > 0:
    seg = src[i:i + 1200]
    print("   " + seg[:1100].replace("\n", "\n   "))

# ---------- ④ 关键：是否有 agent/created 事件 ----------
print("\n④ agent/created 事件（监听器可能在这里挂载）")
agent_events = Counter()
for e in events:
    k = str(e.get("kind", ""))
    if k.startswith("agent"):
        agent_events[k] += 1
if agent_events:
    for k, v in agent_events.most_common():
        print(f"   {k:<30} {v}")
else:
    print("   ⚠ 日志里没有任何 agent* 事件")

# ---------- ⑤ 结论 ----------
print("\n" + "=" * 78)
print("结论")
print("=" * 78)

print(f"""
  lifecycle-attached 总数: {len(attachs)}
  最后一条 lifecycle: {time.strftime('%H:%M:%S', time.localtime(lc[-1][0])) if lc else '无'}

  ★ 核心矛盾：
    assemble 在 15:21~15:54 期间跑了 62 次（说明 agent 在工作），
    但 lifecycle 一条记录都没有。

    turn-stopping 是「回合即将结束时」触发的事件。
    如果我的回合确实结束了（用户说"你又停止了"），
    那么 turn-stopping 一定触发过。

    既然没日志 → 监听器不在。

  最可能的原因：
    apply() 重新执行时，createLifecycleSupervisor().attach(ctx) 没被重新调用，
    或者调用了但 ctx.on 注册的监听器随旧 ctx 一起被丢弃。
""")

out = {
    "experiment": "E-363",
    "n_lifecycle": len(lc),
    "n_attach": len(attachs),
    "last_lc_ago_min": round((time.time() - lc[-1][0]) / 60, 1) if lc else None,
    "agent_events": dict(agent_events),
}
p = os.path.join(_HERE, "results", "e363_turn_stopping.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e363_turn_stopping.json")
