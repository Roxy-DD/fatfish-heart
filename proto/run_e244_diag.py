"""E-244 自我诊断：下一个该修的是什么

判断依据：只看数据，不猜。

检查项：
  ① sleep 的 merge 是否会改写原文（和 E-243 同样的问题）
  ② 各模块真实的"程序 vs 模型"职责划分是否合理
  ③ 有没有其他"只在测试里跑过、真实从未触发"的机制
  ④ 记忆库的当前健康度
"""
import sys
import os
import json
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
_MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, _MEM)

from store_v2 import MemoryStore
import audit as _audit
import consolidate as _con
import notebook as nb

DB = os.path.join(_MEM, "dsh_mem.db")
st = MemoryStore(DB)

print("=" * 76)
print("E-244 自我诊断：下一个该修什么")
print("=" * 76)

# ============ ① sleep 的 merge 是否改写原文 ============
print("\n① sleep merge 是否会改写原文（E-243 同类问题）")
cons_src = open(os.path.join(_MEM, "consolidate.py"), encoding="utf-8").read()

# 找 merged_text 的处理
for line in cons_src.split("\n"):
    if "merged_text" in line and "op ==" not in line and "a.get" in line:
        print(f"   代码: {line.strip()}")

has_revise = "st.revise(keep_id, new_text=merged_text)" in cons_src
print(f"\n   merge 时调用 revise 改写 keep 的文本: {'⚠ 是（会改写原文）' if has_revise else '✅ 否'}")

if has_revise:
    print("""
   → 问题：睡眠 merge 让模型生成 merged_text 并覆盖 keep 的原文。
   → 这与 E-243 修正的"模型不该重写内容"是同一类问题。
   → 原文被覆盖后，虽然 superseded_by 可溯源到 drop 那条，
     但 keep 那条的原始表述已经丢失（revision+1 保留了历史吗？需查）。
""")

# 查 revision 是否保留历史
print("   检查 revise 是否保留历史版本...")
v1_src = open(os.path.join(_MEM, "store_v1.py"), encoding="utf-8").read()
revise_block = v1_src[v1_src.find("def revise"):][:900]
keeps_history = "history" in revise_block.lower() or "old_text" in revise_block.lower()
print(f"   revise 保留历史: {'✅ 是' if keeps_history else '❌ 否（旧文本被直接覆盖）'}")

# ============ ② 真实触发过的机制 ============
print("\n② 机制真实触发情况")
astats = _audit.stats(st)
print(f"   审计日志总计: {astats['total']} 条")
for k in ["remember", "recall", "inject", "sleep", "conflict",
          "chain", "affect", "note", "todo"]:
    n = astats["by_kind"].get(k, 0)
    mark = "✅" if n > 0 else "❌"
    print(f"     {mark} {k:<10} {n}")

# ============ ③ 笔记/待办现状 ============
print("\n③ 知识库现状")
ns = nb.note_stats(st)
ts = nb.todo_stats(st)
print(f"   笔记: 总计 {ns['valid']} 条 "
      f"(随笔 {ns['scrap']} / 笔记 {ns['note']} / 手册 {ns['manual']})")
print(f"   待办: {ts['total']} 条 (待办 {ts['pending']} / 完成 {ts['done']})")

# ============ ④ 记忆健康度 ============
print("\n④ 记忆健康度")
s = st.stats()
print(f"   有效: {s['valid']} | 失效: {s['invalid']} | 向量: {s['vectors']}")
print(f"   高代价: {s['high_cost']} | 强情绪: {s['high_affect']}")

# channel 计数（E-242 修复后应开始累积）
ch_rows = st.con.execute(
    "SELECT COUNT(*) FROM meta WHERE key LIKE 'channel:%'").fetchone()[0]
print(f"   channel 计数记录: {ch_rows} 条")

# ============ ⑤ 结论 ============
print("\n" + "=" * 76)
print("诊断结论")
print("=" * 76)

issues = []
if has_revise:
    issues.append({
        "id": "sleep-merge-rewrite",
        "desc": "sleep merge 让模型重写 keep 原文，与 E-243 修正的原则冲突",
        "severity": "high",
        "fix": "改成：merge 只做'保留哪条 + 标注关联'或'程序拼接'，不让模型重写",
    })
if not keeps_history:
    issues.append({
        "id": "revise-no-history",
        "desc": "revise 直接覆盖旧文本，无历史版本（R4 可塑性受损）",
        "severity": "high",
        "fix": "增加 note/episode 历史表，或 revision 存快照",
    })
if ch_rows == 0:
    issues.append({
        "id": "channel-empty",
        "desc": "channel 计数仍为空（E-242 修复后未产生新数据）",
        "severity": "medium",
        "fix": "确认插件重启后是否已应用修复",
    })
if astats["by_kind"].get("sleep", 0) == 0:
    issues.append({
        "id": "sleep-never-run",
        "desc": "sleep 从未真实触发",
        "severity": "low",
        "fix": "需真实对话触发，或接受它低频",
    })

for i, iss in enumerate(issues, 1):
    print(f"\n{i}. [{iss['severity'].upper()}] {iss['desc']}")
    print(f"   修法: {iss['fix']}")

if not issues:
    print("\n没有发现高优先级问题。")

st.con.close()

out = {"experiment": "E-244", "issues": issues,
       "audit_stats": astats["by_kind"],
       "note_stats": ns, "todo_stats": ts,
       "memory_stats": s, "channel_records": ch_rows}
p = os.path.join(_HERE, "results", "e244_diag.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 results/e244_diag.json")
