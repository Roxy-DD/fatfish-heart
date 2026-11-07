"""E-236 诊断：记忆系统的真实闭环在哪一步断了

自我审视：我不是要"加功能"，而是要找出当前系统最实质的缺失。

先看数据：
  ① 记忆存量与结构
  ② 注入历史（系统实际注入了什么）
  ③ 睡眠历史（有没有真实运行过）
  ④ 审计日志（真实动作分布）
  ⑤ 检索质量（离线指标 vs 真实使用）

判断依据：哪一环有数据、哪一环是空的。
空的环节才是真问题。
"""
import sys
import os
import json

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))

from store_v2 import MemoryStore
import hook as _hook
import seed as _seed
import audit as _audit
import consolidate as _con

DB = os.path.join(_HERE, "cosmos_mem", "dsh_mem.db")
st = MemoryStore(DB)

print("=" * 74)
print("E-236 自我诊断：记忆系统的闭环完整性")
print("=" * 74)

# ============ ① 记忆存量 ============
print("\n① 记忆存量")
s = st.stats()
sd = _seed.seed_stats(st)
hs = _hook.stats(st)
print(f"   有效: {s['valid']} 条（种子 {sd['valid']} + 用户 {s['valid']-sd['valid']}）")
print(f"   失效: {s['invalid']} 条")
print(f"   保护: L0={hs['by_level'].get('L0',0)} "
      f"L1={hs['by_level'].get('L1',0)} L2={hs['by_level'].get('L2',0)}")

# ============ ② 注入历史（有数据吗？）============
print("\n② 注入历史")
# 注入历史存在插件内存里，不在 DB。这里查 DB 里有没有相关记录
inject_rows = st.con.execute(
    "SELECT COUNT(*) FROM audit_log WHERE kind='inject'").fetchone()[0]
print(f"   audit_log 中 inject 记录: {inject_rows} 条")
if inject_rows == 0:
    print("   ⚠ 无注入审计记录 —— 注入历史只在插件内存，重启即丢")

# ============ ③ 睡眠历史（真实运行过吗？）============
print("\n③ 睡眠历史")
sleep_log_row = st.con.execute(
    "SELECT val FROM meta WHERE key='sleep:log'").fetchone()
if sleep_log_row:
    logs = json.loads(sleep_log_row[0])
    print(f"   睡眠记录: {len(logs)} 次")
else:
    print("   ⚠ 从未真实睡眠过（只有测试脚本调用过）")

cursor = _con.get_cursor(st)
print(f"   睡眠游标: {cursor:.0f} ({'已设置' if cursor > 0 else '未设置=首次'})")

# ============ ④ 审计日志动作分布 ============
print("\n④ 审计日志：真实动作分布")
astats = _audit.stats(st)
print(f"   总计: {astats['total']} 条")
for k, v in sorted(astats.get("by_kind", {}).items(), key=lambda x: -x[1]):
    print(f"     {k:<10} {v}")

# ============ ⑤ 关键判断：哪些环节是空的 ============
print("\n⑤ 闭环完整性判断")
chains = {
    "写入 (remember)": astats["by_kind"].get("remember", 0) > 0,
    "检索 (recall)": astats["by_kind"].get("recall", 0) > 0,
    "注入 (inject)": inject_rows > 0,
    "睡眠 (sleep)": astats["by_kind"].get("sleep", 0) > 0,
    "矛盾 (conflict)": astats["by_kind"].get("conflict", 0) > 0,
    "链式 (chain)": astats["by_kind"].get("chain", 0) > 0,
    "情绪 (affect)": astats["by_kind"].get("affect", 0) > 0,
}
for name, ok in chains.items():
    print(f"   {'✅' if ok else '❌'} {name}")

missing = [k for k, v in chains.items() if not v]
print(f"\n   缺失环节: {missing if missing else '无'}")

# ============ ⑥ 最核心的缺失：效果验证 ============
print("\n⑥ 最核心的问题")
print("   系统能【存】、能【取】、能【整理】，但缺少【效果验证】：")
print("   · 注入了记忆，但不知道模型有没有真的用上")
print("   · 用了记忆，但不知道答案质量有没有提升")
print("   · 睡眠整理了，但不知道整理后是变好还是变差")
print("\n   → 这就是『以实验数据为准』在真实使用场景下的空白。")
print("   → 离线指标（LoCoMo 召回 0.5802）测的是检索，不是记忆的价值。")

st.con.close()

print("\n" + "=" * 74)
print("结论：下一步该做什么")
print("=" * 74)
print("""
  不是加功能，而是建立【记忆效果的自验证闭环】：

  当前：记忆 → 注入 → (黑箱) → 回答
  应该有：记忆 → 注入 → 回答 → 反馈 → 回到记忆

  具体：记录每次注入后模型的表现，
       用真实数据判断"这条记忆有帮助/有害/无用"
""")
