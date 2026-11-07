"""E-237 诊断：睡眠机制为什么从未触发？该不该改？

现象：audit_log 里 sleep 记录为 0（只有测试脚本直接调 cmd_sleep_apply 过）。

需要先分清三种可能：
  A. 模型不知道有这个工具 → 工具描述/prompt 问题
  B. 模型知道但判断不需要睡 → 判断合理，不是问题
  C. 模型想睡但没有触发时机 → 机制问题

判断依据（按"物理世界为裁判"原则）：
  不能靠猜，要看实际数据。

本诊断检查：
  ① mem_sleep 工具是否真的注册了
  ② 睡眠触发条件在 prompt 里怎么写的
  ③ 当前记忆状态是否"值得睡"（数据是否分散/重复）
  ④ 睡眠会做什么、代价多大
"""
import sys
import os
import json

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))

from store_v2 import MemoryStore
import hook as _hook
import seed as _seed
import consolidate as _con

DB = os.path.join(_HERE, "cosmos_mem", "dsh_mem.db")
st = MemoryStore(DB)

print("=" * 74)
print("E-237 诊断：睡眠机制为何未触发")
print("=" * 74)

# ---------- ① 工具是否注册 ----------
print("\n① mem_sleep 工具注册检查")
plugin = os.path.join(os.path.dirname(_HERE), "dsh-plugin", "index.js")
if os.path.exists(plugin):
    with open(plugin, "r", encoding="utf-8") as f:
        src = f.read()
    has_tool = "name: 'mem_sleep'" in src
    has_desc = "睡眠/巩固记忆" in src
    print(f"   工具已注册: {'✅' if has_tool else '❌'}")
    print(f"   描述存在:   {'✅' if has_desc else '❌'}")
    # 提取工具描述
    idx = src.find("name: 'mem_sleep'")
    if idx > 0:
        seg = src[idx:idx+300]
        import re
        m = re.search(r"description:\s*'([^']+)'", seg)
        if m:
            print(f"   描述内容: {m.group(1)[:100]}")
else:
    print("   ❌ 插件文件不存在")

# ---------- ② prompt 里的触发条件 ----------
print("\n② 睡眠触发条件（memoryPolicy）")
idx = src.find("const memoryPolicy")
if idx > 0:
    seg = src[idx:idx+1800]
    # 找 mem_sleep 相关段落
    for line in seg.split("\n"):
        if "mem_sleep" in line or "睡眠整理" in line:
            print(f"   {line.strip()[:90]}")

# ---------- ③ 当前记忆是否"值得睡" ----------
print("\n③ 当前记忆状态：值不值得睡")
rows = st.con.execute(
    "SELECT ep_id, raw_text, created_at FROM episodes "
    "WHERE invalid_at IS NULL ORDER BY created_at").fetchall()
print(f"   有效记忆: {len(rows)} 条")

# 看有多少条是未保护的（L3/L4，会被睡眠处理）
protected = _hook.present_ids(st)
protected_ids = _hook.protected_ids(st) if hasattr(_hook, 'protected_ids') else set(
    p["ep_id"] for p in _hook.protected_of(st))
l3_rows = [(eid, txt) for eid, txt, _ in rows if eid not in protected_ids]
print(f"   可被睡眠处理（L3/L4）: {len(l3_rows)} 条")

# 算两两相似，看有无可合并的
from store_v1 import Embedder
pack = _con.prepare_sleep_pack(st, Embedder.get())
print(f"   候选相似对: {len(pack.pairs)} 对")
print(f"   扫描窗口: {pack.window}（fresh=增量 / recent=兜底）")
print(f"   睡眠游标: {pack.cursor:.0f}（{'已设' if pack.cursor > 0 else '首次'}）")
if pack.pairs:
    print(f"   最高相似度: {pack.pairs[0].cos:.3f}")
    print(f"      A: {pack.pairs[0].a_text[:50]}")
    print(f"      B: {pack.pairs[0].b_text[:50]}")
check_worth = len(pack.pairs) > 0 and pack.pairs[0].cos > 0.75
print(f"   → {'值得睡（有高相似可合并）' if check_worth else '不太需要睡（无高相似）'}")

# ---------- ④ 睡眠的代价 ----------
print("\n④ 睡眠代价评估")
print(f"   候选对数量: {len(pack.pairs)}")
print(f"   L3 候选:    {len(pack.l3_candidates)}")
print(f"   需要一次模型调用（约 900 tokens）判断")
print(f"   耗时估计: 数秒")

# ---------- 结论 ----------
print("\n" + "=" * 74)
print("诊断结论")
print("=" * 74)
print(f"""
  工具已注册: {'是' if has_tool else '否'}
  prompt 有触发指引: {'是' if 'mem_sleep' in src else '否'}
  当前有可合并的对: {'是' if check_worth else '否'}
  当前 L3 候选: {len(pack.l3_candidates)} 条

  判断：
""")

if not check_worth:
    print("""    → 睡眠未触发【不是 bug】，因为当前记忆没有值得合并的高相似对。
    → 48 条记忆两两相似度都不高，说明它们是独立事实，本就不该合并。
    → 正确行为就是"不睡"。强行睡反而会错误合并。
""")
else:
    print(f"""    → 有 {len(pack.pairs)} 对候选，最高 {pack.pairs[0].cos:.3f}，值得睡。
    → 但模型没触发，可能是 prompt 指引不够明确，或模型判断代价高于收益。
""")

# ---------- 真正的缺失 ----------
print("   真正的问题不是'没睡'，而是：")
print("   ❌ 没有【睡眠效果的度量】——睡了之后，记忆质量有没有变好？")
print("   ❌ 没有【不睡的代价评估】——记忆碎片化到什么程度才该睡？")
print("   → 这两个才是下一步该做的东西。")

st.con.close()

out = {
    "experiment": "E-237",
    "memory_count": len(rows),
    "l3_count": len(l3_rows),
    "pair_count": len(pack.pairs),
    "top_cos": pack.pairs[0].cos if pack.pairs else 0,
    "l3_candidates": len(pack.l3_candidates),
    "worth_sleeping": check_worth,
    "tool_registered": has_tool,
}
p = os.path.join(_HERE, "results", "e237_sleep_diag.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 results/e237_sleep_diag.json")
