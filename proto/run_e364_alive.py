"""E-364 关键验证：15:19 之后插件到底有没有重新 apply？

E-363 发现：
  15:19 之后 lifecycle 记录 0 条
  但 assemble 跑了 62 次

矛盾点：
  · 如果插件没重载 → 旧的 ctx.on 监听器应该还在 → turn-stopping 应该触发
  · 如果插件重载了 → 应该重新 attach → 应该有 lifecycle-attached 日志

两种情况都该有 lifecycle 日志，但实际一条都没有。

第三可能：**turn-stopping 事件本身没被触发**。

为什么可能不触发？回看 DSH 源码（E-290 已验证）：
    if (turnEnds && this.inbox.nextStep.length === 0) {
        await this.dispatch.serial("agent/turn-stopping", {...});
    }
    if (turnEnds && this.inbox.nextStep.length === 0) break;

  → 只有 turnEnds 为 true 且 inbox.nextStep 为空才 dispatch。

如果 **inbox.nextStep 不为空**，就不会 dispatch turn-stopping，
直接 target="next-step" 继续。那么：
  · 回合根本没"结束"
  · 但模型也没输出（因为它在等？）

另一种可能：**我的回合确实结束，但走的是别的路径**
（例如 abort、error、或 turnEnds=false 的自然结束）。

必须从 DSH 侧找证据。
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
print("E-364 15:19 之后插件状态审计")
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


T0 = time.mktime(time.strptime("2026-11-10 15:19:00", "%Y-%m-%d %H:%M:%S"))

# ---------- ① 15:19 之后的全部事件类型 ----------
print("\n① 15:19 之后的全部日志事件类型（不只 lifecycle）")
kinds = Counter()
for e in events:
    ts = to_local(str(e.get("t", "")))
    if ts and ts > T0:
        kinds[str(e.get("kind"))] += 1

print(f"   事件类型数: {len(kinds)}")
for k, v in kinds.most_common(25):
    mark = "  ← 关键" if ("lifecycle" in k or "turn" in k or "steer" in k) else ""
    print(f"   {k:<32} {v:>4}{mark}")

# ---------- ② 有没有任何 steer 迹象 ----------
print("\n② steer / injected 相关")
steer_events = [e for e in events
                if "steer" in str(e.get("kind", "")).lower()]
print(f"   steer 相关事件总计: {len(steer_events)}")
for e in steer_events[-5:]:
    ts = to_local(str(e.get("t", "")))
    print(f"     {time.strftime('%m-%d %H:%M', time.localtime(ts)) if ts else '?'}  "
          f"{e.get('kind')}")

# ---------- ③ 15:21~15:54 期间 assemble 的详细信息 ----------
print("\n③ 停止前后 assemble 的详情（15:20~15:25）")
T_A = time.mktime(time.strptime("2026-11-10 15:20:00", "%Y-%m-%d %H:%M:%S"))
T_B = time.mktime(time.strptime("2026-11-10 15:25:00", "%Y-%m-%d %H:%M:%S"))

win = []
for e in events:
    ts = to_local(str(e.get("t", "")))
    if ts and T_A <= ts <= T_B:
        win.append((ts, e))
win.sort(key=lambda x: x[0])

for ts, e in win:
    k = str(e.get("kind"))
    if k.startswith("assemble") or "lifecycle" in k:
        extra = json.dumps(
            {kk: vv for kk, vv in e.items() if kk not in ("t", "kind")},
            ensure_ascii=False)
        print(f"   {time.strftime('%H:%M:%S', time.localtime(ts))}  "
              f"{k:<18} {extra[:80]}")

# ---------- ④ 检查 assemble 是否有 assemble-error ----------
print("\n④ assemble-error / 异常")
errs = [e for e in events if "error" in str(e.get("kind", "")).lower()]
print(f"   总数: {len(errs)}")
for e in errs[-5:]:
    ts = to_local(str(e.get("t", "")))
    print(f"     {time.strftime('%m-%d %H:%M', time.localtime(ts)) if ts else '?'}  "
          f"{e.get('kind')}  {str(e.get('error'))[:60]}")

# ---------- ⑤ 结论 ----------
print("\n" + "=" * 78)
print("结论")
print("=" * 78)

print(f"""
  15:19 之后的事件类型: {len(kinds)} 种
  其中 lifecycle 相关: {sum(v for k, v in kinds.items() if 'lifecycle' in k)} 条
  assemble 相关:      {sum(v for k, v in kinds.items() if k.startswith('assemble'))} 条

  {"✅ 插件在正常工作（有大量 assemble），但 turn-stopping 从未触发" if sum(v for k, v in kinds.items() if k.startswith('assemble')) > 10 else ""}

  ★ 结论转向：
    插件没死，监听器可能也在，
    但 **DSH 侧根本没有 dispatch 过 turn-stopping 事件**。

  这意味着：我的回合不是"正常结束"，而是走了别的路径
  （可能是 abort、或 turnEnds=false 时模型停止输出）。
""")

out = {
    "experiment": "E-364",
    "kinds_after": dict(kinds),
    "n_lifecycle_after": sum(v for k, v in kinds.items() if "lifecycle" in k),
    "n_assemble_after": sum(v for k, v in kinds.items() if k.startswith("assemble")),
    "n_errors": len(errs),
}
p = os.path.join(_HERE, "results", "e364_plugin_alive.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e364_plugin_alive.json")
