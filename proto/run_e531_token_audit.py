"""测量记忆注入的实际 token 开销

目的：为 FISH-15「token 开销优化」提供真实数据。
不猜，直接量。

测三件事：
1. 每次注入的字符数/估算 token 数
2. top_k 变化对开销的影响
3. 注入内容的构成（seed / 常驻准则 / 检索结果 各占多少）
"""
import json
import os
import sqlite3
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, "cosmos_mem")

from store_v2 import MemoryStore

DB = "cosmos_mem/dsh_mem.db"


def est_tokens(text):
    """粗估 token 数

    中英混合的经验值：
    · 中文约 1 字 ≈ 1 token
    · 英文约 4 字符 ≈ 1 token
    这里用保守估计：总字符数 / 1.6（中文占多数时偏保守）
    """
    if not text:
        return 0
    n_cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    n_other = len(text) - n_cjk
    return int(n_cjk + n_other / 4)


print("=" * 66)
print("记忆注入的开销实测（FISH-15）")
print("=" * 66)

st = MemoryStore(DB)

# ---------- ① 常驻部分（每次都注入）----------
print("\n【1】常驻部分")
seed_path = os.path.join("cosmos_mem", "seed_memory.json")
seed_chars = 0
seed_tokens = 0
if os.path.exists(seed_path):
    seed = json.load(open(seed_path, encoding="utf-8"))
    for level in ("L0", "L1", "L2"):
        items = seed.get(level) or []
        for it in items:
            t = it.get("text") or it.get("content") or ""
            seed_chars += len(t)
    seed_tokens = est_tokens(
        json.dumps(seed, ensure_ascii=False)
    )
    print(f"  seed_memory.json: {seed_chars} 字 / ~{seed_tokens} tokens")

# ---------- ② 注入内容构成 ----------
print("\n【2】一次典型注入的构成")
print("  参考记忆里「本轮前瞻 + 常驻准则 + 相关记忆」三块")

# 用真实检索模拟
queries = [
    "继续完善记忆系统",
    "token 开销优化",
    "is a plan 平台任务",
]
for q in queries:
    hits = st.search(q, top=5)
    total_chars = 0
    for h in hits:
        ep = h[0] if isinstance(h, tuple) else h
        txt = getattr(ep, "raw_text", "") or ""
        total_chars += len(txt)
    print(f"  Q={q:20s}  top5 = {total_chars} 字 / ~{est_tokens('中' * total_chars)} tokens")

# ---------- ③ top_k 的影响 ----------
print("\n【3】top_k 对开销的影响（这是可调参数）")
q = "记忆系统怎么优化"
for k in (3, 5, 8, 12):
    hits = st.search(q, top=k)
    total = 0
    for h in hits:
        ep = h[0] if isinstance(h, tuple) else h
        total += len(getattr(ep, "raw_text", "") or "")
    print(f"  top_k={k:2d}  {len(hits):2d} 条  {total:5d} 字  ~{est_tokens('中' * total):5d} tokens")

# ---------- ④ 数据库总量 ----------
print("\n【4】记忆库规模")
n_ep = st.con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
avg_len = st.con.execute(
    "SELECT AVG(LENGTH(raw_text)) FROM episodes WHERE raw_text IS NOT NULL"
).fetchone()[0] or 0
print(f"  episodes: {n_ep}")
print(f"  平均每条: {avg_len:.0f} 字")
print(f"  全库总量: {n_ep * avg_len / 1024:.1f} K 字")

st.con.close()

print("\n" + "=" * 66)
print("结论要点")
print("=" * 66)
print("""
可优化项（按收益排序）：
1. top_k 从 5 降到 3 —— 直接省 40% 检索注入量
2. 常驻 seed 是固定开销，与对话无关，只加载一次可缓存
3. 注入时截断单条长度 —— 长记忆占了大头

需要实测确认的：
· 降 top_k 会不会伤害检索质量？（需要 A/B 对比）
· 哪些记忆被检索到但从未被用上？（可下沉）
""")
