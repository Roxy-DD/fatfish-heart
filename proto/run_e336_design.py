"""E-336 重新审视设计：只注入一次 vs 每轮注入

代码逻辑（E-335 发现）：
  if (st.injected) {
    // 已注入过：只补常驻指令（相关记忆不再自动塞）
    return { ...result, contexts: [...contexts, { name:'cosmos-mem', text: memoryPolicy }] }
  }
  st.injected = true
  // ↓ 只有第一次才走到这里
  const found = await callKernel(cfg, 'search', { query: latest, ... })

即：**相关记忆只在会话第一轮注入一次**，后续靠模型主动 mem_search。

这个设计的问题：
  ① 长会话中，第一轮的 query 可能与后面的话题完全无关
  ② 用户换了话题，系统不会再给相关记忆
  ③ 模型需要自己想到去 mem_search —— 但这不是可靠的

实测：本轮对话里我用过几次 mem_search？

验证：
  ① 统计审计日志里 recall（主动检索）的次数
  ② 对比自动注入 vs 主动检索的比例
  ③ 判断是否需要改回"每轮注入"
"""
import sys
import os
import json
import io
import warnings

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, MEM)

from store_v2 import MemoryStore
import audit as _audit

DB = os.path.join(MEM, "dsh_mem.db")
st = MemoryStore(DB)

print("=" * 76)
print("E-336 只注入一次 vs 每轮注入")
print("=" * 76)

# ============ ① 审计统计 ============
print("\n① 审计统计")

a = _audit.stats(st)
print(f"   总审计: {a['total']}")
print(f"   分类:")
for k, v in sorted(a["by_kind"].items(), key=lambda x: -x[1]):
    print(f"     {k:<12} {v:>5}")

# ============ ② recall（模型主动检索）vs inject（系统自动注入）============
print("\n② 主动检索 vs 自动注入")

recall_n = a["by_kind"].get("recall", 0)
inject_n = a["by_kind"].get("inject", 0)

print(f"   recall（模型主动 mem_search）: {recall_n}")
print(f"   inject（系统自动注入）:        {inject_n}")

if recall_n + inject_n > 0:
    ratio = recall_n / (recall_n + inject_n)
    print(f"   主动检索占比: {ratio*100:.0f}%")

# ============ ③ 最近的时间分布 ============
print("\n③ 最近 20 条 recall / inject")

rows = st.con.execute(
    "SELECT seq, at, kind, msg FROM audit_log "
    "WHERE kind IN ('recall','inject') ORDER BY seq DESC LIMIT 20").fetchall()

import time
for seq, at, kind, msg in rows:
    t = time.strftime("%H:%M:%S", time.localtime(at)) if at else "?"
    print(f"     {t}  {kind:<8} {str(msg)[:55]}")

# ============ ④ 检查注入历史 ============
print("\n④ 注入历史（系统每次注入了什么）")

rows2 = st.con.execute(
    "SELECT seq, at, msg FROM audit_log WHERE kind='inject' "
    "ORDER BY seq DESC LIMIT 10").fetchall()

print(f"   inject 记录: {len(rows2)}")
for seq, at, msg in rows2:
    t = time.strftime("%H:%M:%S", time.localtime(at)) if at else "?"
    print(f"     {t}  {str(msg)[:70]}")

# ============ ⑤ 结论 ============
print("\n" + "=" * 76)
print("结论")
print("=" * 76)

print(f"""
  主动检索: {recall_n} 次
  自动注入: {inject_n} 次

  设计意图（E-231 起）：
    · 只在会话第一轮注入相关记忆
    · 后续靠模型主动 mem_search

  这个设计的理由（当时的判断）：
    · 避免每轮塞记忆，省 token
    · 让模型自己决定什么时候需要回忆

  但风险：
    如果模型不主动检索，就等于没有记忆
""")

if recall_n == 0:
    print("""
  ⚠ 严重观察：主动检索次数为 0

  这意味着"后续靠模型主动 mem_search"这条路**从未被走过**。
  那么第一轮之后，记忆实际上**完全没参与**。

  如果属实，这是一个设计失败：
    省了 token，但也省掉了记忆的作用。
""")
elif recall_n < 3:
    print(f"""
  ⚠ 主动检索只有 {recall_n} 次，很少。

  "靠模型主动检索"这条路基本没走通。
""")
else:
    print(f"\n  ✅ 主动检索 {recall_n} 次，说明这条路能走通")

st.con.close()

out = {
    "experiment": "E-336",
    "recall": recall_n,
    "inject": inject_n,
    "ratio": round(recall_n / max(1, recall_n + inject_n), 3),
}
p = os.path.join(_HERE, "results", "e336_inject_design.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e336_inject_design.json")
