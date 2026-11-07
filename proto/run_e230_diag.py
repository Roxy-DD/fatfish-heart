"""E-230 诊断：强制注入 L0/L1 是否会削弱模型主动回忆的动机

假设（我的怀疑）：
  系统每次强制塞 9 条 L0/L1（约 2200 字）进上下文，
  模型看到"记忆已经在眼前了"，就不会主动调 mem_search 去查 L3/L4 的细节。
  → 强制注入可能产生"依赖"，反而降低主动回忆率。

这违背用户的核心要求："记忆检索应由模型自动触发，不依赖显式指令"。

本实验只做**测量**，不改机制：
  ① 统计当前强制注入的字符数占比
  ② 检查 store.search 在不命中 L0/L1 时的表现
  ③ 估算注入成本（每轮多花多少 token）

如果假设成立 → 后续实验改成"L0/L1 摘要注入 + 完整内容按需"。
"""
import sys
import os
import json

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))

from store_v2 import MemoryStore
import hook as _hook
import seed as _seed

DB = os.path.join(_HERE, "cosmos_mem", "dsh_mem.db")
st = MemoryStore(DB)

print("=" * 72)
print("E-230 诊断：强制注入的成本与影响")
print("=" * 72)

# ---------- ① 强制注入的实际成本 ----------
present = _hook.present_ids(st)
rows = st.con.execute(
    "SELECT ep_id, raw_text FROM episodes WHERE invalid_at IS NULL").fetchall()
present_texts = [txt for eid, txt in rows if eid in present]
present_chars = sum(len(t) for t in present_texts)

all_chars = sum(len(t) for _, t in rows)
print(f"\n① 强制注入（L0+L1）成本")
print(f"   条数: {len(present)} 条")
print(f"   字符: {present_chars} 字")
print(f"   占全部有效记忆: {present_chars/max(1,all_chars)*100:.1f}%")
# 中文约 1.5 字/token
print(f"   估算 token: ~{int(present_chars/1.5)} tokens/轮")

# ---------- ② 检索时 L0/L1 的干扰 ----------
print(f"\n② 检索干扰：无关 query 下 L0/L1 是否挤占结果")
test_queries = [
    "今天天气怎么样",
    "帮我写个 Python 函数",
    "推荐一家餐厅",
]
for q in test_queries:
    res = st.search(q, top=5)
    forced = [1 for ep, _, fl in res if fl == 2]
    normal = [1 for ep, _, fl in res if fl != 2]
    print(f"   '{q[:16]}'")
    print(f"     结果 {len(res)} 条：强制 {len(forced)} + 普通 {len(normal)}")

# ---------- ③ 用户实际记忆的占比 ----------
print(f"\n③ 记忆构成")
seeds = _seed.seed_stats(st)
stats = st.stats()
print(f"   种子记忆: {seeds['valid']} 条")
print(f"   用户记忆: {stats['valid'] - seeds['valid']} 条")
print(f"   总有效:   {stats['valid']} 条")

# ---------- ④ 关键问题：L0/L1 到底需不需要每轮全文 ----------
print(f"\n④ L0/L1 逐条长度（判断能否摘要化）")
titles = _seed.seed_titles(st)
id2t = {v: k for k, v in titles.items()}
for eid, txt in rows:
    if eid in present:
        lv = _hook.level_of(st, eid) or "?"
        title = id2t.get(eid, "?")
        print(f"   [{lv}] {title:<12} {len(txt):>4} 字")

# ---------- ⑤ 结论 ----------
print(f"\n{'='*72}")
print("诊断结论")
print("=" * 72)
per_round = int(present_chars / 1.5)
print(f"\n  每轮固定消耗 ~{per_round} tokens 在 L0/L1 上")
print(f"  若一轮对话 20 轮，累计 ~{per_round*20} tokens")
print(f"\n  关键问题：这 {len(present)} 条准则，是否需要**每轮全文**在场？")
print("  其中「红线」221 字是架构约束，模型不需要每轮读全文才能工作——")
print("  它更像是『设计者知道』，而非『执行者每步要看』。")

out = {
    "experiment": "E-230",
    "present_count": len(present),
    "present_chars": present_chars,
    "present_tokens_est": per_round,
    "all_chars": all_chars,
    "ratio": round(present_chars / max(1, all_chars), 4),
}
p = os.path.join(_HERE, "results", "e230_diag.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 results/e230_diag.json")

st.con.close()
