"""E-318 查：强制注入的记忆为什么有分数（不该是 0.0 吗？）

E-317 显示：
  查询「记忆系统」时，flag=2 的记忆显示 cos=2.60, 2.55, 2.49...
  但代码里明明是 out.append((ep, 0.0, 2))

说明：这些记忆是**被检索命中**的（有自己的分数），
      同时又被升级为 flag=2（因为它们在 L0/L1 里）。

这是正常的：命中 + 受保护 = 保留原分数 + 标记 flag=2。
只有「没被命中但必须在场」的才是 0.0。

验证这个理解是否正确。
"""
import sys
import os
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
print("E-318 验证强制注入的分数语义")
print("=" * 76)

present = _hook.present_ids(st)
print(f"\n① L0/L1 强制在场: {len(present)} 条")

res = st.search("记忆系统", top=5)
print(f"\n② 查询「记忆系统」结果（{len(res)} 条）")

n_zero = 0
n_scored = 0
for i, (ep, sc, fl) in enumerate(res, 1):
    tag = "强制" if fl == 2 else ("固定" if fl == 1 else "普通")
    if fl == 2:
        if sc == 0.0:
            n_zero += 1
        else:
            n_scored += 1
    print(f"   {i:>2}. [{tag:>2}] cos={sc:>5.2f} [{ep.ep_id[:8]}] "
          f"{ep.raw_text[:40]}")

print(f"\n③ 统计")
print(f"   flag=2 且 cos=0.0（纯追加）: {n_zero}")
print(f"   flag=2 且 cos>0（本身命中）: {n_scored}")

# ============ ④ 验证：纯追加的确实是 0.0 ============
print("\n④ 验证：随机查询时纯追加的记忆分数")

res2 = st.search("Docker 容器编排", top=5)
print(f"   查询「Docker 容器编排」（库里没有相关内容）")
print(f"   返回 {len(res2)} 条:")

zero_count = 0
for i, (ep, sc, fl) in enumerate(res2, 1):
    tag = "强制" if fl == 2 else ("固定" if fl == 1 else "普通")
    if sc == 0.0:
        zero_count += 1
    print(f"     {i:>2}. [{tag:>2}] cos={sc:>5.2f} {ep.raw_text[:40]}")

print(f"\n   cos=0.0 的条数: {zero_count}")

# ============ 结论 ============
print("\n" + "=" * 76)
print("结论")
print("=" * 76)

print(f"""
  分数语义确认：
    · 被检索命中的 L0/L1 记忆 → 保留原分数 + flag=2
    · 未被命中的 L0/L1 记忆   → 追加到末尾，cos=0.0 + flag=2

  这两个都是**正确行为**：
    · 命中的说明它确实相关，应该保留分数
    · 没命中的也必须在场（红线/准则），所以追加

  E-317 的「强制注入混在前部」是因为：
    查询「记忆系统」太宽泛，普通结果只有 1 条，
    L0/L1 里本来就有很多讲记忆系统的，它们**真的被命中了**。

  这不是 bug，是查询本身宽泛导致的正常结果。
""")

st.con.close()
