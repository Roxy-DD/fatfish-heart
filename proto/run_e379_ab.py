"""E-379 未决实验：只注入一次 vs 每轮注入

问题背景（E-336 发现）：
  · 设计：只在会话第一轮注入相关记忆，后续靠模型主动 mem_search
  · 实测：主动检索 4 次 / 自动注入 28 次
  · 疑点：如果模型不主动检索，第一轮之后记忆就完全不参与

现在有足够数据做定量分析：
  ① 一轮对话里，模型平均主动检索几次？
  ② 第一轮注入的记忆，与后续话题的相关性如何？
  ③ 如果改成每轮注入，成本增加多少？

★ 这是实验，不是改动。先量化，再决定改不改。
"""
import sys
import os
import json
import time
import warnings
import sqlite3

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
DB = os.path.join(MEM, "dsh_mem.db")

print("=" * 78)
print("E-379 只注入一次 vs 每轮注入 —— 量化分析")
print("=" * 78)

con = sqlite3.connect(DB)

# ---------- ① 审计日志里的 recall vs inject ----------
print("\n① 主动检索 vs 自动注入")

kinds = {}
for k, n in con.execute(
        "SELECT kind, COUNT(*) FROM audit_log GROUP BY kind"):
    kinds[k] = n

recall = kinds.get("recall", 0)
inject = kinds.get("inject", 0)
print(f"   recall（模型主动查）: {recall}")
print(f"   inject（系统自动塞）: {inject}")

if recall + inject > 0:
    print(f"   主动检索占比: {recall/(recall+inject)*100:.1f}%")

# ---------- ② recall 的时间分布（看是否集中在某些时段）----------
print("\n② recall 的时间分布")

recalls = [r[0] for r in con.execute(
    "SELECT at FROM audit_log WHERE kind='recall' ORDER BY at")]
print(f"   共 {len(recalls)} 次")
if recalls:
    for t in recalls[:10]:
        print(f"     {time.strftime('%m-%d %H:%M:%S', time.localtime(t))}")
    if len(recalls) > 10:
        print(f"     ... 共 {len(recalls)} 次")

# ---------- ③ 每轮对话的检索密度 ----------
print("\n③ 每轮对话的检索密度")

# 用 kernel-ready 之后的时间段估算"轮次"
events_count = con.execute(
    "SELECT COUNT(*) FROM audit_log WHERE kind='recall'").fetchone()[0]

# assemble 注入次数已知
print(f"   一个会话生命周期内：")
print(f"     · 自动注入 1 次（首轮）")
print(f"     · 主动检索 {recall} 次（累计）")

# ---------- ④ 估算"每轮注入"的额外成本 ----------
print("\n④ 每轮注入的成本估算")

# 首轮注入的实际字符数
rows = con.execute(
    "SELECT extra FROM audit_log WHERE kind='inject'").fetchall()
chars_list = []
for (ex,) in rows:
    try:
        d = json.loads(ex or "{}")
        c = d.get("content_chars")
        if isinstance(c, int):
            chars_list.append(c)
    except Exception:
        pass

if chars_list:
    avg_chars = sum(chars_list) / len(chars_list)
    print(f"   历史注入平均字符数: {avg_chars:.0f}")
    print(f"   若改成每轮注入：")
    print(f"     · 假设一次会话 30 轮（本次迭代的经验值）")
    print(f"     · 额外成本 = 30 × {avg_chars:.0f} = {30*avg_chars:.0f} 字符")
    print(f"     · 约 {30*avg_chars/1.3:.0f} tokens")

# ---------- ⑤ 关键：首轮注入的记忆后来被用过吗 ----------
print("\n⑤ 首轮注入的记忆后来被用过吗？")

# 看 visit 计数（每个通道被调用次数）
print("   通道访问计数（channel 表）：")
try:
    rows2 = con.execute(
        "SELECT key, val FROM meta WHERE key LIKE 'visits:%' "
        "ORDER BY CAST(val AS INTEGER) DESC LIMIT 8").fetchall()
    if rows2:
        for k, v in rows2:
            print(f"     {k[:40]:<42} {v}")
    else:
        print("     （无 visits:* 记录）")
except Exception as e:
    print(f"     查询失败: {e}")

# recall_by_query 计数
rows3 = con.execute(
    "SELECT key, val FROM meta WHERE key LIKE 'recall_by_query:%' "
    "ORDER BY CAST(val AS INTEGER) DESC LIMIT 8").fetchall()
print(f"\n   被检索命中最多的记忆（recall_by_query）：")
if rows3:
    for k, v in rows3:
        print(f"     {k[:40]:<42} {v}")
else:
    print("     （无记录）")

# ---------- ⑥ 判定 ----------
print("\n" + "=" * 78)
print("分析结论")
print("=" * 78)

print(f"""
  实测数据：
    · 自动注入 {inject} 次（每会话 1 次，符合设计）
    · 主动检索 {recall} 次

  ★ 关键判断：

    主动检索 {recall} 次相对于 {inject} 次注入，比例是
    {recall/max(1,inject):.1f} : 1。

    {"→ 模型确实在主动检索，设计成立" if recall >= inject * 0.5 else "→ 主动检索极少，设计可能失效"}

  但要注意：recall 统计的是**我主动调 mem_search** 的次数。
  在我这个会话里，我几乎不主动查记忆——
  因为信息都在当前对话上下文里，不需要查。

  这是设计上的一个真实矛盾：
    · 「只注入一次」假设模型会主动查
    · 但模型没有动机主动查（除非它意识到自己忘了什么）

  候选方案：
    A. 保持现状（省 token，依赖模型自觉）
    B. 改成每轮注入（不省 token，但记忆必参与）
    C. **折中：每 N 轮注入一次**（成本可控，覆盖率提升）
    D. 折中：话题切换时注入（需要检测话题变化）

  我的判断：
    当前证据**不足以判定哪个更好**。
    需要 A/B 对照实验（同一个任务，两种配置，比效果）。

    而 A/B 实验需要真实的多轮任务，成本很高。
    → 先把这条留作「待实验」，不凭直觉改。
""")

con.close()

out = {
    "experiment": "E-379",
    "recall": recall,
    "inject": inject,
    "avg_chars": round(avg_chars, 0) if chars_list else None,
    "verdict": "证据不足，需 A/B 对照实验",
}
p = os.path.join(_HERE, "results", "e379_inject_design.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e379_inject_design.json")
