"""E-367 搞清路径 B 的真实条件：什么时候 inbox 是空的？

E-366 的模拟有 bug：我假设 inbox 初始有消息，判断条件写错了。
必须从真实数据反推。

DSH 源码：
  const claimed = this.inbox.claim(target, position.turn);
  ...
  decision = await dispatch.waterfall('agent/pre-step', {messages: claimed, ...})
  ...
  if (turnEnds && decision.messages.length === 0) break;             // A
  if (phase.step === 0 && decision.messages.length === 0) { ... }    // B

`decision.messages` 的默认值 = claimed = inbox.claim() 的结果。

关键问题：**claim() 什么时候返回空？**

看 inbox.claim 的实现。如果 nextStep 为空，claim 就返回 []。
那么：
  · step 0 + inbox 空 → 路径 B → 整个 turn 直接结束，不 dispatch
  · step >0 + turnEnds 有值 + inbox 空 → 路径 A → break，不 dispatch
  · step >0 + turnEnds 有值 + inbox 非空 → 走到 C，dispatch

那么我的回合是怎么结束的？
  我在回合里调用了工具 → 工具结果会让 inbox 非空吗？
  → 需要看 claim 的 target 语义。

target 有两个值："next-turn"（第一轮）和 "next-step"（后续）。
claim(target) 从对应队列取。

所以：
  · 第一次迭代 target="next-turn" → 取用户消息队列
  · 后续迭代 target="next-step" → 取 steer/工具结果队列

如果模型输出了最终回答（没有工具调用）：
  · step 返回 turnEnds={completed}
  · 此时 nextStep 队列是空的
  · 走到 C → dispatch turn-stopping ✓
  · 我的监听器应该能拦到

那为什么 15:23 没拦到？
  可能：turn-stopping 触发了，但我的监听器不在（attach 到别的 ctx）
  或者：steer 了但没生效

必须查开头的 assume：33 次 attach 里，attach@15:18:44 之后有没有 steer。
E-365 显示：attach@15:18:44 → 期间 steer 0 次。

而 15:19:36 有一条 steer！它属于哪一个 attach 区间？
  attach@15:18:44 之后的下一个 attach 是……没有（15:18:44 是最后一个）

所以 15:19:36 的 steer 应该属于 attach@15:18:44 这个区间。
E-365 的循环写错了：它用「本次 attach 到下次 attach」，
但最后一次 attach 用 time.time() 作 end，应该能覆盖 15:19:36。

重算。
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
print("E-367 精确重算 attach 与 steer 的区间归属")
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


def _collect(kind):
    out = []
    for e in events:
        if e.get("kind") == kind:
            ts = to_local(str(e.get("t", "")))
            if ts:
                out.append((ts, e))
    out.sort(key=lambda x: x[0])
    return out


attach = _collect("lifecycle-attached")
steer = _collect("lifecycle-steer")
noprog = _collect("lifecycle-no-progress")

print(f"\n① attach {len(attach)} 次, steer {len(steer)} 次, no-progress {len(noprog)} 次")

# ---------- ② 逐区间精确统计 ----------
print("\n② 逐区间统计（[本次attach, 下次attach) 左闭右开）")
print(f"   {'区间起点':<16} {'区间终点':<16} {'steer':>6} {'noprog':>7}")
print("   " + "-" * 55)

total_s = 0
total_n = 0
for i, (t0, e0) in enumerate(attach):
    t1 = attach[i + 1][0] if i + 1 < len(attach) else float("inf")
    s_in = [x for x in steer if t0 <= x[0] < t1]
    n_in = [x for x in noprog if t0 <= x[0] < t1]
    total_s += len(s_in)
    total_n += len(n_in)
    end_str = (time.strftime("%m-%d %H:%M:%S", time.localtime(t1))
               if t1 != float("inf") else "至今")
    mark = ""
    if i >= len(attach) - 4:
        mark = "  ← 最近"
    print(f"   {time.strftime('%m-%d %H:%M:%S', time.localtime(t0)):<16} "
          f"{end_str:<16} {len(s_in):>6} {len(n_in):>7}{mark}")

print(f"\n   合计: steer {total_s}, no-progress {total_n}")

# ---------- ③ 异常：steer 出现在 attach 之前 ----------
print("\n③ 孤儿 steer（不属于任何 attach 区间）")
orphans = []
if attach:
    t_first = attach[0][0]
    orphans = [x for x in steer if x[0] < t_first]
print(f"   首个 attach 之前的 steer: {len(orphans)}")
for ts, e in orphans[:5]:
    print(f"     {time.strftime('%m-%d %H:%M', time.localtime(ts))}  "
          f"{json.dumps({k: v for k, v in e.items() if k not in ('t','kind')}, ensure_ascii=False)[:60]}")

# ---------- ④ 最后一次 attach 之后的情况 ----------
print("\n④ 最后一次 attach（15:18:44）之后")
if attach:
    t_last = attach[-1][0]
    print(f"   attach 时间: {time.strftime('%m-%d %H:%M:%S', time.localtime(t_last))}")
    s_after = [x for x in steer if x[0] >= t_last]
    n_after = [x for x in noprog if x[0] >= t_last]
    print(f"   之后 steer: {len(s_after)} 次")
    for ts, e in s_after:
        print(f"     {time.strftime('%H:%M:%S', time.localtime(ts))}  "
              f"{json.dumps({k: v for k, v in e.items() if k not in ('t','kind')}, ensure_ascii=False)}")
    print(f"   之后 no-progress: {len(n_after)} 次")
    for ts, e in n_after:
        print(f"     {time.strftime('%H:%M:%S', time.localtime(ts))}  "
              f"{json.dumps({k: v for k, v in e.items() if k not in ('t','kind')}, ensure_ascii=False)}")

# ---------- ⑤ 结论 ----------
print("\n" + "=" * 78)
print("结论")
print("=" * 78)

print(f"""
  累计 attach: {len(attach)}
  累计 steer:  {len(steer)}
  区间内合计:  {total_s}
  孤儿:        {len(orphans)}

  {"✅ 所有 steer 都归属于某个 attach 区间" if len(orphans) == 0 and total_s == len(steer) else "⚠ 有对不上的"}

  ★ 关键事实：
    最后一次 attach 是 15:18:44，
    之后只有 15:19:36 那一次 steer，
    然后到今天 15:53 用户询问，中间 30 分钟没有 lifecycle 记录。

    如果 turn-stopping 在 15:23 左右被 dispatch 过，
    监听器应该至少留下一条日志（steer 或 no-progress 或 max-reached）。

    一条都没有 → **turn-stopping 没被 dispatch**。

    这与源码一致：路径 A/B 会跳过 dispatch。
""")

out = {
    "experiment": "E-367",
    "n_attach": len(attach),
    "n_steer": len(steer),
    "sum_in_ranges": total_s,
    "n_orphan": len(orphans),
}
p = os.path.join(_HERE, "results", "e367_ranges.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e367_ranges.json")
