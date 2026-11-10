"""E-352 记忆整理前的体检：找出近重复、过期、该沉淀成笔记的

用户要求「先整理记忆」。
不盲目跑 sleep（它需要模型判对），先做**机械体检**：

  ① 近重复对（向量 >= 0.80，用 E-309 标定的阈值）
  ② 内容过期的（描述的是旧状态，例如"有效记忆 67 条"）
  ③ 该沉淀成笔记的（多条同类技术结论）
  ④ 受保护记忆是否完好
"""
import sys
import os
import json
import warnings
from collections import Counter

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, MEM)

import numpy as np
from store_v2 import MemoryStore
import hook as _hook

DB = os.path.join(MEM, "dsh_mem.db")
st = MemoryStore(DB)

print("=" * 78)
print("E-352 记忆整理体检")
print("=" * 78)

# ---------- ① 基本状况 ----------
s = st.stats()
print(f"\n① 基本状况")
print(f"   有效 {s['valid']} / 失效 {s['invalid']} / 总计 {s['valid']+s['invalid']}")
print(f"   向量 {s['vectors']}")

prot = _hook.protected_of(st)
lv = Counter(p["level"] for p in prot)
print(f"   受保护: {dict(sorted(lv.items()))}")

# ---------- ② 近重复检测 ----------
print("\n② 近重复检测（cos >= 0.80）")

st._load_vectors(include_invalid=False)
vec = st._vec
ids = list(st._vec_ids)
norms = np.linalg.norm(vec, axis=1)
norms[norms == 0] = 1.0
vn = vec / norms[:, None]
sim = vn @ vn.T
np.fill_diagonal(sim, 0)

pairs = []
for i in range(len(ids)):
    for j in range(i + 1, len(ids)):
        c = float(sim[i, j])
        if c >= 0.80:
            pairs.append((ids[i], ids[j], c))
pairs.sort(key=lambda x: -x[2])

print(f"   近重复对: {len(pairs)}")


def text_of(eid):
    r = st.con.execute(
        "SELECT raw_text FROM episodes WHERE ep_id=?", (eid,)).fetchone()
    return r[0] if r else ""


# 排除受保护的
prot_ids = {p["ep_id"] for p in prot if p["level"] in ("L0", "L1", "L2")}
safe = [(a, b, c) for a, b, c in pairs
        if a not in prot_ids and b not in prot_ids]
print(f"   排除 L0/L1/L2 后: {len(safe)} 对")

for a, b, c in safe[:10]:
    ta, tb = text_of(a), text_of(b)
    print(f"\n   cos={c:.3f}")
    print(f"     A[{a[:8]}] {ta[:70]}")
    print(f"     B[{b[:8]}] {tb[:70]}")

# ---------- ③ 过期内容检测 ----------
print("\n③ 过期内容检测（描述旧状态的数字）")

# 常见的过期标志：写的数字与当前不符
STALE_PATTERNS = [
    ("有效记忆 67 条", "当前 91"),
    ("有效记忆 88 条", "当前 91"),
    ("有效记忆 78 条", "当前 91"),
    ("失效 270 条", "当前 347"),
    ("最新 E-", "版本标签已到 E-351"),
]

all_rows = st.con.execute(
    "SELECT ep_id, raw_text FROM episodes WHERE invalid_at IS NULL").fetchall()

stale = []
for eid, txt in all_rows:
    for pat, note in STALE_PATTERNS:
        if pat in txt:
            stale.append((eid, txt[:80], pat, note))
            break

print(f"   疑似过期: {len(stale)} 条")
for eid, txt, pat, note in stale[:8]:
    print(f"\n     [{eid[:8]}] 含「{pat}」→ {note}")
    print(f"       {txt}")

# ---------- ④ 该沉淀成笔记的 ----------
print("\n④ 该沉淀成笔记的主题聚类")

# 用关键词粗聚类
TOPICS = {
    "热重载/插件": ["热重载", "apply", "WeakMap", "ctx", "自重启", "reload"],
    "检索/召回": ["检索", "召回", "recall", "FTS", "向量", "相似度"],
    "去重/合并": ["去重", "重复", "合并", "折叠", "近重复"],
    "注入/token": ["注入", "token", "上下文", "预算"],
    "红线/版本": ["红线", "版本", "回退", "迁移", "生命周期"],
    "方法论": ["实验", "数据", "证据", "物理世界", "实测"],
}
topic_counts = Counter()
topic_items = {}
for eid, txt in all_rows:
    for topic, kws in TOPICS.items():
        if any(k in txt for k in kws):
            topic_counts[topic] += 1
            topic_items.setdefault(topic, []).append(eid)

for topic, n in topic_counts.most_common():
    print(f"   {topic:<14} {n:>3} 条")

# ---------- ⑤ 笔记现状 ----------
print("\n⑤ 笔记现状")
notes = st.con.execute(
    "SELECT note_id, title, tier, length(body) FROM notes "
    "WHERE invalid_at IS NULL ORDER BY tier, updated_at DESC").fetchall()
print(f"   共 {len(notes)} 条")
tc = Counter(n[2] for n in notes)
print(f"   按 tier: {dict(tc)}")
for nid, title, tier, blen in notes:
    print(f"     [{tier}] [{nid[:8]}] {title}  ({blen} 字)")

# ---------- ⑥ todos ----------
print("\n⑥ 待办清单")
todos = st.con.execute(
    "SELECT todo_id, title, status, kind FROM todos "
    "WHERE invalid_at IS NULL").fetchall()
print(f"   共 {len(todos)} 条")
for t in todos:
    print(f"     [{t[2]}] {t[1][:60]}")

# ---------- 结论 ----------
print("\n" + "=" * 78)
print("结论与整理计划")
print("=" * 78)

plan = []
if safe:
    plan.append(f"合并 {len(safe)} 对近重复")
if stale:
    plan.append(f"更新 {len(stale)} 条过期数字")
big_topics = [(t, n) for t, n in topic_counts.items() if n >= 5]
if big_topics:
    plan.append(f"沉淀 {len(big_topics)} 个主题为笔记：" +
                ", ".join(t for t, _ in big_topics))
if not todos:
    plan.append("待办清单为空，需要重建")

print()
for i, p in enumerate(plan, 1):
    print(f"   {i}. {p}")
print()

out = {
    "experiment": "E-352",
    "valid": s["valid"],
    "near_dup_pairs": len(safe),
    "stale": len(stale),
    "topics": dict(topic_counts),
    "notes": len(notes),
    "todos": len(todos),
    "plan": plan,
}
p = os.path.join(_HERE, "results", "e352_tidy_check.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e352_tidy_check.json")

st.con.close()
