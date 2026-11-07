"""E-267 chain 链式展开：真实跑一次

未触发机制剩：chain / affect

chain = 链式联想：看到 A 想起 B 再想起 C。
触发条件：检索命中某条记忆后，模型判断"这条背后还有关联"。

先看代码链路是否完整，再用真实数据跑一次。
"""
import sys
import os
import json

_HERE = os.path.dirname(os.path.abspath(__file__))
_MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, _MEM)

from store_v2 import MemoryStore
import cli_mcp

DB = os.path.join(_MEM, "dsh_mem.db")
st = MemoryStore(DB)

print("=" * 76)
print("E-267 chain 链式展开")
print("=" * 76)

# ============ ① 检查代码链路 ============
print("\n① 代码链路")
import consolidate as _con

for fn in ["evolution_chain", "get_cursor", "set_cursor"]:
    print(f"   consolidate.{fn}: {'✅' if hasattr(_con, fn) else '❌'}")

# 检查 cli_mcp 里的 chain 命令
import inspect
cli_src = inspect.getsource(cli_mcp)
for cmd in ["cmd_chain_prepare", "cmd_chain_apply", "cmd_expand"]:
    has = f"def {cmd}" in cli_src
    print(f"   {cmd}: {'✅' if has else '❌'}")

# ============ ② 实测 evolution_chain ============
print("\n② 实测 evolution_chain（A→B→C 追溯）")

# 找一条有 superseded_by 链的记忆
rows = st.con.execute(
    "SELECT ep_id, raw_text, superseded_by FROM episodes "
    "WHERE superseded_by IS NOT NULL AND superseded_by != '' "
    "LIMIT 5").fetchall()
print(f"   有取代关系的记忆: {len(rows)} 条")
for eid, txt, sup in rows[:3]:
    print(f"     {eid[:8]} → {sup[:8]}: {txt[:45]}")

if rows:
    eid = rows[0][0]
    try:
        chain = _con.evolution_chain(st, eid)
        print(f"\n   追溯 {eid[:8]} 的演化链:")
        print(f"     长度: {chain.get('length')}")
        print(f"     root: {chain.get('root', '')[:8]}")
        print(f"     tip:  {chain.get('tip', '')[:8]}")
        for i, item in enumerate(chain.get("chain", []), 1):
            print(f"       {i}. {item['ep_id'][:8]}: {item['raw_text'][:50]}")
    except Exception as e:
        print(f"   ❌ evolution_chain 出错: {type(e).__name__}: {e}")
else:
    print("   ⚠ 没有取代关系的数据，无法测 chain")

# ============ ③ 实测 cmd_expand ============
print("\n③ 实测 cmd_expand（按链式展开相关记忆）")

# 找一条内容丰富的记忆作为起点
seed = st.con.execute(
    "SELECT ep_id, raw_text FROM episodes WHERE invalid_at IS NULL "
    "AND length(raw_text) > 100 LIMIT 1").fetchone()

if seed:
    sid, stxt = seed
    print(f"   起点: {sid[:8]} {stxt[:50]}...")
    try:
        r = cli_mcp.cmd_expand(st, {"ep_id": sid, "depth": 2, "limit": 5})
        print(f"   ok={r.get('ok')}")
        items = r.get("items") or r.get("expanded") or []
        print(f"   展开条数: {len(items)}")
        for it in items[:5]:
            txt = it.get("raw_text") or it.get("text") or ""
            print(f"     · {txt[:55]}")
    except Exception as e:
        print(f"   ❌ cmd_expand 出错: {type(e).__name__}: {e}")

# ============ ④ chain_prepare ============
print("\n④ 实测 cmd_chain_prepare")
try:
    r = cli_mcp.cmd_chain_prepare(st, {"seed_id": seed[0] if seed else "", "depth": 2})
    print(f"   ok={r.get('ok')}")
    print(f"   pack 长度: {len(str(r.get('pack', '')))}")
    if r.get("pack"):
        print(f"   预览: {str(r.get('pack'))[:200]}")
except Exception as e:
    print(f"   ❌ 出错: {type(e).__name__}: {e}")

st.con.close()

print("\n" + "=" * 76)
print("结论")
print("=" * 76)
print("""
  如果 evolution_chain 和 cmd_expand 都能正常返回，
  说明 chain 机制代码是好的，只是平时没触发。

  需要考虑：什么场景该触发 chain？
  · 当前是"模型主动调 mem_chain"才触发
  · 但模型不知道自己该调这个

  可能的方向：在检索结果里附带"这条有演化链"的提示。
""")
