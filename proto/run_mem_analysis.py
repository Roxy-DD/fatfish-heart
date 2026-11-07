"""分析记忆的类别分布，为保护层级设计提供依据

不预设分类，先看数据长什么样：
  · 按长度、cost、affect 分布
  · 按内容特征聚类（人工归纳）
"""
import sys
import os
import json
from collections import Counter

sys.path.insert(0, r"C:\Users\xuee\Desktop\肥鱼之心\proto\cosmos_mem")
from store_v2 import MemoryStore

db = r"C:\Users\xuee\Desktop\肥鱼之心\proto\cosmos_mem\dsh_mem.db"
st = MemoryStore(db)

rows = st.con.execute(
    "SELECT ep_id, raw_text, cost, affect_peak, valence, created_at "
    "FROM episodes WHERE invalid_at IS NULL ORDER BY created_at ASC"
).fetchall()

print(f"总有效记忆: {len(rows)}\n")

# ============ 内容特征分类（人工归纳，先看数据）============
CATS = {
    "A_红线与不可变约束": ["红线", "无损迁移", "不可变", "底线", "不可动摇"],
    "B_方法论与判断原则": ["实验数据为准", "以数据为准", "方法论", "判断优先级",
                          "实验是获得真理", "以实验数据", "实证"],
    "C_工作流程约定": ["遇到问题先自己", "先自己尝试", "统一询问", "新会话开始",
                      "循环回顾", "三思", "复盘"],
    "D_自主权与交互偏好": ["完全权限", "自主决定", "自主迭代", "不喜欢被反复询问",
                          "自主推进", "不要频繁请示"],
    "E_用户对记忆系统的定义": ["记忆单位", "情景", "触发", "遗忘", "情绪",
                             "固定席位", "重要性", "代价", "习惯性"],
    "F_界面与交互需求": ["UI", "界面", "配置", "时间选择器", "注入策略"],
    "G_用户身份与求职": ["27届", "校招", "INTP", "求职", "西安", "简历", "投递"],
    "H_用户思想与哲学": ["预言", "书信箱", "论文", "独立发现", "认识论",
                        "永生", "贝尔实验室", "香农", "科幻"],
    "I_技术方案约定": ["余弦", "top k", "合并", "去重", "查询改写", "GPU",
                       "quantized", "cross encoder", "精排"],
    "J_项目状态与实验结论": ["实验E-", "实测", "召回", "F1=", "性能优化",
                            "int8", "三路检索", "遗忘机制", "identity"],
    "K_测试数据": ["8848", "5678", "爷爷去世", "卡罗拉", "沪A", "MacBook",
                  "Cursor", "出大事"],
}

counts = Counter()
classified = {}
for ep_id, txt, cost, aff, val, cat in rows:
    hit = None
    for name, kws in CATS.items():
        if any(k in txt for k in kws):
            hit = name
            break
    if hit is None:
        hit = "Z_其他"
    counts[hit] += 1
    classified.setdefault(hit, []).append({
        "ep_id": ep_id, "text": txt, "cost": cost, "affect": aff,
    })

print("=" * 74)
print("记忆类别分布")
print("=" * 74)
for name in sorted(counts, key=lambda x: -counts[x]):
    print(f"  {name:<28} {counts[name]:>3} 条")

# ============ 各类型的样例 ============
print("\n" + "=" * 74)
print("各类样例（前 3 条）")
print("=" * 74)
for name in sorted(classified):
    items = classified[name]
    print(f"\n【{name}】{len(items)} 条")
    for it in items[:3]:
        print(f"   · {it['text'][:72]}")

# ============ 从"循环参与度"看哪些该永久保留 ============
print("\n" + "=" * 74)
print("按'是否需要每次都参与'分析")
print("=" * 74)

# 每次对话都必须生效的（行为准则类）
MUST_EVERY = ["实验数据为准", "以数据为准", "遇到问题先自己", "先自己尝试",
              "自主决定", "完全权限", "统一询问", "红线", "实验是获得真理",
              "循环回顾", "三思", "不喜欢被反复询问", "自主迭代"]
# 需要时可查的（事实/结论类）
ON_DEMAND = ["实验E-", "实测", "F1=", "召回", "性能"]

n_must = sum(1 for r in rows if any(k in r[1] for k in MUST_EVERY))
n_demand = sum(1 for r in rows if any(k in r[1] for k in ON_DEMAND))
n_test = counts.get("K_测试数据", 0)

print(f"  每次对话都必须生效（行为准则）: {n_must} 条")
print(f"  需要时检索（事实/结论）:        {n_demand} 条")
print(f"  测试数据（应删除）:              {n_test} 条")
print(f"  其他:                            {len(rows) - n_must - n_demand - n_test} 条")

# 保存
out = {
    "total": len(rows),
    "counts": dict(counts),
    "classified": {k: [{"ep_id": x["ep_id"], "text": x["text"]} for x in v]
                   for k, v in classified.items()},
    "must_every": n_must,
    "on_demand": n_demand,
    "test_data": n_test,
}
p = r"C:\Users\xuee\Desktop\肥鱼之心\proto\results\mem_analysis.json"
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 {p}")

st.con.close()
