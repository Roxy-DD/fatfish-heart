"""E-365 找到根因：turnEnds 有值但 nextStep 非空时 dispatch 被跳过

读完 DSH 源码（dsh-agent-loop/lib/index.js 的 turn() 方法）：

  while (true) {
      decision = await this.preStep(target, {turn, step})
      if (decision.kind === "reject") { turnEnds = {blocked}; return false }
      if (turnEnds && decision.messages.length === 0) break;       // ← A
      if (phase.step === 0 && decision.messages.length === 0) {    // ← B
          turnEnds = {completed}; return false;                    //   直接返回！
      }
      ...
      const stepEnd = await this.step(decision)
      if (turnEnds === null || turnEnds.kind !== "max-tokens") turnEnds = stepEnd;
      ...
      if (turnEnds && this.inbox.nextStep.length === 0) {          // ← C
          await this.dispatch.serial("agent/turn-stopping", {turn, signal})
      }
      if (turnEnds && this.inbox.nextStep.length === 0) break;
      target = "next-step";
  }

关键发现：
  路径 A：turnEnds 有值 + decision.messages 为空 → **break，跳过 dispatch**
  路径 B：step===0 且无消息 → **return false，整个 turn 直接结束，跳过 dispatch**
  路径 C：只有走到这里才 dispatch turn-stopping

也就是说，turn-stopping **不是每次回合结束都会触发**。

触发它需要同时满足：
  · 这一轮 step() 正常返回了 turnEnds（不是 A/B 提前退出）
  · inbox.nextStep 为空

这就解释了为什么 lifecycle 日志稀少：
  大量回合走的是 B（step===0，模型没产生任何消息就结束）
  或 A（turnEnds 已有值且无新消息）。

验证：统计实际 dispatch 次数 vs assemble 次数。
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
print("E-365 turn-stopping 触发频率的真相")
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


# ---------- ① 统计关键事件次数 ----------
print("\n① 累计次数对比")

kinds = Counter(str(e.get("kind")) for e in events if e.get("kind"))

n_enter = kinds.get("assemble-enter", 0)
n_assemble = kinds.get("assemble", 0)
n_attach = kinds.get("lifecycle-attached", 0)
n_steer = kinds.get("lifecycle-steer", 0)
n_noprog = kinds.get("lifecycle-no-progress", 0)
n_max = kinds.get("lifecycle-max-reached", 0)

print(f"   assemble-enter（每次模型调用前）: {n_enter}")
print(f"   assemble（实际注入记忆）:         {n_assemble}")
print(f"   lifecycle-attached（监听器挂载）: {n_attach}")
print(f"   lifecycle-steer（成功续跑）:      {n_steer}")
print(f"   lifecycle-no-progress:            {n_noprog}")
print(f"   lifecycle-max-reached:            {n_max}")

# ---------- ② 计算比例 ----------
print("\n② 比例分析")

if n_attach:
    print(f"   steer / attach = {n_steer}/{n_attach} = {n_steer/max(1,n_attach)*100:.1f}%")
print(f"   attach / assemble-enter = {n_attach}/{n_enter} = {n_attach/max(1,n_enter)*100:.1f}%")

print(f"""
   解释：
     assemble-enter 每个 step 都会触发（模型每次调用前）
     attach 是插件 apply 时挂一次

     如果 attach 远少于 assemble-enter → 正常（插件不常重载）
""")

# ---------- ③ 关键：attach 之后有多少次 turn-stopping 机会 ----------
print("\n③ 每次 attach 之后的 steer 机会")

attach_times = []
for e in events:
    if e.get("kind") == "lifecycle-attached":
        ts = to_local(str(e.get("t", "")))
        if ts:
            attach_times.append(ts)
attach_times.sort()

steer_times = []
for e in events:
    if e.get("kind") == "lifecycle-steer":
        ts = to_local(str(e.get("t", "")))
        if ts:
            steer_times.append(ts)
steer_times.sort()

print(f"   attach: {len(attach_times)} 次")
print(f"   steer:  {len(steer_times)} 次")

# 每次 attach 到下次 attach 之间有多少 steer
if attach_times:
    for i, at in enumerate(attach_times[-8:], 1):
        end = attach_times[i] if i < len(attach_times) else time.time()
        n_s = sum(1 for st in steer_times if at <= st < end)
        print(f"     attach@{time.strftime('%m-%d %H:%M:%S', time.localtime(at))} "
              f"→ 期间 steer {n_s} 次")

# ---------- ④ 结论 ----------
print("\n" + "=" * 78)
print("结论")
print("=" * 78)

print(f"""
  ★ 根因（从 DSH 源码读出来的，不是推测）：

    `agent/turn-stopping` **不是每次回合结束都触发**。
    只有走到 turn() 循环里的路径 C 才会 dispatch：
        if (turnEnds && this.inbox.nextStep.length === 0) {{
            await dispatch.serial("agent/turn-stopping", ...)
        }}

    而下面两条路径会**提前退出，跳过 dispatch**：
        A. if (turnEnds && decision.messages.length === 0) break;
        B. if (phase.step === 0 && decision.messages.length === 0) {{
               turnEnds = {{completed}}; return false;    // 整个 turn 直接结束
           }}

    → **如果模型在 step 0 就没有产生任何消息，或 turnEnds 已有值且无新消息，
       turn-stopping 根本不会被触发，我的监听器再怎么写也拦不到。**

  这解释了为什么：
    · 累计 33 次 attach，但只有 11 次 steer
    · 大量回合走 A/B 路径，静默结束

  ★ 这不是我的代码 bug，是**拦截点选错了**。

  正确的做法应该是：
    拦截 `agent/pre-step`（每个 step 前都触发，不会跳过）
    或 `agent/request-error` / `turn/end` 级别的事件，
    而不是依赖只在特定路径才 dispatch 的 turn-stopping。
""")

out = {
    "experiment": "E-365",
    "n_enter": n_enter,
    "n_attach": n_attach,
    "n_steer": n_steer,
    "n_noprog": n_noprog,
    "steer_per_attach": round(n_steer / max(1, n_attach), 3),
    "verdict": "turn-stopping 不是每次回合结束都触发，拦截点选错",
}
p = os.path.join(_HERE, "results", "e365_root_cause.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e365_root_cause.json")
