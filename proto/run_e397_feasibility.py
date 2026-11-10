"""E-397 评估：主张4（交叉重组）和主张6（环境耦合）的可行性

原文（已读）：
  主张4：多个谱系可以交换机制、经验与结构，并通过交叉重组形成新的分支。
         数学形式：多关系网络 N=(V, E, T, τ)
         边类型：inherit / derive / transfer / exchange / recombine / recover

  主张6：生命改变环境，改变后的环境又影响后续生命。
         形式：L_t → E_{t+1} → L_{t+1} → E_{t+2}

当前系统的现实：
  · 有版本链（git tag + DB 快照）→ 单谱系
  · 有 history 表 → 个体内的修订历史
  · 有 hooks → 可删除的标记
  · **没有**多谱系概念、没有谱系间交换、没有环境状态记录

必须诚实评估：现在能做什么，不能做什么。
不能为了"实现主张4"而造一个空壳。

先查基础设施。
"""
import sys
import os
import json
import sqlite3
import warnings

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
DB = os.path.join(MEM, "dsh_mem.db")

print("=" * 78)
print("E-397 主张4/6 可行性评估")
print("=" * 78)

# ---------- ① 当前有什么 ----------
print("\n① 当前基础设施")

con = sqlite3.connect(DB)
tables = [r[0] for r in con.execute(
    "SELECT name FROM sqlite_master WHERE type='table'")]

print(f"   表: {', '.join(sorted(tables))}")

# 检查是否有谱系相关字段
cols = [c[1] for c in con.execute("PRAGMA table_info(episodes)").fetchall()]
lineage_fields = [c for c in cols if any(
    k in c.lower() for k in ["lineage", "branch", "parent", "origin",
                             "env", "environment"])]
print(f"   episodes 中的谱系相关字段: {lineage_fields or '无'}")

# hooks 有哪些类型
hook_kinds = con.execute(
    "SELECT val FROM meta WHERE key LIKE 'hook:%'").fetchall()
kinds = set()
for (v,) in hook_kinds:
    try:
        d = json.loads(v)
        kinds.add(d.get("kind"))
    except Exception:
        pass
print(f"   hook 类型: {kinds or '无'}")

# 版本链
man_path = os.path.join(_HERE, "..", ".dsh_versions.json")
for cand in [os.path.join(os.path.dirname(_HERE), "versions.json"),
             os.path.join(_HERE, "versions.json")]:
    if os.path.exists(cand):
        print(f"   版本清单: {cand}")
        break
else:
    print("   版本清单: 未找到（lifecycle.py 管理）")

con.close()

# ---------- ② 主张4 需要什么 ----------
print("\n② 主张4（交叉重组）需要什么")

print("""
   原文要求：
     · 多个谱系同时存在（V 是多个生命周期节点）
     · 谱系间可以交换机制/经验/结构（E_exchange）
     · 交叉重组产生新分支（E_recombine）

   当前系统：
     · 只有一个"谱系"（就是当前这个 DB）
     · 没有第二个谱系可与它交换
     · 没有"机制"这个可交换的实体（只有记忆条目）

   ★ 最根本的缺口：**没有"另一个谱系"存在。**
     这不是代码问题，是运行环境问题——
     当前只有我这一个实例在跑。

   可以做的最小实现：
     在同一 DB 内模拟"分支"——用 lineage 字段标记不同分支，
     允许从一个分支"借用"某些记忆到另一个分支。
     但这只是**单进程内的模拟**，不是真正的多谱系并存。

   诚实判断：现在做这个，只能做出一个"演示"，
             不是真实的多谱系演化。
""")

# ---------- ③ 主张6 需要什么 ----------
print("\n③ 主张6（生命-环境双向耦合）需要什么")

print("""
   原文要求（5.3 节的因果链）：
     系统结构 → 对物理环境施加动作 → 环境状态变化
     → 产生资源/能量/结构后果 → 影响系统维持
     → 影响后继产生与传播

   原文强调（5.1/5.2）：
     · 环境不是有意识的裁判，不给打分
     · 必须区分「人为评价」与「自然因果筛选」
     · 内部奖励不能冒充环境因果

   ★ 当前系统的"环境"是什么？
     · 用户对话（人的反馈）→ 这是**人类评价**，不是自然筛选
     · 代码执行结果（跑通/报错）→ 这是**真实因果**，最接近"环境"
     · 文件系统状态 → 也是真实因果

   可以做的最小实现：
     把「代码执行成功/失败」记录为环境事件，
     并让它影响记忆的存续（成功路径的记忆被强化，
     失败路径的被标记）。
     这是真实的因果，不是人为打分。

   诚实判断：这个比主张4 更可行，
             因为"代码执行结果"是真实的环境反馈，
             而且当前系统已经在用它（回归测试）。
""")

# ---------- ④ 结论 ----------
print("\n" + "=" * 78)
print("可行性结论")
print("=" * 78)

print("""
  主张4（交叉重组）：
    可行性：❌ 低
    原因：需要"多个谱系并存"，当前只有单一实例。
          强行实现只能做演示，不是真实演化。
    建议：暂不实现，等真的有多实例运行时再说。

  主张6（环境耦合）：
    可行性：🟡 中
    原因：「代码执行结果」已经是真实的环境反馈，
          而且系统在用它（回归测试）。
          缺的是：把它**接入记忆的存续机制**。
    建议：可以先做「环境事件 → 记忆存续」这条链。

  ★ 但更重要的判断：

    这两条主张都需要一个前提：**系统要真的在环境里跑**。
    当前我的"环境"主要是用户对话 + 代码执行。
    对话是人的评价（不算自然筛选），
    代码执行是真实因果但范围很窄。

    在环境这么窄的情况下强行实现主张4/6，
    会得到"形式上符合、实质上是空壳"的东西。
    这比不实现更糟——因为它会让人误以为做到了。
""")

out = {
    "experiment": "E-397",
    "claim4_feasible": False,
    "claim4_reason": "需要多谱系并存，当前只有单一实例",
    "claim6_feasible": "partial",
    "claim6_reason": "代码执行是真实环境反馈，但范围窄",
    "tables": sorted(tables),
    "lineage_fields": lineage_fields,
    "hook_kinds": list(kinds),
}
p = os.path.join(_HERE, "results", "e397_feasibility.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e397_feasibility.json")
