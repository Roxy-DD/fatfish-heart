"""E-378 确证：同一 turn 内多次 steer 已在真实环境发生

E-371 修复前后对比的关键证据：

  修复前（turn 272）：
    15:19:36  steer {turn:272, consecutive:1}
    15:22:43  再次结束 → 静默 return（无日志）→ 永久停止

  修复后（turn 274）：
    16:06:53  steer {turn:274, consecutive:1}
    16:10:31  no-progress {turn:274, streak:1, action:'continue-anyway'}
    16:10:31  steer {turn:274, consecutive:2}      ← 同一 turn 第二次！

★ 这是修复生效的**直接证据**：
  同一 turn 内的第二次 turn-stopping 不再被静默吃掉。

本测试固化这个证据，并在未来验证：
  · 同一 turn 是否允许 >1 次 steer
  · 是否出现 reentry-skip（说明 steerInFlight 误挡）
"""
import sys
import os
import json
import time
import warnings
from collections import defaultdict

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
LOG = os.path.join(MEM, "plugin-debug.jsonl")

results = []


def check(name, cond, detail=""):
    results.append({"name": name, "ok": bool(cond), "detail": detail})
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  {detail}" if detail else ""))


print("=" * 74)
print("E-378 同一 turn 多次 steer 的真实证据")
print("=" * 74)

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


# ---------- ① 按 turn 汇总 steer 次数 ----------
print("\n① 每个 turn 的 steer 次数")

by_turn = defaultdict(list)
for e in events:
    if e.get("kind") == "lifecycle-steer":
        ts = to_local(str(e.get("t", "")))
        if ts:
            by_turn[e.get("turn")].append((ts, e.get("consecutive")))

print(f"   有 steer 的 turn: {len(by_turn)} 个")
print(f"\n   {'turn':>6} {'steer次数':>9}  时间")
print("   " + "-" * 50)
multi_turns = []
for turn in sorted(by_turn, key=lambda x: (x is None, x)):
    items = by_turn[turn]
    times = ", ".join(time.strftime("%m-%d %H:%M",
                                    time.localtime(t)) for t, _ in items)
    mark = "  ← 多次" if len(items) > 1 else ""
    print(f"   {str(turn):>6} {len(items):>9}  {times}{mark}")
    if len(items) > 1:
        multi_turns.append((turn, len(items)))

# ---------- ② 断言 ----------
print("\n② 断言")

check("存在「同一 turn 多次 steer」的证据",
      len(multi_turns) > 0,
      f"{len(multi_turns)} 个 turn")

# 具体验证 turn 274（修复后的那个）
t274 = by_turn.get(274, [])
# 修复后同一 turn 被 steer 了多次（数据会随运行增长，用 >= 2 而非固定值）
check("turn 274 被 steer 了多次（修复后）",
      len(t274) >= 2, f"实际 {len(t274)} 次")

if len(t274) >= 2:
    dt = (t274[-1][0] - t274[0][0]) / 60
    print(f"      首末间隔 {dt:.1f} 分钟")
    cons = [c for _, c in t274]
    print(f"      consecutive: {cons}")
    # consecutive 可能被 reset 归零（用户发消息 → 清零）。
    # 只要出现过「同一 turn 内多次 steer」且没有被 reentry 挡，就证明修复生效。
    # 这里断言：存在相邻两次 steer 中后一次 > 前一次（有递增即说明真的在执行）
    has_increase = any(
        cons[i] is not None and cons[i-1] is not None
        and cons[i] > cons[i-1]
        for i in range(1, len(cons)))
    check("consecutive 有递增（证明 steer 真的在执行）",
          has_increase, " → ".join(str(c) for c in cons))
    # 若出现下降，说明 reset 触发过，这是正常设计
    has_reset = any(
        cons[i] is not None and cons[i-1] is not None
        and cons[i] < cons[i-1]
        for i in range(1, len(cons)))
    if has_reset:
        print("      （出现下降 = 用户发消息触发 reset，属正常设计）")

# ---------- ③ 没有 reentry-skip 误挡 ----------
print("\n③ steerInFlight 是否误挡")

reentry = [e for e in events if e.get("kind") == "lifecycle-reentry-skip"]
check("无 reentry-skip 误挡", len(reentry) == 0, f"{len(reentry)} 条")

# ---------- ④ no-progress 的 action 字段 ----------
print("\n④ no-progress 的 action 标记")

noprogs = [e for e in events if e.get("kind") == "lifecycle-no-progress"]
with_action = [e for e in noprogs if e.get("action")]
print(f"   no-progress 总数: {len(noprogs)}")
print(f"   有 action 字段（新代码）: {len(with_action)}")
check("新代码已产出带 action 的日志",
      len(with_action) > 0, f"{len(with_action)} 条")

actions = defaultdict(int)
for e in with_action:
    actions[e.get("action")] += 1
for a, n in actions.items():
    print(f"     {a}: {n} 次")

# ---------- ⑤ 候选 ⑤：连续 steer ----------
print("\n⑤ 连续 steer 情况（同一会话内）")

steers = sorted([(to_local(str(e.get("t", ""))), e) for e in events
                 if e.get("kind") == "lifecycle-steer"
                 and to_local(str(e.get("t", "")))],
                key=lambda x: x[0])

if len(steers) >= 2:
    gaps = [(steers[i][0] - steers[i-1][0]) / 60
            for i in range(1, len(steers))]
    print(f"   steer 间隔（分钟）: "
          f"{[round(g, 1) for g in gaps[-8:]]}")

# ---------- 汇总 ----------
print("\n" + "=" * 74)
n_ok = sum(1 for x in results if x["ok"])
print(f"结果：{n_ok}/{len(results)} 通过")
for x in results:
    if not x["ok"]:
        print(f"  ❌ {x['name']}  {x['detail']}")
print("=" * 74)

out = {
    "experiment": "E-378",
    "n_turns_with_steer": len(by_turn),
    "multi_turns": [{"turn": t, "n": n} for t, n in multi_turns],
    "turn274_count": len(t274),
    "n_reentry": len(reentry),
    "n_action_logs": len(with_action),
}
p = os.path.join(_HERE, "results", "e378_same_turn.json")
if os.environ.get("FFH_NO_WRITE") == "1":
    print("（FFH_NO_WRITE=1，跳过写盘）")
else:
    with open(p, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e378_same_turn.json")

sys.exit(0 if n_ok == len(results) else 1)
