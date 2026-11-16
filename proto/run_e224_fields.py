"""E-224 验证 cmd_list 返回补全的字段"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "proto", "cosmos_mem"))

from store_v2 import MemoryStore
import cli_mcp

# 用生产库
db = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "proto", "cosmos_mem", "dsh_mem.db")
st = MemoryStore(db)

r = cli_mcp.cmd_list(st, {"top": 3})
print("=" * 70)
print("E-224 cmd_list 字段补全验证（生产库）")
print("=" * 70)
print(f"ok={r.get('ok')}, items={len(r.get('items', []))}")
print(f"stats keys: {sorted(r.get('stats', {}).keys())}")

if r.get("items"):
    it = r["items"][0]
    print(f"\n第一条字段:")
    for k in ["ep_id", "raw_text", "cost", "affect", "valence",
              "atmosphere", "triggers", "person", "time", "location",
              "confidence", "revision", "superseded_by", "text_hash",
              "scope", "issues"]:
        v = it.get(k)
        if isinstance(v, list):
            v = v[:3]
        elif isinstance(v, str) and len(v) > 30:
            v = v[:30] + '...'
        print(f"  {k:<16}= {v}")

st.con.close()
print("\n→ 字段补全验证完成")
