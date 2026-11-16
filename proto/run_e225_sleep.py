"""E-225 睡眠机制内核级验证：prepare + apply + 红线"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "proto", "cosmos_mem"))

from store_v2 import MemoryStore
import cli_mcp

db = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "proto", "work", "e225_sleep.db")
for suf in ("", "-wal", "-shm"):
    p = db + suf
    if os.path.exists(p):
        os.remove(p)

st = MemoryStore(db)

# 造几条相关/碎片记忆，模拟"一个项目做完后的碎片"
st.add("登录密码是8848", resolve_conflict=False)
st.add("我电脑的登录密码是 8848，别忘", resolve_conflict=False)  # 重复碎片
st.add("测试服务器密码改成5678了", resolve_conflict=False)        # 新值，旧密码过时
st.add("项目A做完了，交付了", resolve_conflict=False)
st.add("今天天气不错", resolve_conflict=False)

print("=" * 72)
print("E-225 睡眠机制验证")
print("=" * 72)

# 第 1 步：prepare
r1 = cli_mcp.cmd_sleep_prepare(st, {"limit": 10})
print(f"\n① sleep_prepare: ok={r1['ok']}, recent={r1.get('total_recent')}, pairs={r1.get('n_pairs')}")
for p in r1.get("pairs", [])[:5]:
    print(f"    cos={p['cos']:.3f}  A[{p['a_id'][:8]}] {p['a_text'][:25]}")
    print(f"                      B[{p['b_id'][:8]}] {p['b_text'][:25]}")

# 第 2 步：模拟模型判定（手动构造 actions）
# 找到"密码 8848"的两条重复碎片
a_id = r1["recent"][0]["ep_id"] if r1.get("recent") else None
# 从 pairs 里找一对最相似的
if r1.get("pairs"):
    top = r1["pairs"][0]
    actions = [{
        "op": "merge",
        "keep_id": top["a_id"],
        "drop_id": top["b_id"],
        "merged_text": top["a_text"] + "；" + top["b_text"],
    }]
    r2 = cli_mcp.cmd_sleep_apply(st, {"actions": actions})
    print(f"\n② sleep_apply: ok={r2['ok']}")
    print(f"    merged: {r2.get('result', {}).get('merged')}")

    # 验证红线：drop 那条还在吗（应该 invalid 但存在）
    drop_id = top["b_id"]
    ep = st.get(drop_id)
    print(f"\n③ 红线验证：")
    print(f"    drop 记忆存在: {ep is not None}")
    print(f"    drop 已失效: {ep.invalid_at is not None if ep else False}")
    print(f"    drop superseded_by: {(ep.superseded_by or '')[:8] if ep else ''}")
    print(f"    → {'✅ 不物理删除，只打标记' if ep and ep.invalid_at else '❌ 被删了!'}")

st.con.close()
print("\n→ 睡眠机制内核链路跑通")
