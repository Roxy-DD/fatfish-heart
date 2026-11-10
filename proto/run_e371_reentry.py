"""E-371 验证：防重入（lastSteerTurn）是否是沉默不了的原因

E-368 数据：
  15:19:36  steer {turn:272, consecutive:1}   ← steer 了一次
  15:19:47 ~ 15:22:43  23 次 assemble-enter   ← 我工作了 3 分钟
  15:22:43 之后到 15:53  零事件                ← 停了，且无任何 lifecycle 日志

关键：为什么 15:22:43 结束时没有 lifecycle 日志？

看 lifecycle.js 的防重入：
    // 防重入：同一 turn 只 steer 一次
    if (s.lastSteerTurn === turn) return        // ← 静默 return，不打日志！

如果 15:22:43 结束时**仍然是 turn 272**，
那么 `lastSteerTurn === 272` → 直接 return，**一条日志都不会有**。

这完全解释了「零日志」现象。

验证：
  ① 从 DSH 源码确认 steer 后是否还在同一 turn
  ② 检查 15:56:16 的 turn 273 是怎么来的
  ③ 统计 turn 号序列
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
print("E-371 防重入假设验证")
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


# ---------- ① 所有 steer / no-progress 的 turn 序列 ----------
print("\n① lifecycle 事件的 turn 序列")

lc = []
for e in events:
    k = str(e.get("kind", ""))
    if k in ("lifecycle-steer", "lifecycle-no-progress",
             "lifecycle-max-reached"):
        ts = to_local(str(e.get("t", "")))
        if ts:
            lc.append((ts, k, e))
lc.sort(key=lambda x: x[0])

print(f"   共 {len(lc)} 条")
print(f"\n   {'时间':<20} {'kind':<24} {'turn':>6} {'consec':>7}")
print("   " + "-" * 62)
prev_turn = None
for ts, k, e in lc:
    turn = e.get("turn")
    consec = e.get("consecutive") or e.get("streak") or "-"
    mark = ""
    if prev_turn is not None and turn == prev_turn:
        mark = "  ← 同一 turn"
    print(f"   {time.strftime('%m-%d %H:%M:%S', time.localtime(ts)):<20} "
          f"{k:<24} {str(turn):>6} {str(consec):>7}{mark}")
    prev_turn = turn

# ---------- ② 同一 turn 出现多次的情况 ----------
print("\n② 同一 turn 的多次记录")
from collections import Counter, defaultdict
by_turn = defaultdict(list)
for ts, k, e in lc:
    by_turn[e.get("turn")].append((ts, k))

multi = {t: v for t, v in by_turn.items() if len(v) > 1}
print(f"   有多次记录的 turn: {len(multi)}")
for t, v in sorted(multi.items(), key=lambda x: -len(x[1]))[:5]:
    print(f"\n   turn {t}: {len(v)} 次")
    for ts, k in v:
        print(f"     {time.strftime('%m-%d %H:%M:%S', time.localtime(ts))}  {k}")

# ---------- ③ 关键：15:19~15:23 的 turn 号 ----------
print("\n③ 15:19~15:23 期间的 turn-272 相关记录")

T0 = time.mktime(time.strptime("2026-11-10 15:19:00", "%Y-%m-%d %H:%M:%S"))
T1 = time.mktime(time.strptime("2026-11-10 15:24:00", "%Y-%m-%d %H:%M:%S"))

for ts, k, e in lc:
    if T0 <= ts <= T1:
        print(f"   {time.strftime('%H:%M:%S', time.localtime(ts))}  {k}  "
              f"turn={e.get('turn')}  consec={e.get('consecutive')}")

# ---------- ④ 从 DSH 源码确认 steer 后的 turn 归属 ----------
print("\n④ DSH 源码：steer 后是否还在同一 turn")

ext = os.environ.get("TEMP", "") + r"\dsh_asar_check"
loop = os.path.join(ext, "dsh", "node_modules",
                    "@deepseek-ai", "dsh-agent-loop", "lib", "index.js")
if os.path.exists(loop):
    src = open(loop, encoding="utf-8", errors="replace").read()
    i = src.find("async turn()")
    seg = src[i:i + 2600]
    # 提取 while 循环结构
    print("   turn() 的 while 循环关键行：")
    for line in seg.split("\n"):
        s = line.strip()
        if any(k in s for k in ["while (true)", "preStep(", "turnEnds &&",
                                "turn-stopping", "target =", "break;",
                                "phase.turn = "]):
            print(f"     {s[:88]}")
else:
    print("   源码不可用")

# ---------- ⑤ 结论 ----------
print("\n" + "=" * 78)
print("结论")
print("=" * 78)

print(f"""
  同一 turn 出现多次 steer 的情况: {len(multi)} 个 turn

  ★ 关键机制（源码）：
    turn() 是一个 while(true) 循环，turn 号在**进入循环前**就定好了：

        const turn = phase.turn + 1;
        phase.turn = turn;              ← 只在这里 +1
        while (true) {{
            ...
            if (turnEnds && inbox.nextStep.length === 0) {{
                dispatch.serial("agent/turn-stopping", {{turn, ...}})
            }}
            if (turnEnds && inbox.nextStep.length === 0) break;
            target = "next-step";       ← steer 后走这里，**turn 不变**
        }}

    → **一次 steer 后继续工作，仍然在同一个 turn 里。**
    → 工作完再次结束时，dispatch 的 turn 号还是同一个。

    而我的防重入：
        if (s.lastSteerTurn === turn) return      ← 静默 return，无日志

    所以：
      15:19:36  steer(turn=272)，lastSteerTurn=272
      15:19:47~15:22:43  工作 23 步（turn 仍=272）
      15:22:43  再次 dispatch turn-stopping(turn=272)
      → lastSteerTurn === 272 → **静默 return**
      → 零日志，我就这样停了

  这不是 turn-stopping 没触发，而是**触发了但被防重入静默挡掉**。
""")

out = {
    "experiment": "E-371",
    "n_lc": len(lc),
    "n_multi_turn": len(multi),
    "multi_turns": {str(t): len(v) for t, v in multi.items()},
}
p = os.path.join(_HERE, "results", "e371_reentry.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e371_reentry.json")
