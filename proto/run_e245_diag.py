"""E-245 自我诊断：下一个该做什么

上一轮（E-244）修了 R4 历史漏洞。现在找下一个真问题。

检查维度：
  ① 52 条记忆的真实结构（有多少是真情景、多少是系统自述）
  ② 检索质量：能不能真的找回来（不是离线 benchmark，是实际库）
  ③ 自动记忆的质量：40 条 remember 都记了什么，有没有垃圾
  ④ 各模块的"有代码但从未被调用"清单
"""
import sys
import os
import json

_HERE = os.path.dirname(os.path.abspath(__file__))
_MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, _MEM)

from store_v2 import MemoryStore
import audit as _audit
import hook as _hook
import seed as _seed

DB = os.path.join(_MEM, "dsh_mem.db")
st = MemoryStore(DB)

print("=" * 78)
print("E-245 自我诊断")
print("=" * 78)

# ============ ① 记忆结构分析 ============
print("\n① 记忆结构：52 条都是什么")
rows = st.con.execute(
    "SELECT ep_id, raw_text, cost, affect_peak, created_at FROM episodes "
    "WHERE invalid_at IS NULL ORDER BY created_at").fetchall()

seeds = _seed.seed_titles(st)
seed_ids = set(seeds.values())

buckets = {"种子": [], "系统自述": [], "用户事实": [], "其他": []}
for eid, txt, cost, aff, cat in rows:
    if eid in seed_ids:
        buckets["种子"].append((eid, txt))
    elif any(k in txt for k in ["实现", "机制", "模块", "架构", "store_",
                                 "E-2", "实测", "实验"]):
        buckets["系统自述"].append((eid, txt))
    elif any(k in txt for k in ["用户", "忘", "记住", "偏好", "希望", "要求"]):
        buckets["用户事实"].append((eid, txt))
    else:
        buckets["其他"].append((eid, txt))

for k, v in buckets.items():
    print(f"   {k}: {len(v)} 条")

print(f"\n   非种子记忆共 {len(rows) - len(buckets['种子'])} 条")

# ============ ② 检索质量实测 ============
print("\n② 检索质量实测（用真实库，不是离线 benchmark）")
test_queries = [
    ("版本回退能力", ["跨版本", "回退", "生命周期"]),
    ("记忆重要性怎么定", ["代价", "重要性", "cost"]),
    ("睡眠机制", ["睡眠", "整理", "遗忘"]),
    ("FTS5 性能", ["FTS5", "回表", "性能"]),
    ("笔记手册", ["笔记", "手册", "册子"]),
]
hit_ok = 0
for q, keywords in test_queries:
    res = st.search(q, top=5)
    # 看命中的记忆里有没有包含关键词的
    found = False
    for ep, sc, fl in res:
        if fl == 2:
            continue  # 跳过强制注入的 L0/L1
        if any(k in ep.raw_text for k in keywords):
            found = True
            break
    mark = "✅" if found else "❌"
    if found:
        hit_ok += 1
    print(f"   {mark} '{q}' → 命中相关记忆")

print(f"\n   命中率: {hit_ok}/{len(test_queries)} = {hit_ok/len(test_queries):.1%}")

# ============ ③ 自动记忆质量 ============
print("\n③ 自动记忆质量：40 条 remember 都记了什么")
recent_remembers = st.con.execute(
    "SELECT at, msg FROM audit_log WHERE kind='remember' "
    "ORDER BY seq DESC LIMIT 10").fetchall()
for at, msg in recent_remembers[:8]:
    print(f"   · {msg[:70]}")

# 检查有没有明显垃圾
all_txt = " ".join(r[1] for r in rows if r[0] not in seed_ids)
junk_markers = ["测试", "test", "demo", "示例", "foo", "bar", "TODO"]
junk = [m for m in junk_markers if m in all_txt.lower()]
print(f"\n   垃圾标记: {junk if junk else '无 ✅'}")

# ============ ④ 有代码但未调用清单 ============
print("\n④ 模块调用情况")
astats = _audit.stats(st)
by_kind = astats["by_kind"]
for k in ["remember", "recall", "inject", "sleep", "conflict",
          "chain", "affect", "note", "todo", "config"]:
    n = by_kind.get(k, 0)
    mark = "✅" if n > 0 else "❌"
    print(f"   {mark} {k:<10} {n}")

# ============ ⑤ 结论 ============
print("\n" + "=" * 78)
print("诊断结论")
print("=" * 78)

issues = []
if hit_ok < len(test_queries):
    issues.append({
        "id": "recall-gap",
        "desc": f"检索命中 {hit_ok}/{len(test_queries)}，有查询找不回相关记忆",
        "severity": "high" if hit_ok < len(test_queries) * 0.6 else "medium",
    })
if junk:
    issues.append({"id": "junk-memory", "desc": f"有垃圾记忆: {junk}",
                   "severity": "medium"})

sys_ratio = len(buckets["系统自述"]) / max(1, len(rows) - len(buckets["种子"]))
if sys_ratio > 0.5:
    issues.append({
        "id": "self-referential",
        "desc": f"非种子记忆中 {sys_ratio:.0%} 是系统自述（关于记忆系统本身）",
        "severity": "medium",
    })

for i, iss in enumerate(issues, 1):
    print(f"\n{i}. [{iss['severity'].upper()}] {iss['desc']}")

if not issues:
    print("\n未发现明显问题。")

st.con.close()

out = {"experiment": "E-245", "issues": issues,
       "buckets": {k: len(v) for k, v in buckets.items()},
       "recall_hit": hit_ok, "recall_total": len(test_queries),
       "audit_by_kind": by_kind}
p = os.path.join(_HERE, "results", "e245_diag.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 results/e245_diag.json")
