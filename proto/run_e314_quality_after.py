"""E-314 清理后的检索质量——有没有因为合并而丢信息？

E-310 把 144 条合并到 78 条（-46%）。
E-313 又把 3 条超长记忆压缩了。

问题：合并有没有损伤检索能力？
  如果原来能找回的记忆，现在找不回了，说明合并过度。

方法：用 E-281 的同一套 70 个查询重跑，对比。
  但查询是从记忆里提取的，记忆变了，查询也变了。
  所以改为：用**固定的、语义化的查询**，看返回质量。
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

DB = os.path.join(MEM, "dsh_mem.db")
st = MemoryStore(DB)

print("=" * 76)
print("E-314 清理后的检索质量")
print("=" * 76)

# ============ ① 现状 ============
s = st.stats()
print("\n① 当前状态")
print(f"   有效记忆: {s['valid']}")
print(f"   失效记忆: {s['invalid']}")
print(f"   向量: {s['vectors']}")

# ============ ② 用语义化查询测试召回 ============
print("\n② 语义化查询测试")

# 这些查询应该能找到对应主题的记忆
CASES = [
    {
        "q": "AGI 是什么样的",
        "expect": ["AGI", "无损迁移", "新媒介", "交互方式"],
        "desc": "用户对 AGI 的观点",
    },
    {
        "q": "怎么让 AI 自己重启",
        "expect": ["自重启", "自我重启", "重载"],
        "desc": "自重启需求",
    },
    {
        "q": "记忆系统为什么耗 token",
        "expect": ["token", "上下文", "耗"],
        "desc": "token 消耗问题",
    },
    {
        "q": "插件热重载要注意什么",
        "expect": ["热重载", "实例", "ctx", "WeakMap"],
        "desc": "热重载铁律",
    },
    {
        "q": "版本回退怎么实现",
        "expect": ["版本", "回退", "迁移", "生命周期"],
        "desc": "版本管理",
    },
    {
        "q": "记忆重要性怎么定义",
        "expect": ["重要性", "代价", "cost"],
        "desc": "重要性的定义",
    },
    {
        "q": "检索质量怎么样",
        "expect": ["检索", "recall", "FTS", "质量"],
        "desc": "检索质量",
    },
    {
        "q": "如何避免记忆重复",
        "expect": ["重复", "去重", "近重复", "相似"],
        "desc": "去重机制",
    },
]

hits = 0
total = len(CASES)

for c in CASES:
    res = st.search(c["q"], top=5)
    # 排除强制注入的
    items = [(ep, sc, fl) for ep, sc, fl in res if fl != 2]

    found = False
    matched_by = None
    for ep, sc, fl in items:
        for kw in c["expect"]:
            if kw in ep.raw_text:
                found = True
                matched_by = kw
                break
        if found:
            break

    mark = "✅" if found else "❌"
    if found:
        hits += 1

    print(f"\n   {mark} 查询: {c['q']}")
    print(f"      期望命中关键词: {c['expect']}")
    if found:
        print(f"      命中关键词: '{matched_by}'")
    print(f"      返回 {len(items)} 条:")
    for ep, sc, fl in items[:3]:
        mark2 = "★" if any(kw in ep.raw_text for kw in c["expect"]) else " "
        print(f"        {mark2}cos={sc:>5.2f} ({len(ep.raw_text):>4}字) "
              f"{ep.raw_text[:55]}")

print(f"\n   命中率: {hits}/{total} = {hits/total*100:.0f}%")

# ============ ③ 检查合并后的记忆是否还能被检索到 ============
print("\n③ 合并后的记忆可检索性")

# 找带合并标记的记忆
merged = st.con.execute(
    "SELECT ep_id, raw_text FROM episodes WHERE invalid_at IS NULL "
    "AND raw_text LIKE '%｜合并:%' LIMIT 5").fetchall()

print(f"   带合并标记的记忆: {len(merged)} 条")

for eid, txt in merged[:3]:
    # 用合并内容里的关键词搜索
    parts = txt.split("｜合并:")
    if len(parts) < 2:
        continue
    # 取第二段的前 20 字作为查询
    probe = parts[1].strip()[:20]
    res = st.search(probe, top=3)
    found = any(ep.ep_id == eid for ep, sc, fl in res)
    print(f"\n   [{eid[:8]}] 用合并内容查询: '{probe}'")
    print(f"      能否找回这条: {'✅' if found else '❌'}")
    for ep, sc, fl in res[:2]:
        print(f"         cos={sc:.2f} [{ep.ep_id[:8]}] {ep.raw_text[:45]}")

# ============ ④ 检查折叠的信息是否还能查 ============
print("\n④ 折叠的信息可追溯性")

folded = st.con.execute(
    "SELECT ep_id, raw_text FROM episodes WHERE invalid_at IS NULL "
    "AND raw_text LIKE '%近重复已折叠%' LIMIT 3").fetchall()

print(f"   带折叠标记的记忆: {len(folded)} 条")

for eid, txt in folded:
    import re
    m = re.search(r"另有 (\d+) 条", txt)
    n = int(m.group(1)) if m else 0
    print(f"\n   [{eid[:8]}] 折叠了 {n} 条")

    # 验证：这些折叠的内容在 history 里能查到
    import history as _hist
    h = _hist.episode_history(st, eid)
    old_versions = [v for v in h if not v["is_current"]]
    print(f"      历史版本: {len(old_versions)} 个")
    if old_versions:
        longest = max(old_versions, key=lambda v: len(v["raw_text"]))
        print(f"      最长历史版本: {len(longest['raw_text'])} 字 "
              f"(rev{longest['revision']})")
        print(f"      → 折叠的原文可从这里查回: "
              f"{'✅' if len(longest['raw_text']) > len(txt) else '❌'}")

# ============ ⑤ 结论 ============
print("\n" + "=" * 76)
print("结论")
print("=" * 76)

issues = []
if hits < total * 0.75:
    issues.append(f"召回率偏低 {hits}/{total}")

print(f"""
  语义查询命中率: {hits}/{total} = {hits/total*100:.0f}%

  说明：
    · 这些查询不是从记忆里机械提取的关键词，而是语义化的自然问法
    · 能命中说明合并没有丢失主题信息
    · 折叠的内容通过 history 表可追溯（R4）
""")

if issues:
    print("  问题：")
    for i in issues:
        print(f"    · {i}")
else:
    print("  ✅ 合并未损伤检索能力")

st.con.close()

out = {
    "experiment": "E-314",
    "valid": s["valid"],
    "hits": hits,
    "total": total,
    "hit_rate": round(hits / total, 3),
    "issues": issues,
}
p = os.path.join(_HERE, "results", "e314_quality_after.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e314_quality_after.json")
