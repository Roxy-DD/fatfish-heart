"""E-220 端到端验证：rerank 参数链路（模拟插件→内核→store 全链路）"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "proto", "cosmos_mem"))

from store_v2 import MemoryStore
import cli_mcp

db = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "proto", "work", "e220_smoke.db")
for suf in ("", "-wal", "-shm"):
    p = db + suf
    if os.path.exists(p):
        os.remove(p)

st = MemoryStore(db)
st.add("我电脑的登录密码是8848，千万别忘了", resolve_conflict=False)
st.add("测试服务器的密码是5678", resolve_conflict=False)
st.add("我每天早上七点起床", resolve_conflict=False)
st.add("爷爷去世了那阵子很痛苦", resolve_conflict=False)

print("=" * 70)
print("E-220 rerank 参数端到端链路验证")
print("=" * 70)

# 模拟插件调内核：cmd_search 带 rerank
for query, kw in [("我的密码是多少", {}),
                  ("我的密码是多少", {"rerank": True}),
                  ("登录密码", {"rerank": True, "rerank_pool": 3})]:
    r = cli_mcp.cmd_search(st, {"query": query, "top": 3, **kw})
    print(f"\nquery='{query}' kw={kw}")
    print(f"  ok={r['ok']}, results={len(r.get('results', []))}")
    for it in r.get("results", [])[:3]:
        print(f"    [{it['ep_id'][:8]}] {it['text'][:30]} score={it.get('score')}")

st.con.close()
print("\n→ 全链路跑通")
