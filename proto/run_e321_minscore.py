"""E-321 校准 minInjectScore 阈值

E-320 我拍了个 1.0，没有数据依据。
按用户要求「不预设门槛，靠实验确定边界」。

方法：
  ① 构造「相关查询」和「不相关查询」
  ② 看两类查询的分数分布
  ③ 找分界线
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
print("E-321 校准 minInjectScore")
print("=" * 76)

# ============ 构造测试集 ============
# 相关查询：库里有对应内容
RELEVANT = [
    ("跨版本迁移能力", ["版本", "迁移", "生命周期", "回退"]),
    ("AGI 的核心观点", ["AGI", "无损迁移", "新媒介"]),
    ("记忆去重怎么做", ["去重", "重复", "相似"]),
    ("插件热重载的坑", ["热重载", "实例", "ctx", "WeakMap"]),
    ("记忆检索质量", ["检索", "recall", "FTS"]),
    ("怎么让 AI 自重启", ["自重启", "重载"]),
    ("重要性怎么定义", ["重要性", "代价", "cost"]),
    ("物理世界作为裁判", ["物理世界", "裁判", "证据"]),
]

# 不相关查询：库里没有对应内容
IRRELEVANT = [
    ("Docker 容器编排", ["Docker", "容器", "K8s"]),
    ("React 组件生命周期", ["React", "组件", "useEffect"]),
    ("如何学吉他", ["吉他", "和弦", "练习"]),
    ("北京天气预报", ["北京", "天气", "温度"]),
    ("量子计算原理", ["量子", "叠加", "纠缠"]),
]

print("\n① 相关查询的分数分布")

relevant_scores = []
for q, keywords in RELEVANT:
    res = st.search(q, top=5)
    normal = [(ep, sc, fl) for ep, sc, fl in res if fl != 2]
    if not normal:
        print(f"   {q}: 无普通结果")
        continue
    top_score = max(sc for ep, sc, fl in normal)
    # 检查最高分那条是否真的相关
    best = max(normal, key=lambda x: x[1])
    is_rel = any(k in best[0].raw_text for k in keywords)
    relevant_scores.append(top_score)
    mark = "✅" if is_rel else "⚠"
    print(f"   {mark} {q}: 最高分 {top_score:.2f} | "
          f"{best[0].raw_text[:40]}")

print("\n② 不相关查询的分数分布")

irrelevant_scores = []
for q, keywords in IRRELEVANT:
    res = st.search(q, top=5)
    normal = [(ep, sc, fl) for ep, sc, fl in res if fl != 2]
    if not normal:
        print(f"   {q}: 无普通结果（理想情况）")
        irrelevant_scores.append(0.0)
        continue
    top_score = max(sc for ep, sc, fl in normal)
    # 检查最高分那条是否相关
    best = max(normal, key=lambda x: x[1])
    is_rel = any(k in best[0].raw_text for k in keywords)
    irrelevant_scores.append(top_score)
    mark = "❌" if is_rel else "✅"
    print(f"   {mark} {q}: 最高分 {top_score:.2f} | "
          f"{best[0].raw_text[:40]}")

# ============ ③ 分析分界 ============
print("\n③ 分界分析")

if relevant_scores and irrelevant_scores:
    rel_min = min(relevant_scores)
    irr_max = max(irrelevant_scores)

    print(f"\n   相关查询的最高分: {sorted(relevant_scores, reverse=True)}")
    print(f"     最低: {rel_min:.2f}")
    print(f"\n   不相关查询的最高分: {sorted(irrelevant_scores, reverse=True)}")
    print(f"     最高: {irr_max:.2f}")

    print(f"\n   分界: {irr_max:.2f} ~ {rel_min:.2f}")
    if rel_min > irr_max:
        gap = rel_min - irr_max
        threshold = (rel_min + irr_max) / 2
        print(f"   ✅ 有清晰分界（间隔 {gap:.2f}），建议阈值 {threshold:.2f}")
        verdict = "clear"
    else:
        print(f"   ⚠ 有重叠，无法完美分开")
        threshold = irr_max
        verdict = "overlap"
else:
    threshold = 1.0
    verdict = "insufficient"
    print("   样本不足")

# ============ ④ 各阈值的表现 ============
print("\n④ 各阈值的过滤效果")

print(f"\n   {'阈值':>6} {'保留相关':>10} {'过滤无关':>10}")
for t in [0.8, 1.0, 1.2, 1.5, 1.8, 2.0]:
    rel_kept = sum(1 for s in relevant_scores if s >= t) / max(1, len(relevant_scores))
    irr_filtered = sum(1 for s in irrelevant_scores if s < t) / max(1, len(irrelevant_scores))
    mark = " ← 最优" if rel_kept >= 0.8 and irr_filtered >= 0.8 else ""
    print(f"   {t:>6.1f} {rel_kept:>9.0%} {irr_filtered:>9.0%}{mark}")

# ============ ⑤ 结论 ============
print("\n" + "=" * 76)
print("结论")
print("=" * 76)

best_t = 1.0
best_score = -1
for t in [x / 10 for x in range(5, 31)]:
    rel_kept = sum(1 for s in relevant_scores if s >= t) / max(1, len(relevant_scores))
    irr_filtered = sum(1 for s in irrelevant_scores if s < t) / max(1, len(irrelevant_scores))
    # 目标：两者都高
    score = rel_kept + irr_filtered
    if score > best_score:
        best_score = score
        best_t = t

print(f"""
  样本: 相关查询 {len(relevant_scores)} 个 / 不相关查询 {len(irrelevant_scores)} 个

  相关查询最高分范围: {min(relevant_scores):.2f} ~ {max(relevant_scores):.2f}
  不相关查询最高分范围: {min(irrelevant_scores):.2f} ~ {max(irrelevant_scores):.2f}

  最优阈值: {best_t:.1f}
  在该阈值下:
    · 保留相关: {sum(1 for s in relevant_scores if s >= best_t)/max(1,len(relevant_scores)):.0%}
    · 过滤无关: {sum(1 for s in irrelevant_scores if s < best_t)/max(1,len(irrelevant_scores)):.0%}

  → 当前代码用的 1.0 {'合适' if abs(best_t - 1.0) < 0.3 else '需要调整到 %.1f' % best_t}
""")

st.con.close()

out = {
    "experiment": "E-321",
    "relevant_scores": [round(s, 3) for s in relevant_scores],
    "irrelevant_scores": [round(s, 3) for s in irrelevant_scores],
    "best_threshold": best_t,
    "current": 1.0,
    "verdict": verdict,
}
p = os.path.join(_HERE, "results", "e321_threshold.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e321_threshold.json")
