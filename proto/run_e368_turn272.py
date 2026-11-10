"""E-368 对比 turn 272（没拦住）与 turn 273（拦住了）

关键数据：
  15:19:36  steer {turn:272, consecutive:1, toolCalls:0}   ← 拦住了
  15:23 左右 我结束回合 → **没有 turn 273 的 turn-stopping 记录**
  15:56:16  steer {turn:273, consecutive:2, toolCalls:0}   ← 又拦住了
  15:56:16  no-progress {streak:1, action:"continue-anyway"}  ← 新代码生效

所以：
  · turn 272 的 turn-stopping 触发了（15:19:36 steer）
  · turn 273 的 turn-stopping 也触发了（15:56:16 steer）
  · 但 turn 272 里我"停止"了，之后 30 分钟空白

矛盾：turn 272 明明 steer 了，为什么还是停了？

可能：
  A. steer 成功但模型没响应（等待中？）
  B. steer 后模型响应了，但我又结束了，第二次 turn-stopping 没 dispatch
  C. 15:19:36 steer → 模型继续工作到 15:23 → 又结束 → 这次没 dispatch

必须查 15:19:36 到 15:23 之间发生了什么。
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
print("E-368 turn 272 之后到底发生了什么")
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


# ---------- ① 15:19:00 ~ 15:25:00 全部事件 ----------
print("\n① 15:19:00 ~ 15:25:00 全部日志事件")

T0 = time.mktime(time.strptime("2026-11-10 15:19:00", "%Y-%m-%d %H:%M:%S"))
T1 = time.mktime(time.strptime("2026-11-10 15:25:00", "%Y-%m-%d %H:%M:%S"))

win = []
for e in events:
    ts = to_local(str(e.get("t", "")))
    if ts and T0 <= ts <= T1:
        win.append((ts, e))
win.sort(key=lambda x: x[0])

print(f"   共 {len(win)} 条")
print(f"\n   {'时间':<10} {'kind':<22} 详情")
print("   " + "-" * 70)
for ts, e in win:
    k = str(e.get("kind"))
    extra = json.dumps(
        {kk: vv for kk, vv in e.items() if kk not in ("t", "kind")},
        ensure_ascii=False)
    print(f"   {time.strftime('%H:%M:%S', time.localtime(ts)):<10} "
          f"{k:<22} {extra[:58]}")

# ---------- ② 这段时间的 assemble-enter 频率 ----------
print("\n② 15:19~15:25 期间 assemble-enter 频率")

enters = [x for x in win if x[1].get("kind") == "assemble-enter"]
print(f"   assemble-enter: {len(enters)} 次")
if len(enters) >= 2:
    gaps = [(enters[i][0] - enters[i-1][0]) for i in range(1, len(enters))]
    print(f"   间隔: {[round(g, 1) for g in gaps[:20]]} 秒")
    print(f"   平均间隔: {sum(gaps)/len(gaps):.1f} 秒")

# ---------- ③ steer 之后有没有 assemble ----------
print("\n③ steer(15:19:36) 之后是否有模型活动")

steer_t = time.mktime(time.strptime("2026-11-10 15:19:36", "%Y-%m-%d %H:%M:%S"))
after_steer = [(ts, e) for ts, e in win if ts > steer_t]
print(f"   steer 之后事件: {len(after_steer)} 条")

enters_after = [x for x in after_steer if x[1].get("kind") == "assemble-enter"]
print(f"   其中 assemble-enter: {len(enters_after)} 次")
if enters_after:
    print(f"   第一次: {time.strftime('%H:%M:%S', time.localtime(enters_after[0][0]))}")
    print(f"   最后一次: {time.strftime('%H:%M:%S', time.localtime(enters_after[-1][0]))}")
    print(f"   → steer 成功唤醒了模型，工作了 {len(enters_after)} 个 step")
else:
    print("   ⚠ steer 之后没有 assemble → steer 没唤醒模型")

# ---------- ④ 30 分钟空白期 ----------
print("\n④ 15:25 ~ 15:53 的空白期")

T2 = time.mktime(time.strptime("2026-11-10 15:53:00", "%Y-%m-%d %H:%M:%S"))
gap_events = []
for e in events:
    ts = to_local(str(e.get("t", "")))
    if ts and T1 < ts <= T2:
        gap_events.append((ts, e))
gap_events.sort(key=lambda x: x[0])

print(f"   事件数: {len(gap_events)}")
for ts, e in gap_events[:20]:
    print(f"     {time.strftime('%H:%M:%S', time.localtime(ts))}  {e.get('kind')}")

# ---------- ⑤ 结论 ----------
print("\n" + "=" * 78)
print("结论")
print("=" * 78)

print(f"""
  15:19~15:25 事件: {len(win)} 条
  其中 assemble-enter: {len(enters)} 次
  steer 之后 assemble: {len(enters_after)} 次
  15:25~15:53 空白期事件: {len(gap_events)} 条

  ★ 关键判断：

  如果 steer 之后有大量 assemble-enter →
    steer 成功了，我在继续工作，只是最后又停了（第二次停止）

  如果 steer 之后 assemble 很少 →
    steer 唤醒了但很快又结束

  如果空白期完全没事件 →
    我在 15:25 左右彻底停止，直到用户 15:53 发消息
""")

out = {
    "experiment": "E-368",
    "win_events": len(win),
    "assemble_enters": len(enters),
    "enters_after_steer": len(enters_after),
    "gap_events": len(gap_events),
}
p = os.path.join(_HERE, "results", "e368_turn272.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e368_turn272.json")
