"""验证跨版本双向迁移能力

模拟用户描述的真实场景：
  E-233(当前) 出问题
    → 回退到 E-232（backward）
    → E-232 上能看到"E-233 坏了"
    → 修好后前进回 E-233（forward）
    → 若 E-232 也有问题，继续退回 E-231（backward_jump）
    → 再跳跃前进（forward_jump）

同时验证钩子：
  · 迁移自动种钩子
  · 钩子可删且不影响主体
"""
import sys
import os
import time

sys.path.insert(0, r"C:\Users\xuee\Desktop\肥鱼之心\proto")
import lifecycle as lc

results = []


def check(name, cond, detail=""):
    results.append({"name": name, "ok": bool(cond), "detail": detail})
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  {detail}" if detail else ""))


print("=" * 74)
print("跨版本双向迁移验证")
print("=" * 74)

# 记录初始状态
m0 = lc._load_manifest()
start = m0.get("current")
chain = lc.version_chain()
print(f"\n初始: current={start}, 共 {len(chain)} 个版本")
for v in chain:
    icon = {"ok": "✅", "broken": "❌"}.get(v["status"], "·")
    mark = "▶" if v["is_current"] else " "
    print(f"  {mark} {icon} {v['id']}")

# ---------- ① 方向判定 ----------
print("\n【1】方向判定（无跨度限制）")
check("E-233 → E-232 判为回退",
      lc._direction_of("E-233", "E-232") == "backward")
check("E-232 → E-233 判为前进",
      lc._direction_of("E-232", "E-233") == "forward")
check("E-233 → E-231 判为回退跳跃",
      lc._direction_of("E-233", "E-231") == "backward_jump")
check("E-231 → E-233 判为前进跳跃",
      lc._direction_of("E-231", "E-233") == "forward_jump")
check("E-233 → v0.1.0 判为回退跳跃（跨5个）",
      lc._direction_of("E-233", "v0.1.0") == "backward_jump")
check("v0.1.0 → E-233 判为前进跳跃（跨5个）",
      lc._direction_of("v0.1.0", "E-233") == "forward_jump")

# ---------- ② 回退：E-233 → E-232 ----------
print("\n【2】回退：E-233 → E-232（模拟 E-233 出问题）")
r1 = lc.migrate_to("E-232", reason="模拟：E-233 的注入分层导致回归失败")
check("迁移成功", r1["ok"], f"方向={r1['direction']}")
check("方向是 backward", r1["direction"] == "backward")
check("E-233 被标记 broken",
      any("标记 E-233" in s for s in r1["steps"]))

# 确认状态
m1 = lc._load_manifest()
check("current 变成 E-232", m1["current"] == "E-232")
v233 = lc._find(m1, "E-233")
check("E-233 状态=b roken", v233 and v233["status"] == "broken",
      f"{v233['status'] if v233 else 'N/A'}")

# ---------- ③ 故障传播：E-232 能知道 E-233 坏了 ----------
print("\n【3】故障传播：E-232 上能否知道 E-233 坏了")
fault_hooks = lc.list_hooks(vid="E-233", kind="fault")
check("E-233 有故障钩子", len(fault_hooks) > 0, f"{len(fault_hooks)} 个")

# 检查记忆库里的故障记录
import sys as _s
_s.path.insert(0, lc._MEM)
from store_v2 import MemoryStore
st = MemoryStore(lc._DB)
rows = st.con.execute(
    "SELECT raw_text FROM episodes WHERE invalid_at IS NULL "
    "AND raw_text LIKE '%版本故障记录%'").fetchall()
st.con.close()
fault_mem = [r[0] for r in rows]
check("记忆库有故障记录", len(fault_mem) > 0, f"{len(fault_mem)} 条")
if fault_mem:
    print(f"     内容: {fault_mem[-1][:70]}...")

# ---------- ④ 前进：E-232 → E-233（修好后） ----------
print("\n【4】前进：E-232 → E-233（模拟修好了）")
r2 = lc.migrate_to("E-233", reason="已修复注入分层问题")
check("迁移成功", r2["ok"])
check("方向是 forward", r2["direction"] == "forward")
check("不标记 E-232 为 broken（前进说明它是好的）",
      any("不标记 E-232" in s for s in r2["steps"]))

m2 = lc._load_manifest()
check("current 回到 E-233", m2["current"] == "E-233")
v232 = lc._find(m2, "E-232")
check("E-232 仍为 ok", v232 and v232["status"] == "ok",
      f"{v232['status'] if v232 else 'N/A'}")

# ---------- ⑤ 级联回退：E-233 → E-231（跨2个） ----------
print("\n【5】级联回退：E-233 → E-231（E-232 也坏了）")
r3 = lc.migrate_to("E-231", reason="模拟：E-232 也有问题")
check("迁移成功", r3["ok"])
check("方向是 backward_jump", r3["direction"] == "backward_jump",
      f"实际={r3['direction']}")
check("current 变成 E-231", lc._load_manifest()["current"] == "E-231")

# ---------- ⑥ 大幅跳跃：E-231 → v0.1.0（跨5个） ----------
print("\n【6】大幅跳跃：E-231 → v0.1.0（跨 5 个版本）")
r4 = lc.migrate_to("v0.1.0", reason="极端测试：退回最初基线")
check("迁移成功", r4["ok"])
check("方向是 backward_jump", r4["direction"] == "backward_jump")
check("current 变成 v0.1.0", lc._load_manifest()["current"] == "v0.1.0")

# ---------- ⑦ 从最老跳回最新（跨5个前进） ----------
print("\n【7】从最老跳回最新：v0.1.0 → E-233（跨 5 个前进）")
r5 = lc.migrate_to("E-233", reason="回到最新版本")
check("迁移成功", r5["ok"])
check("方向是 forward_jump", r5["direction"] == "forward_jump")
check("current 回到 E-233", lc._load_manifest()["current"] == "E-233")

# ---------- ⑧ 钩子可删 ----------
print("\n【8】钩子可删（删了不影响机制）")
before = lc.hooks_summary()["total"]
n = lc.clear_hooks(kind="anchor")
after = lc.hooks_summary()["total"]
check("能清空锚点钩子", n > 0, f"清空 {n} 个")
check("总数减少", after < before, f"{before} → {after}")
check("系统仍健康", lc.health_check()["healthy"])

# ---------- ⑨ 最终版本链 ----------
print("\n【9】最终版本链")
for v in lc.version_chain():
    icon = {"ok": "✅", "broken": "❌"}.get(v["status"], "·")
    mark = "▶" if v["is_current"] else " "
    print(f"  {mark} {icon} {v['id']:<10} {v['status']:<8} {v['reason'][:30]}")

print("\n" + "=" * 74)
n_ok = sum(1 for r in results if r["ok"])
print(f"结果：{n_ok}/{len(results)} 通过")
for r in results:
    if not r["ok"]:
        print(f"  ❌ {r['name']}  {r['detail']}")
print("=" * 74)
sys.exit(0 if n_ok == len(results) else 1)
