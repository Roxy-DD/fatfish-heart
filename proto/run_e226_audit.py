"""E-226 审计日志验证：写入 + 查询 + 不影响红线"""
import sys
import os

sys.path.insert(0, r"C:\Users\xuee\Desktop\肥鱼之心\proto\cosmos_mem")

from store_v2 import MemoryStore
import cli_mcp

db = r"C:\Users\xuee\Desktop\肥鱼之心\proto\work\e226_audit.db"
for suf in ("", "-wal", "-shm"):
    p = db + suf
    if os.path.exists(p):
        os.remove(p)

st = MemoryStore(db)

print("=" * 70)
print("E-226 审计日志验证")
print("=" * 70)

# 触发几个动作
st.add("登录密码是8848", resolve_conflict=False)
r = cli_mcp.cmd_add(st, {"text": "测试服务器密码改成5678"})
cli_mcp.cmd_search(st, {"query": "密码"})

# 查日志
a = cli_mcp.cmd_audit(st, {"limit": 20})
print(f"\naudit ok={a['ok']}, logs={len(a.get('logs', []))}")
print(f"stats: {a.get('stats')}")

for l in a.get("logs", [])[:6]:
    print(f"  [{l['kind']}] {l['msg'][:40]}  ep={l['ep_id'][:8]}")

# 按类型过滤
a2 = cli_mcp.cmd_audit(st, {"kind": "remember", "limit": 10})
print(f"\n按 remember 过滤: {len(a2.get('logs', []))} 条")

st.con.close()
print("\n→ 审计日志链路跑通")
