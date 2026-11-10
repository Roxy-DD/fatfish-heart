"""E-350 严重问题：protected_count 恒为 0，L0/L1 从未被注入

E-349 数据：
  28 次注入，protected_count 全部 = 0
  但库里实际有 16 条受保护记忆（L0=2 L1=9 L2=5），全文 4333 字

这意味着「常驻准则永远在场」这个核心设计**从未生效**。

可能原因：
  A. search 返回的结果里没有 flag=2（L0/L1 强制在场失效）
  B. 插件侧 protectedItems = allItems.filter(it => it.flag === 2) 拿不到
  C. scoped:true 参数导致 L0/L1 被排除

先直接测：调 search 看返回里有没有 flag=2。
"""
import sys
import os
import json
import warnings

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, MEM)

from store_v2 import MemoryStore
import hook as _hook

DB = os.path.join(MEM, "dsh_mem.db")
st = MemoryStore(DB)

print("=" * 76)
print("E-350 protected_count 为何恒为 0")
print("=" * 76)

# ---------- ① 库里到底有多少受保护记忆 ----------
print("\n① 受保护记忆")
prot = _hook.protected_of(st)
from collections import Counter
lv = Counter(p["level"] for p in prot)
print(f"   共 {len(prot)} 条: {dict(lv)}")

present = _hook.present_ids(st)
print(f"   present_ids（L0/L1）: {len(present)} 条")

# ---------- ② 直接调 search 看 flag ----------
print("\n② 直接调 search 看是否有 flag=2")

for q in ["记忆系统", "Docker", ""]:
    try:
        res = st.search(q, top=5)
        flags = [fl for ep, sc, fl in res]
        n2 = sum(1 for f in flags if f == 2)
        print(f"\n   查询 {q!r}:")
        print(f"     返回 {len(res)} 条, flag 分布: {Counter(flags)}")
        print(f"     flag=2 数量: {n2}")
        for ep, sc, fl in res[:3]:
            mark = "★" if fl == 2 else " "
            print(f"       {mark}flag={fl} cos={sc:.2f} {ep.raw_text[:40]}")
    except Exception as e:
        print(f"   查询 {q!r} 失败: {e}")

# ---------- ③ scoped 参数是否影响 ----------
print("\n③ scoped 参数的影响")
# 插件调用时传了 scoped: true，cmd_search 里会走不同分支
cli = open(os.path.join(MEM, "cli_mcp.py"), encoding="utf-8").read()
i = cli.find('if payload.get("scoped")')
if i > 0:
    print("   cmd_search 里的 scoped 分支:")
    print(cli[i:i+900])

st.con.close()

# ---------- 结论 ----------
print("\n" + "=" * 76)
print("结论")
print("=" * 76)
print("""
  如果上面 search 返回里 flag=2 数量为 0 →
    说明 store_v1.search 的强制注入失效
  如果 search 有 flag=2 但注入记录 protected_count=0 →
    说明插件侧过滤或计数有问题
""")
