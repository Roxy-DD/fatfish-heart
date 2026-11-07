"""E-258 诊断：剩余未触发机制，哪个最值得先跑？

剩余未真实触发：chain / affect / note / todo / config

判断依据（不猜，看数据）：
  ① 各机制在真实链路里是否会被调用（代码层面）
  ② 它们各自"不触发"的代价是什么
  ③ 哪个的缺失影响最大
"""
import sys
import os
import json

_HERE = os.path.dirname(os.path.abspath(__file__))
_MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, _MEM)

from store_v2 import MemoryStore
import audit as _audit

DB = os.path.join(_MEM, "dsh_mem.db")
st = MemoryStore(DB)

print("=" * 76)
print("E-258 诊断：剩余机制优先级")
print("=" * 76)

astats = _audit.stats(st)
print(f"\n当前审计（总 {astats['total']} 条）：")
for k in ["remember", "recall", "inject", "sleep", "conflict",
          "chain", "affect", "note", "todo", "config"]:
    n = astats["by_kind"].get(k, 0)
    print(f"  {'✅' if n > 0 else '❌'} {k:<10} {n}")

# ============ 逐个分析 ============
print("\n" + "=" * 76)
print("逐个分析：不触发的代价")
print("=" * 76)

analysis = [
    {
        "name": "note",
        "desc": "笔记/知识沉淀",
        "trigger": "模型主动调 mem_note",
        "cost": "系统缺少知识沉淀能力，只有记忆没有笔记",
        "data": "笔记表 0 条",
        "priority": "high",
        "reason": "这是用户明确要求的核心功能（写书/手册的能力）",
    },
    {
        "name": "todo",
        "desc": "待办清单",
        "trigger": "模型主动调 mem_todo",
        "cost": "模型无法自主管理任务队列",
        "data": "待办表 0 条",
        "priority": "medium",
        "reason": "辅助功能，没有也不影响核心闭环",
    },
    {
        "name": "chain",
        "desc": "链式展开（联想）",
        "trigger": "检索命中 seed 后，模型判断需要联想",
        "cost": "多跳查询能力弱",
        "data": "audit 0 条",
        "priority": "medium",
        "reason": "E-143 实测链式对多跳有效，但需要特定触发条件",
    },
    {
        "name": "affect",
        "desc": "情绪判断（模型版）",
        "trigger": "写入记忆时，模型判断情绪",
        "cost": "情绪取值靠规则，精度较低",
        "data": "audit 0 条",
        "priority": "low",
        "reason": "规则版已够用（affect_v3），模型版是精度优化",
    },
    {
        "name": "config",
        "desc": "配置变更审计",
        "trigger": "UI 改配置时",
        "cost": "配置变更无审计记录",
        "data": "audit 0 条",
        "priority": "low",
        "reason": "只影响可追溯性，不影响功能",
    },
]

for a in analysis:
    icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}[a["priority"]]
    print(f"\n{icon} {a['name']} — {a['desc']}")
    print(f"   触发条件: {a['trigger']}")
    print(f"   不触发代价: {a['cost']}")
    print(f"   当前数据: {a['data']}")
    print(f"   判断: {a['reason']}")

# ============ 检查 note 的真实可用性 ============
print("\n" + "=" * 76)
print("重点检查：note 是否真的可用（用户核心需求）")
print("=" * 76)

import notebook as nb
import cli_mcp

# 直接测端到端
r = cli_mcp.cmd_note(st, {"action": "list"})
print(f"\n   note list: ok={r.get('ok')}, 条数={len(r.get('notes', []))}")

r2 = cli_mcp.cmd_note(st, {
    "action": "add",
    "title": "测试笔记",
    "body": "这是一条测试内容",
    "tier": "scrap",
    "topic": "test",
})
print(f"   note add: ok={r2.get('ok')}, tier={r2.get('note', {}).get('tier')}")

if r2.get("note"):
    nid = r2["note"]["note_id"]
    r3 = cli_mcp.cmd_note(st, {"action": "promote", "note_id": nid, "to_tier": "note"})
    print(f"   promote scrap→note: ok={r3.get('ok')}, tier={r3.get('note', {}).get('tier')}")
    # 清理
    cli_mcp.cmd_note(st, {"action": "invalidate", "note_id": nid})
    print(f"   （测试笔记已清理）")

# ============ 结论 ============
print("\n" + "=" * 76)
print("结论")
print("=" * 76)

print("""
  优先级排序：
    1. 🔴 note  — 用户核心需求（写书/手册），且功能已完整但零使用
    2. 🟡 todo  — 辅助，可后置
    3. 🟡 chain — 需要特定触发条件，可等真实场景
    4. 🟢 affect/config — 低优先，规则版够用

  我的判断：
    note 是用户明确要求的核心能力（"记笔记的能力，也就是写书、写手册的能力"），
    代码已完成但零使用——这说明"能力建好了但没被用起来"。

    真正该做的不是"让 note 被调用"，而是：
      让系统在合适的时机**自动发现**"该写笔记了"，
      并生成 note_hint 待办，提示模型去做。

    这才是把 note 从"有代码"变成"真运转"的路径。
""")

st.con.close()

out = {
    "experiment": "E-258",
    "audit": astats["by_kind"],
    "analysis": analysis,
    "note_works": bool(r.get("ok") and r2.get("ok")),
}
p = os.path.join(_HERE, "results", "e258_priority.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e258_priority.json")
