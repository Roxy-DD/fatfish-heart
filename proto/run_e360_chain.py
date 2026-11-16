import os
import sys
import warnings

warnings.filterwarnings("ignore")
MEM = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "proto", "cosmos_mem")
sys.path.insert(0, MEM)

import cli_mcp
from store_v2 import MemoryStore

st = MemoryStore(MEM + r"\dsh_mem.db")

print("=" * 70)
print("模拟插件实际调用：cmd_search with scoped=True")
print("=" * 70)

# 插件实际发的参数（index.js 里 autoInjectTopK ?? 5）
r = cli_mcp.cmd_search(st, {
    "query": "验证注入修复是否生效",
    "top": 5,
    "scoped": True,
})

items = r.get("results", [])
print("\n返回 %d 条" % len(items))

protected = [it for it in items if it.get("flag") == 2]
normal = [it for it in items if it.get("flag") != 2]

print("  protectedItems (flag==2): %d 条" % len(protected))
print("  items (flag!=2):          %d 条" % len(normal))

print("\n关键：如果 protectedItems 非空，说明插件侧 E-348/350 修复能拿到 L0/L1")
if protected:
    print("  ✅ protectedItems 非空 → 链路已通")
    for it in protected[:3]:
        print("     [%s] %s" % (it["ep_id"][:8], it["text"][:40]))
else:
    print("  ❌ protectedItems 仍为空")

# 模拟插件构造 inject_log 的参数
content_chars = sum(len(it["text"]) for it in items)
print("\n模拟 inject_log 参数：")
print("  protected_count = %d" % len(protected))
print("  ep_ids 数量     = %d" % len(items))

st.con.close()
