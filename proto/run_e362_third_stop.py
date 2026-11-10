"""E-362 第三次停止：为什么 E-356 的修复没拦住？

时间线：
  15:17:20  lifecycle.js 修改
  15:18:44  重载（指纹确认）
  15:19:36  lifecycle-steer {turn:272}   ← 修复后第一次，成功了
  15:23 左右 我发了总结，回合结束 —— **没有 steer**
  15:53:38  用户问「你怎么又停止了」

E-356 修复后应该：
  · 我没做工具调用的回合 → noProgressStreak+1，但 streak<3 → 仍然 steer
  · 我做了工具调用的回合 → hasProgress=true → 正常 steer

上一轮我做了大量工具调用（git commit 等），
按新逻辑 hasProgress=true，应该 steer 才对。

必须查：15:19:36 之后到底发生了什么。
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
print("E-362 第三次停止的根因")
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

# ---------- ② 15:19 之后的所有 lifecycle 记录 ----------
print("\n② 15:19 之后的所有 lifecycle 记录（本地时间）")

def to_local(t):
    try:
        return time.mktime(time.strptime(t[:19], "%Y-%m-%dT%H:%M:%S")) + 8*3600
    except Exception:
        return None

T0 = time.mktime(time.strptime("2026-11-10 15:19:00", "%Y-%m-%d %H:%M:%S"))

lc_after = []
for e in events:
    ts = to_local(str(e.get("t", "")))
    if ts and ts > T0 and "lifecycle" in str(e.get("kind", "")):
        lc_after.append((ts, e))

print(f"   条数: {len(lc_after)}")
for ts, e in lc_after:
    print(f"   {time.strftime('%H:%M:%S', time.localtime(ts))}  "
          f"{e.get('kind')}  "
          f"{json.dumps({k: v for k, v in e.items() if k not in ('t','kind')}, ensure_ascii=False)[:70]}")

# ---------- ③ 15:19 之后的 assemble 情况 ----------
print("\n③ 15:19 之后的 assemble / assemble-enter")

assemble_after = []
for e in events:
    ts = to_local(str(e.get("t", "")))
    if ts and ts > T0 and str(e.get("kind", "")).startswith("assemble"):
        assemble_after.append((ts, e))

print(f"   条数: {len(assemble_after)}")
for ts, e in assemble_after[-15:]:
    k = e.get("kind")
    print(f"   {time.strftime('%H:%M:%S', time.localtime(ts))}  {k}  "
          f"{json.dumps({kk: vv for kk, vv in e.items() if kk not in ('t','kind')}, ensure_ascii=False)[:70]}")

# ---------- ④ 有没有 attach 记录 ----------
print("\n④ 15:19 之后是否重新 attach 过")
attach_after = [x for x in lc_after if x[1].get("kind") == "lifecycle-attached"]
print(f"   lifecycle-attached: {len(attach_after)} 次")
for ts, e in attach_after:
    print(f"     {time.strftime('%H:%M:%S', time.localtime(ts))}  "
          f"{json.dumps({k: v for k, v in e.items() if k not in ('t','kind')}, ensure_ascii=False)}")

# ---------- ⑤ 全局：最后一次 lifecycle 事件 ----------
print("\n⑤ 全日志最后一次 lifecycle 事件")
lc_all = []
for e in events:
    ts = to_local(str(e.get("t", "")))
    if ts and "lifecycle" in str(e.get("kind", "")):
        lc_all.append((ts, e))
lc_all.sort(key=lambda x: x[0])
if lc_all:
    for ts, e in lc_all[-5:]:
        print(f"   {time.strftime('%m-%d %H:%M:%S', time.localtime(ts))}  "
              f"{e.get('kind')}")
    print(f"\n   最后一条 lifecycle 距今: "
          f"{(time.time() - lc_all[-1][0])/60:.1f} 分钟")

# ---------- ⑥ 结论 ----------
print("\n" + "=" * 78)
print("初步结论")
print("=" * 78)

if not lc_after:
    print("""
  ⚠ 15:19 之后**完全没有** lifecycle 记录

  这说明 turn-stopping 监听器要么：
    · 没有在这个 agent 上挂载
    · 或者挂载了但事件没触发
    · 或者触发了但抛出异常被吞掉

  注意：如果 steer 成功，一定会有 lifecycle-steer 日志；
        如果被拦，一定有 no-progress / max-reached 日志；
        如果禁用，有 lifecycle-disabled。
        **一条都没有 → 监听器根本没运行。**
""")
    verdict = "no_listener"
elif not any(x[1].get("kind") == "lifecycle-steer" for x in lc_after):
    print("""
  ⚠ 有 lifecycle 记录但没有 steer

  → 触发了但被某个条件拦住了
""")
    verdict = "blocked"
else:
    print("   有 steer 记录")
    verdict = "steered"

out = {
    "experiment": "E-362",
    "lc_after_1519": len(lc_after),
    "attach_after": len(attach_after),
    "n_assemble_after": len(assemble_after),
    "verdict": verdict,
}
p = os.path.join(_HERE, "results", "e362_third_stop.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 results/e362_third_stop.json")
