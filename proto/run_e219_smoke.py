"""E-219 内核 cmd_search rerank 冒烟测试"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "proto", "cosmos_mem"))

from store_v2 import MemoryStore
import cli_mcp

# 建一个临时库
db = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "proto", "work", "e219_smoke.db")
for suf in ("", "-wal", "-shm"):
    p = db + suf
    if os.path.exists(p):
        os.remove(p)

st = MemoryStore(db)
# 写几条记忆
st.add("我电脑的登录密码是8848", resolve_conflict=False)
st.add("爷爷去世了那阵子很痛苦", resolve_conflict=False)
st.add("Melanie painted a lake sunrise last year", resolve_conflict=False)

print("=" * 70)
print("E-219 cmd_search rerank 冒烟测试")
print("=" * 70)

# 不带 rerank
r1 = cli_mcp.cmd_search(st, {"query": "密码", "top": 3})
print(f"\n不带 rerank: ok={r1['ok']}, results={len(r1.get('results', []))}")

# 带 rerank（中文查询，cross-encoder 支持中英文）
r2 = cli_mcp.cmd_search(st, {"query": "密码", "top": 3, "rerank": True})
print(f"带 rerank:   ok={r2['ok']}, results={len(r2.get('results', []))}")
if r2.get("results"):
    print(f"  第一条: {r2['results'][0]['text'][:40]}")

st.con.close()
print("\n→ cmd_search rerank 参数穿透成功" if r2.get("ok") else "\n→ 失败!")
