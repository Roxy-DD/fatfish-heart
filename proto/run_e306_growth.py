"""E-306 查异常：有效记忆从 88 涨到 140

E-305 基线显示有效记忆 140 条。
但 E-291 整理后是 88 条，中间我只做过少量实验，
不应该增长 52 条。

可能原因：
  a) autoRemember 在每轮对话自动记了很多（正常，但要看质量）
  b) 测试污染（我跑的实验把测试数据写进了生产库）
  c) 重复写入
  d) 统计口径变化（stats 的 valid 定义变了？）
"""
import os
import sys
import json
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(HERE, "cosmos_mem")
sys.path.insert(0, MEM)

from store_v2 import MemoryStore
import audit as _audit
import hook as _hook

DB = os.path.join(MEM, "dsh_mem.db")
st = MemoryStore(DB)

print("=" * 76)
print("E-306 查记忆数量异常增长")
print("=" * 76)

# ============ ① 当前状态 ============
s = st.stats()
print("\n① 当前统计")
print(f"   有效: {s['valid']}")
print(f"   失效: {s['invalid']}")
print(f"   总计: {s['valid'] + s['invalid']}")
print(f"   向量: {s['vectors']}")

# ============ ② 按创建时间分布 ============
print("\n② 按创建时间分布（看什么时候涨的）")

rows = st.con.execute(
    "SELECT ep_id, raw_text, created_at, invalid_at FROM episodes "
    "ORDER BY created_at DESC").fetchall()

import time
now = time.time()
buckets = {
    "10分钟内": 0,
    "1小时内": 0,
    "今天": 0,
    "3天内": 0,
    "更早": 0,
}
valid_buckets = dict(buckets)

for eid, txt, cat, inv in rows:
    age = now - (cat or 0)
    if age < 600:
        k = "10分钟内"
    elif age < 3600:
        k = "1小时内"
    elif age < 86400:
        k = "今天"
    elif age < 259200:
        k = "3天内"
    else:
        k = "更早"
    buckets[k] += 1
    if inv is None:
        valid_buckets[k] += 1

print(f"\n   {'时间段':<10} {'总数':>6} {'有效':>6}")
for k in ["10分钟内", "1小时内", "今天", "3天内", "更早"]:
    print(f"   {k:<10} {buckets[k]:>6} {valid_buckets[k]:>6}")

# ============ ③ 最近的记忆都是什么 ============
print("\n③ 最近 20 条有效记忆")

recent = st.con.execute(
    "SELECT ep_id, raw_text, created_at FROM episodes "
    "WHERE invalid_at IS NULL ORDER BY created_at DESC LIMIT 20").fetchall()

for eid, txt, cat in recent:
    t = time.strftime("%H:%M", time.localtime(cat)) if cat else "?"
    print(f"\n   [{t}] [{eid[:8]}]")
    print(f"   {txt[:110]}")

# ============ ④ 检查测试污染 ============
print("\n④ 测试污染检查")

TEST_MARKERS = [
    "测试", "test", "并发测试", "稳定性测试", "备份测试",
    "E289", "e289", "E-2", "验证自重启", "probe", "marker-",
    "cycle-", "patch-", "v302", "hmr-",
]

all_rows = st.con.execute(
    "SELECT ep_id, raw_text FROM episodes WHERE invalid_at IS NULL").fetchall()

suspects = []
for eid, txt in all_rows:
    for m in TEST_MARKERS:
        if m in txt:
            suspects.append((eid, txt[:80], m))
            break

print(f"   疑似测试污染: {len(suspects)} 条")
for eid, txt, m in suspects[:15]:
    print(f"     [{eid[:8]}] (匹配 '{m}') {txt}")

# ============ ⑤ 检查重复 ============
print("\n⑤ 重复检查")

texts = [txt for eid, txt in all_rows]
dup = Counter(texts)
dups = {t: c for t, c in dup.items() if c > 1}
print(f"   完全重复: {len(dups)} 组")
for t, c in list(dups.items())[:5]:
    print(f"     [{c}次] {t[:70]}")

# ============ ⑥ 审计统计 ============
print("\n⑥ 审计统计")
a = _audit.stats(st)
print(f"   总审计: {a['total']}")
print(f"   按类型: {dict(sorted(a['by_kind'].items()))}")

# 最近的 remember 记录
rem = st.con.execute(
    "SELECT seq, at, msg FROM audit_log WHERE kind='remember' "
    "ORDER BY seq DESC LIMIT 10").fetchall()
print(f"\n   最近 10 条 remember:")
for seq, at, msg in rem:
    print(f"     seq={seq} {msg[:70]}")

# ============ ⑦ 结论 ============
print("\n" + "=" * 76)
print("结论")
print("=" * 76)

issues = []
if len(suspects) > 5:
    issues.append(f"{len(suspects)} 条疑似测试污染")
if len(dups) > 0:
    issues.append(f"{len(dups)} 组完全重复")

print(f"\n  有效记忆: {s['valid']}")
print(f"  最近10分钟新增: {valid_buckets['10分钟内']}")
print(f"  最近1小时新增: {valid_buckets['1小时内']}")
print(f"  疑似污染: {len(suspects)}")
print(f"  完全重复: {len(dups)} 组")

if issues:
    print("\n  发现问题：")
    for i in issues:
        print(f"    · {i}")
else:
    print("\n  ✅ 增长看起来是正常的 autoRemember 积累")

st.con.close()

out = {
    "experiment": "E-306",
    "valid": s["valid"],
    "invalid": s["invalid"],
    "buckets": buckets,
    "valid_buckets": valid_buckets,
    "suspects": len(suspects),
    "dup_groups": len(dups),
    "audit": a["by_kind"],
    "issues": issues,
}
p = os.path.join(HERE, "results", "e306_growth.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 results/e306_growth.json")
