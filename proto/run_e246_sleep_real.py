"""E-246 打通未触发机制：先真实跑一次 sleep

诊断发现 7 个机制「有代码、有单测，但真实链路从未调用」：
  sleep / conflict / chain / affect / note / todo / config

判断：这些是「未验证的假设」。我写过代码、跑过单测，
      但没在真实链路里跑过。这比继续加功能更有价值。

先跑 sleep：它是记忆系统的核心闭环（整理/遗忘/强化），
且不依赖外部条件（不需要矛盾、不需要链式触发）。

做法：
  · 用真实生产库（先备份）
  · 真实调用 cli_mcp.cmd_sleep_prepare → 看候选包
  · 用真实模型判断（通过插件层的逻辑模拟，或直接给固定判定）
  · 真实调用 cmd_sleep_apply → 看落库
  · 验证：效果是否合理，有无副作用
"""
import sys
import os
import json
import shutil
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, _MEM)

from store_v2 import MemoryStore
import cli_mcp
import consolidate as _con
import audit as _audit
import hook as _hook

DB = os.path.join(_MEM, "dsh_mem.db")
WORK = os.path.join(_HERE, "work")

print("=" * 76)
print("E-246 真实跑一次 sleep")
print("=" * 76)

# ---------- 备份 ----------
ts = time.strftime("%Y%m%d_%H%M%S")
backup = os.path.join(WORK, f"dsh_mem_before_sleep_{ts}.db")
shutil.copy2(DB, backup)
print(f"\n① 备份 → {os.path.basename(backup)}")

st = MemoryStore(DB)

before = st.stats()
print(f"\n② 睡眠前状态")
print(f"   有效: {before['valid']} | 失效: {before['invalid']}")

# ---------- 阶段1：prepare ----------
print(f"\n③ 调 cmd_sleep_prepare")
r1 = cli_mcp.cmd_sleep_prepare(st, {"limit": 20, "recent_n": 50})
print(f"   ok={r1.get('ok')}")
print(f"   window: {r1.get('window')}（fresh=增量 / recent=兜底）")
print(f"   cursor: {r1.get('cursor', 0):.0f}")
print(f"   候选对: {r1.get('n_pairs')}")
print(f"   L3 候选: {len(r1.get('l3_candidates', []))}")
print(f"   本次新增: {len(r1.get('fresh', []))}")

pairs = r1.get("pairs", [])
if pairs:
    print(f"\n   最高的几对：")
    for p in pairs[:5]:
        print(f"     cos={p['cos']:.3f}")
        print(f"       A: {p['a_text'][:55]}")
        print(f"       B: {p['b_text'][:55]}")

l3 = r1.get("l3_candidates", [])
if l3:
    print(f"\n   L3 候选前几条：")
    for c in l3[:5]:
        print(f"     {c['text'][:60]}")

# ---------- 阶段2：模拟模型判定 ----------
print(f"\n④ 生成判定（这里用保守策略：只合并高相似对，不轻易遗忘）")
actions = []

# 策略：cos >= 0.9 的才合并（保守）
HIGH_SIM = 0.90
for p in pairs:
    if p["cos"] >= HIGH_SIM:
        actions.append({
            "op": "merge",
            "keep_id": p["a_id"],
            "drop_id": p["b_id"],
        })
        print(f"   合并: cos={p['cos']:.3f} "
              f"{p['a_text'][:30]} ← {p['b_text'][:30]}")

# 不主动 invalidate L3（保守：宁可留着）
print(f"   L3 遗忘: 跳过（保守策略，不确定是否过时就不删）")

if not actions:
    print(f"   → 没有 cos >= {HIGH_SIM} 的对，本次不合并任何东西")

# ---------- 阶段3：apply ----------
print(f"\n⑤ 调 cmd_sleep_apply（{len(actions)} 个动作）")
r2 = cli_mcp.cmd_sleep_apply(st, {"actions": actions, "advance_cursor": True})
print(f"   ok={r2.get('ok')}")
print(f"   cursor 推进到: {r2.get('cursor')}")
res = r2.get("result", {})
print(f"   merged: {len(res.get('merged', []))}")
print(f"   invalidated: {len(res.get('invalidated', []))}")
print(f"   bumped: {len(res.get('bumped', []))}")
print(f"   skipped_hooks: {len(res.get('skipped_hooks', []))}")

# ---------- 验证 ----------
after = st.stats()
print(f"\n⑥ 睡眠后状态")
print(f"   有效: {after['valid']} | 失效: {after['invalid']}")
print(f"   变化: 有效 {after['valid'] - before['valid']:+d}, "
      f"失效 {after['invalid'] - before['invalid']:+d}")

# 验证合并结果
if res.get("merged"):
    print(f"\n   合并详情：")
    for m in res["merged"]:
        keep = st.get(m["keep"])
        drop = st.get(m["drop"])
        print(f"     keep {m['keep'][:8]}: {keep.raw_text[:60] if keep else '?'}")
        if keep and "｜合并:" in keep.raw_text:
            print(f"       ✅ 有合并标注")
        if drop:
            print(f"     drop {m['drop'][:8]}: invalid={drop.invalid_at is not None}, "
                  f"superseded_by={drop.superseded_by[:8] if drop.superseded_by else 'None'}")

# 验证审计
astats = _audit.stats(st)
print(f"\n⑦ 审计日志")
print(f"   sleep 记录: {astats['by_kind'].get('sleep', 0)} 条")

# 验证游标
cursor = _con.get_cursor(st)
print(f"   睡眠游标: {cursor:.0f}（{'已设置 ✅' if cursor > 0 else '未设置'}）")

# ---------- 再次 prepare 验证增量窗口 ----------
print(f"\n⑧ 再次 prepare（应走增量窗口）")
r3 = cli_mcp.cmd_sleep_prepare(st, {"limit": 20, "recent_n": 50})
print(f"   window: {r3.get('window')}（第一次是 recent，第二次应该 fresh 或 recent）")
print(f"   fresh 条数: {len(r3.get('fresh', []))}")
print(f"   候选对: {r3.get('n_pairs')}")

st.con.close()

# ---------- 结论 ----------
print("\n" + "=" * 76)
print("结论")
print("=" * 76)
ok = (r1.get("ok") and r2.get("ok"))
print(f"\n  sleep 完整链路: {'✅ 跑通' if ok else '❌ 有问题'}")
print(f"  睡眠游标机制: {'✅ 工作' if cursor > 0 else '❌ 未工作'}")
print(f"  合并结果: {len(res.get('merged', []))} 对")
print(f"  备份: {os.path.basename(backup)}")

out = {
    "experiment": "E-246",
    "before": {"valid": before["valid"], "invalid": before["invalid"]},
    "after": {"valid": after["valid"], "invalid": after["invalid"]},
    "n_pairs": r1.get("n_pairs"),
    "n_l3": len(l3),
    "merged": len(res.get("merged", [])),
    "window_first": r1.get("window"),
    "window_second": r3.get("window"),
    "cursor": cursor,
    "ok": ok,
}
p = os.path.join(_HERE, "results", "e246_sleep_real.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 results/e246_sleep_real.json")
