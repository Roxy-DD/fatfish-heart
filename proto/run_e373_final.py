"""E-373 最终验证：修复后的防重入逻辑是否正确

E-371 的修复：把 turn 号防重入换成 steerInFlight。

E-372 又澄清了一个事实：
  · turn 号会自然递增（273→274），不是永远卡在同一 turn
  · 但 turn 272 只有 1 次记录，之后停了

所以问题不是「同一 turn 永久锁死」，
而是「**某次 steer 后，下一次 turn-stopping 恰好还在同一 turn 内**」。

无论如何，用 turn 号做防重入都是错的：
  · 它在 turn 号未变时会错误拦截合法续跑
  · 它给不了任何保护（真正的重入是同一次 dispatch 内的事件重放）

本轮验证修复是否真的解决了问题：
  ① 单元测试：同一 turn 多次触发 → 每次都 steer（已过 19/19）
  ② 检查运行时是否出现 reentry-skip（不该出现）
  ③ 检查修复后 steer 是否连续成功
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
LC = os.path.join(os.path.dirname(_HERE), "dsh-plugin", "lifecycle.js")

print("=" * 78)
print("E-373 防重入修复的最终验证")
print("=" * 78)

# ---------- ① 确认代码里旧逻辑已移除 ----------
print("\n① 代码检查：旧的 turn 号防重入是否已移除")

src = open(LC, encoding="utf-8").read()

# 只检查**实际代码行**，排除注释
def find_live_code(pattern):
    """找出匹配且不在注释里的行"""
    hits = []
    for i, line in enumerate(src.split("\n"), 1):
        if pattern not in line:
            continue
        s = line.strip()
        if s.startswith("//") or s.startswith("*") or s.startswith("/*"):
            continue
        hits.append((i, s))
    return hits

old_hits = find_live_code("lastSteerTurn === turn")
has_old = len(old_hits) > 0
has_new = len(find_live_code("if (s.steerInFlight)")) > 0
has_log = "lifecycle-reentry-skip" in src

print(f"   旧逻辑 (lastSteerTurn === turn) return: "
      f"{'❌ 仍存在！' if has_old else '✅ 已移除（仅注释中提及）'}")
if old_hits:
    for n, s in old_hits:
        print(f"      行 {n}: {s[:70]}")
print(f"   新逻辑 (steerInFlight 守卫): "
      f"{'✅ 存在' if has_new else '❌ 缺失'}")
print(f"   重入时打日志: {'✅ 有' if has_log else '❌ 无（还是静默）'}")

# ---------- ② 运行时检查 ----------
print("\n② 运行时：是否还有 reentry-skip")

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


FIX = time.mktime(time.strptime("2026-11-10 16:03:50", "%Y-%m-%d %H:%M:%S"))

reentry = [e for e in events if e.get("kind") == "lifecycle-reentry-skip"]
print(f"   全部 reentry-skip: {len(reentry)} 条")
for e in reentry[-3:]:
    ts = to_local(str(e.get("t", "")))
    print(f"     {time.strftime('%H:%M:%S', time.localtime(ts)) if ts else '?'}  {e}")

# ---------- ③ 修复后的 lifecycle 事件 ----------
print("\n③ 修复（16:03:50）之后的 lifecycle 事件")

after = []
for e in events:
    ts = to_local(str(e.get("t", "")))
    if ts and ts > FIX and "lifecycle" in str(e.get("kind", "")):
        after.append((ts, e))
after.sort(key=lambda x: x[0])

print(f"   条数: {len(after)}")
for ts, e in after:
    print(f"   {time.strftime('%H:%M:%S', time.localtime(ts))}  "
          f"{e.get('kind')}  "
          f"{json.dumps({k: v for k, v in e.items() if k not in ('t','kind')}, ensure_ascii=False)[:60]}")

# ---------- ④ steer 连续性 ----------
print("\n④ 历史 steer 的 turn 与 consecutive")

steers = []
for e in events:
    if e.get("kind") == "lifecycle-steer":
        ts = to_local(str(e.get("t", "")))
        if ts:
            steers.append((ts, e))
steers.sort(key=lambda x: x[0])

print(f"   steer 总数: {len(steers)}")
print(f"   {'时间':<20} {'turn':>5} {'consec':>7}")
print("   " + "-" * 40)
for ts, e in steers[-10:]:
    print(f"   {time.strftime('%m-%d %H:%M:%S', time.localtime(ts)):<20} "
          f"{str(e.get('turn')):>5} {str(e.get('consecutive')):>7}")

# ---------- 结论 ----------
print("\n" + "=" * 78)
print("结论")
print("=" * 78)

ok_code = (not has_old) and has_new and has_log
print(f"""
  代码检查: {'✅ 旧逻辑已移除，新逻辑就位' if ok_code else '❌ 代码未完全更新'}
  reentry-skip 记录: {len(reentry)} 条（正常应为 0，因为很少真正重入）
  修复后 lifecycle 事件: {len(after)} 条

  {'✅ 修复已就位并生效' if ok_code else ''}

  ★ 但要诚实说明：
    修复后的真实效果（能否长时间不停）还需要实际运行观察。
    单元测试 19/19 通过只证明逻辑正确，
    不证明在真实 DSH 环境里一定不会停。
""")

out = {
    "experiment": "E-373",
    "old_removed": not has_old,
    "new_present": has_new,
    "has_log": has_log,
    "n_reentry": len(reentry),
    "n_after_fix": len(after),
    "n_steers": len(steers),
}
p = os.path.join(_HERE, "results", "e373_final_verify.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e373_final_verify.json")
