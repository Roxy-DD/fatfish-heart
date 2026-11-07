"""E-262 验证：detect_triggers 硬编码词表不适配技术记忆

E-260/E-261 诊断确认：
  · suggest_from_triage 正常（critical=14, minor=41，返回 2 条建议）
  · detect_triggers 几乎全空（67 条记忆只有 1 条有 trigger）

根因：
  TRIGGER_SCENE 是 12 个**生活/创伤场景**的硬编码词表：
    被狗咬 / 当众出丑 / 亲人去世 / 失利 / 被批评 / 走丢
    夕阳 / 毕业 / 第一次工资 / 约会 / 孩子学说话 / 通宵聊天

  而库里 67 条记忆全是技术讨论和项目需求，
  一个都匹配不上。

  这不是 bug，是**设计局限**：
    TRIGGER_SCENE 是为 E-62 "创伤情景触发"实验设计的，
    只覆盖情绪记忆场景。

  但 suggest_note_topics 用它做"主题聚类"，等于用错维度。

本实验验证：
  ① 用 trigger 聚类 vs 用其他维度聚类，哪个更适合技术记忆
  ② 提出替代方案并验证
"""
import sys
import os
import json
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
_MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, _MEM)

from store_v2 import MemoryStore
import notebook as _nb

DB = os.path.join(_MEM, "dsh_mem.db")
st = MemoryStore(DB)

print("=" * 76)
print("E-262 验证：trigger 聚类的局限与替代方案")
print("=" * 76)

rows = st.con.execute(
    "SELECT ep_id, raw_text, triggers, person, time, location "
    "FROM episodes WHERE invalid_at IS NULL").fetchall()
print(f"\n有效记忆: {len(rows)} 条")

# ============ ① 当前方案：trigger 聚类 ============
print("\n① 当前方案：按 trigger 聚类")
trig_cnt = Counter()
for eid, txt, trigs, p, t, l in rows:
    try:
        tl = json.loads(trigs) if trigs else []
    except Exception:
        tl = []
    trig_cnt.update(tl)

print(f"   trigger 种类: {len(trig_cnt)}")
print(f"   总出现次数: {sum(trig_cnt.values())}")
if trig_cnt:
    print(f"   最高频: {trig_cnt.most_common(5)}")
print(f"   → 能聚出主题: {len([k for k,v in trig_cnt.items() if v >= 3])} 个")

# ============ ② 替代方案 A：按关键词共现 ============
print("\n② 替代方案 A：按内容关键词共现")

# 提取技术关键词（简单但有效：英文词 + 中文双字以上专有词）
import re
tech_terms = [
    "FTS5", "SQLite", "Python", "onnxruntime", "React", "Cordis", "DSH",
    "记忆", "检索", "向量", "情绪", "睡眠", "笔记", "手册", "版本",
    "回退", "迁移", "红线", "保护", "审计", "矛盾", "合并", "遗忘",
    "插件", "接口", "路由", "配置", "实验", "实测", "数据", "方案",
]

term_mem = {}
for eid, txt, *_ in rows:
    for term in tech_terms:
        if term in txt:
            term_mem.setdefault(term, []).append(eid)

print("   关键词命中:")
usable = []
for term, eids in sorted(term_mem.items(), key=lambda x: -len(x[1])):
    mark = "✅" if len(eids) >= 3 else "  "
    print(f"     {mark} {term}: {len(eids)} 条")
    if len(eids) >= 3:
        usable.append((term, len(eids)))

print(f"\n   → 能聚出主题: {len(usable)} 个")

# 对比
print(f"\n   对比：trigger 方案 {len([k for k,v in trig_cnt.items() if v>=3])} 个 vs 关键词方案 {len(usable)} 个")

# ============ ③ 替代方案 B：按已有 topic 字段 ============
print("\n③ 替代方案 B：用 trigger 之外的现成字段")
# person / time / location
for field, idx in [("person", 3), ("time", 4), ("location", 5)]:
    cnt = Counter()
    for row in rows:
        try:
            v = json.loads(row[idx]) if row[idx] else []
        except Exception:
            v = []
        cnt.update(v)
    print(f"   {field}: {len(cnt)} 种, 最高 {cnt.most_common(3) if cnt else '无'}")

# ============ ④ 结论 ============
print("\n" + "=" * 76)
print("结论")
print("=" * 76)

print(f"""
  实测对比：
    · trigger 聚类:     {len([k for k,v in trig_cnt.items() if v>=3])} 个主题（几乎失效）
    · 关键词共现:       {len(usable)} 个主题（有效）
    · 结构化字段:       person/time/location 也基本为空

  根因确认：
    TRIGGER_SCENE 是为「创伤情景触发」实验（E-62）设计的
    12 个生活场景词表，只覆盖情绪记忆。

    用它给技术记忆做主题聚类，等于**用错维度找规则**。

  我的判断：
    suggest_note_topics 依赖 trigger 是设计失误。
    应该改成「按内容关键词共现」——这才是通用做法，
    不依赖预先定义的场景词表。

  下一步：改 suggest_note_topics 用关键词共现算法。
""")

st.con.close()

out = {
    "experiment": "E-262",
    "valid_memories": len(rows),
    "trigger_topics": len([k for k, v in trig_cnt.items() if v >= 3]),
    "trigger_detail": trig_cnt.most_common(10),
    "keyword_topics": len(usable),
    "keyword_detail": usable[:15],
}
p = os.path.join(_HERE, "results", "e262_cluster.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e262_cluster.json")
