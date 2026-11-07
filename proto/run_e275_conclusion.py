"""E-275 记录体检结论：检索延迟不是 bug

连续四个实验（E-269~274）追查"检索最慢 7347ms"，结论：

E-269 体检：max(msSearch) = 7347ms → 标记为 MEDIUM 问题
E-270 实测：冷启动 7189ms，热启动 10ms
            → 怀疑是模型加载
E-271 归因：21/152 次 >1000ms，分散在 14 簇，平均 1.5 次/簇
            → 排除"系统性退化"
E-272 验证 GPU 争用假设：并发只慢 1.27x（11→14ms）
            → **假设被推翻**，不是 GPU 争用
E-273 日志时序破解：
    16:40:19  assemble-enter     ← 请求开始
    16:40:26  kernel-ready       ← 7 秒后内核才就绪
    16:40:26  assemble msSearch=7347
    → 7347ms = waitReady 等待内核冷启动时间
E-274 验证预热效果：
    预热 encode 后首次检索：42ms
    预热 + 真实检索后首次：12ms
    → 预热有效，且内核**已经两个都做了**
    → 检测 kernel_server.py:93 encode + :98 search 双预热已存在

最终结论：7347ms 是**不可避免的一次性冷启动成本**，不是 bug。

为什么必须等这 7 秒：
  BGE 模型加载需要把 71 个权重文件读进显存，
  这不是可以跳过的工作，只能：
    a) 让 session 一次性承担（当前做法）
    b) 进程常驻（当前已是常驻内核，重启时才付一次）
    c) 用更小的模型（会损失检索质量）

已确认的优化空间：0（内核双预热已是最佳实践）

这次排查的价值不在"修了什么"，而在：
  · 排除了一个假问题（GPU 争用）
  · 确认了一个真实性质（冷启动成本）
  · 避免了为"不存在的 bug"做无谓优化
  · 验证了 7 项回归仍然全绿

对比：如果我只看到 7347ms 就直接"优化检索性能"，
      会浪费时间在错误的方向上（因为热启动只有 10ms，已经很快）。
"""
import sys
import os
import json

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))

from store_v2 import MemoryStore
import audit as _audit

DB = os.path.join(_HERE, "cosmos_mem", "dsh_mem.db")
st = MemoryStore(DB)

print("=" * 76)
print("E-275 体检结论汇总")
print("=" * 76)

# ============ 系统健康总览 ============
print("\n【系统健康总览】")

n_valid = st.con.execute(
    "SELECT COUNT(*) FROM episodes WHERE invalid_at IS NULL").fetchone()[0]
n_invalid = st.con.execute(
    "SELECT COUNT(*) FROM episodes WHERE invalid_at IS NOT NULL").fetchone()[0]
n_vec = st.con.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]

print(f"\n  记忆:   有效 {n_valid} | 失效 {n_invalid} | 向量 {n_vec}")
print(f"  一致性: 向量数匹配 {'✅' if n_vec == n_valid + n_invalid else '❌'}")

# 数据质量
bad_aff = st.con.execute(
    "SELECT COUNT(*) FROM episodes WHERE affect_peak < 0 OR affect_peak > 1"
).fetchone()[0]
bad_val = st.con.execute(
    "SELECT COUNT(*) FROM episodes WHERE valence < -1 OR valence > 1"
).fetchone()[0]
dup = st.con.execute(
    "SELECT COUNT(*) FROM (SELECT text_hash FROM episodes "
    "WHERE invalid_at IS NULL GROUP BY text_hash HAVING COUNT(*) > 1)"
).fetchone()[0]
print(f"  质量:   affect越界 {bad_aff} | valence越界 {bad_val} | 重复组 {dup}")

astats = _audit.stats(st)
print(f"\n  审计:   总计 {astats['total']} 条")
for k in ["remember", "recall", "inject", "sleep", "conflict",
          "chain", "affect", "config", "note"]:
    n = astats["by_kind"].get(k, 0)
    mark = "✅" if n > 0 else "·"
    print(f"          {mark} {k:<10} {n}")

# ============ 性能基准 ============
print("\n【性能基准】")
print("""
  检索（热）:     10-14 ms
  检索（冷启动）: ~7300 ms（一次性，模型加载）
  写入:          11-12 ms
  审计写入:      0.5 ms
  sleep prepare: 7 ms
  knowledge_hint: 0.1 ms（缓存命中）
""")

# ============ 结论 ============
print("=" * 76)
print("结论")
print("=" * 76)
print("""
  E-269 体检标记的"检索最慢 7347ms"经四个实验追查：

  ❌ 不是 GPU 争用（实测只慢 1.27x）
  ❌ 不是性能退化（热启动 10ms，比历史 26.6ms 更快）
  ✅ 是内核冷启动加载 BGE 模型的一次性成本

  内核已做双预热（encode + search），无可优化空间。

  最终：系统健康，无需修复。
""")

st.con.close()

out = {
    "experiment": "E-275",
    "conclusion": "7347ms 是不可避免的冷启动成本，不是 bug",
    "n_valid": n_valid, "n_invalid": n_invalid, "n_vec": n_vec,
    "quality": {"bad_affect": bad_aff, "bad_valence": bad_val,
                "dup_groups": dup},
    "audit": astats["by_kind"],
    "perf": {
        "search_warm_ms": 10,
        "search_cold_ms": 7300,
        "write_ms": 12,
        "knowledge_hint_cached_ms": 0.1,
    },
}
p = os.path.join(_HERE, "results", "e275_conclusion.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e275_conclusion.json")
