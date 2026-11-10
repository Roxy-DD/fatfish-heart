"""E-372 验证 turn 号变化的两种模式

E-371 后数据：
  15:19:36  steer {turn:272, consec:1}
  15:56:16  steer {turn:273, consec:2}
  16:00:53  steer {turn:274, consec:3}

turn 273 和 274 都 steer 成功了 → 说明不同 turn 不受防重入影响。
但 turn 272 只 1 次 → 同一 turn 内第二次被锁死。

问题：什么情况下 steer 后会进新 turn，什么情况下留在同一 turn？

从源码看：
    const turn = phase.turn + 1;      ← 每次调用 turn() 才 +1
    while (true) {
        ...
        target = "next-step";         ← steer 后走这里，同一 turn 内继续
    }

如果 steer 后模型**输出了内容并再次结束**，还是同一 turn。
那什么时候进新 turn？

答案：**只有 driver 重新调用 turn() 才 +1**。
turn() 结束后，driver 看到 inbox 有 next-turn 消息才再开一个 turn。

关键区分：
  · steer 推到 "next-step" → 同一 turn 内继续
  · 用户发消息推到 "next-turn" → 新 turn

所以 turn 273→274 之间，用户一定发过消息（reset 了 consecutive 吗？）
但 consecutive 是 2→3 递增的，说明**没 reset**。

这矛盾。必须查清楚。

可能：
  · lifecycle-reset 只在 consecutive>0 时打日志，且 reset 了 consecutive=0
  · 但 273→274 时 consecutive 从 2 到 3，没归零
  · 所以中间没有用户消息

那 turn 号怎么从 273 变 274 的？

→ 说明 turn() 被调用了两次，但第一次的 steer 让 while 继续，
  最后 while 退出（break），driver 再开新 turn()。

这需要看 driver 的逻辑。
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
print("E-372 turn 号变化模式分析")
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


# ---------- ① 15:56 ~ 16:04 全部事件 ----------
print("\n① 15:56 ~ 16:04 全部事件（看 turn 273→274 之间发生了什么）")

T0 = time.mktime(time.strptime("2026-11-10 15:56:00", "%Y-%m-%d %H:%M:%S"))
T1 = time.mktime(time.strptime("2026-11-10 16:04:30", "%Y-%m-%d %H:%M:%S"))

win = []
for e in events:
    ts = to_local(str(e.get("t", "")))
    if ts and T0 <= ts <= T1:
        win.append((ts, e))
win.sort(key=lambda x: x[0])

print(f"   事件数: {len(win)}")
print(f"\n   {'时间':<10} {'kind':<24} 详情")
print("   " + "-" * 68)
for ts, e in win[:40]:
    k = str(e.get("kind"))
    extra = json.dumps(
        {kk: vv for kk, vv in e.items() if kk not in ("t", "kind")},
        ensure_ascii=False)
    print(f"   {time.strftime('%H:%M:%S', time.localtime(ts)):<10} "
          f"{k:<24} {extra[:52]}")

# ---------- ② assemble 注入记录 ----------
print("\n② 这段时间的 assemble（注入）记录")
asms = [x for x in win if x[1].get("kind") == "assemble"]
print(f"   共 {len(asms)} 次")
for ts, e in asms:
    print(f"   {time.strftime('%H:%M:%S', time.localtime(ts))}  "
          f"hits={e.get('hits')} query='{str(e.get('query',''))[:40]}'")

# ---------- ③ lifecycle-reset 记录 ----------
print("\n③ lifecycle-reset（用户发消息会清零）")
resets = [x for x in win if x[1].get("kind") == "lifecycle-reset"]
print(f"   共 {len(resets)} 次")
for ts, e in resets:
    print(f"   {time.strftime('%H:%M:%S', time.localtime(ts))}  {e}")

# ---------- ④ 结论 ----------
print("\n" + "=" * 78)
print("结论")
print("=" * 78)

turns = [e.get("turn") for ts, e in win
         if e.get("kind") in ("lifecycle-steer", "lifecycle-no-progress")
         and e.get("turn")]
print(f"""
  这段时间出现的 turn 号: {sorted(set(turns))}
  lifecycle-reset 次数: {len(resets)}
  assemble 注入次数: {len(asms)}

  ★ 判断要点：
    · 如果 273→274 之间有 reset 或 assemble →
      说明用户发了新消息，新 turn 是正常的
    · 如果都没有 → turn 号自己变了，说明 steer 后 while 退出、
      driver 又开了新 turn（每轮独立 turn）
""")

out = {
    "experiment": "E-372",
    "turns": sorted(set(turns)),
    "n_resets": len(resets),
    "n_assemble": len(asms),
    "n_events": len(win),
}
p = os.path.join(_HERE, "results", "e372_turn_pattern.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e372_turn_pattern.json")
