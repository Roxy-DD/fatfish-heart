"""E-248 诊断：conflict 矛盾判定的真实状态

conflict 是"记忆能否自我修正"的核心能力：
  当新信息与旧记忆冲突时，判断该采信哪个、或合成新信息。

它从未被真实调用过（audit 里 0 条）。先查：
  ① 代码链路是否完整（cmd_add → judge → cmd_judge）
  ② 历史 verdict 记录（meta 里的 conflict 数据）
  ③ 插件层是否真的会调 judge
  ④ 用真实数据模拟一次完整链路
"""
import sys
import os
import json

_HERE = os.path.dirname(os.path.abspath(__file__))
_MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, _MEM)

from store_v2 import MemoryStore
import conflict as _cf
import audit as _audit

DB = os.path.join(_MEM, "dsh_mem.db")
st = MemoryStore(DB)

print("=" * 76)
print("E-248 诊断：conflict 矛盾判定")
print("=" * 76)

# ---------- ① meta 里历史 verdict ----------
print("\n① 历史 verdict 记录")
rows = st.con.execute(
    "SELECT key, val FROM meta WHERE key LIKE 'conflict%' OR key LIKE 'merge%'"
).fetchall()
print(f"   记录数: {len(rows)}")
from collections import Counter
kinds = Counter(k.split(':')[0] for k, v in rows)
print(f"   按类型: {dict(kinds)}")

# 看几条实际内容
for k, v in rows[:3]:
    print(f"\n   {k}:")
    try:
        d = json.loads(v)
        print(f"     {json.dumps(d, ensure_ascii=False)[:200]}")
    except Exception:
        print(f"     {v[:150]}")

# ---------- ② 代码链路 ----------
print("\n② 代码链路检查")
import cli_mcp
add_src = None
import inspect
try:
    add_src = inspect.getsource(cli_mcp.cmd_add)
except Exception:
    pass

has_judge_param = add_src and 'payload.get("judge")' in add_src
has_prepare = add_src and 'prepare_candidates' in add_src
print(f"   cmd_add 支持 judge 参数: {'✅' if has_judge_param else '❌'}")
print(f"   cmd_add 会调 prepare_candidates: {'✅' if has_prepare else '❌'}")

# 检查 conflict 的核心函数
for fn in ["prepare_candidates", "needs_model", "parse_model_output",
           "apply_verdict", "log_verdict"]:
    exists = hasattr(_cf, fn)
    print(f"   conflict.{fn}: {'✅' if exists else '❌'}")

# ---------- ③ 插件层是否调 judge ----------
print("\n③ 插件层")
plugin = os.path.join(os.path.dirname(_HERE), "dsh-plugin", "index.js")
src = open(plugin, encoding="utf-8").read()
has_judge_call = "'judge'" in src and "judgeConflict" in src
print(f"   有 judgeConflict 函数: {'✅' if 'judgeConflict' in src else '❌'}")
print(f"   mem_add 里调用 judge: {'✅' if has_judge_call else '❌'}")

# 找 mem_add 的实际逻辑
idx = src.find("name: 'mem_add'")
if idx > 0:
    seg = src[idx:idx + 1500]
    if "need_judge" in seg:
        print(f"   mem_add 里处理 need_judge: ✅")
    else:
        print(f"   mem_add 里处理 need_judge: ❌")

# ---------- ④ 模拟一次完整冲突判定 ----------
print("\n④ 模拟完整链路：写一条与旧记忆冲突的内容")
# 找一条"密码"类记忆
targets = st.con.execute(
    "SELECT ep_id, raw_text FROM episodes WHERE invalid_at IS NULL "
    "AND (raw_text LIKE '%密码%' OR raw_text LIKE '%改成%' OR raw_text LIKE '%版本%') "
    "LIMIT 3").fetchall()
print(f"   找到 {len(targets)} 条可能冲突的目标：")
for eid, txt in targets:
    print(f"     {eid[:8]}: {txt[:60]}")

if targets:
    # 模拟新增一条冲突内容
    new_text = "登录密码已经改成 6666 了"
    r = cli_mcp.cmd_add(st, {"text": new_text, "judge": True})
    print(f"\n   写入 '{new_text}'")
    print(f"   ok={r.get('ok')}")
    print(f"   need_judge={r.get('need_judge')}")
    print(f"   need_affect={r.get('need_affect')}")

    if r.get("need_judge") and r.get("pack"):
        print(f"\n   ✅ 系统预筛认为需要模型判断")
        print(f"   候选包长度: {len(str(r.get('pack', '')))} 字")
        print(f"   候选包预览: {str(r.get('pack'))[:300]}")
    elif r.get("judge_skip_reason"):
        print(f"\n   ⚠ 系统预筛跳过: {r.get('judge_skip_reason')}")

    # 清理测试写入
    if r.get("ok") and r.get("ep_id"):
        st.con.execute("DELETE FROM episodes WHERE ep_id=?", (r["ep_id"],))
        st.con.execute("DELETE FROM vectors WHERE ep_id=?", (r["ep_id"],))
        st.con.commit()
        st.invalidate_vector_cache()
        print(f"\n   （已清理测试写入）")

# ---------- ⑤ 结论 ----------
print("\n" + "=" * 76)
print("诊断结论")
print("=" * 76)

issues = []
if not has_judge_param:
    issues.append("cmd_add 不支持 judge 参数")
if not has_prepare:
    issues.append("cmd_add 不调 prepare_candidates")
if not has_judge_call:
    issues.append("插件层未调用 judgeConflict")
if len(rows) == 0:
    issues.append("meta 里无任何 conflict 历史记录（从未真实判定过）")

if issues:
    print("\n发现的问题：")
    for i, s in enumerate(issues, 1):
        print(f"  {i}. {s}")
else:
    print("\n代码链路看起来完整，但从未真实触发。")

print(f"""
关键判断：
  conflict 的触发条件是「新写入的内容与已有记忆高度相似」。
  如果从未触发，可能是：
    a) 该条件确实没出现过（正常）
    b) 系统预筛的相似度门槛太高，导致从不触发（bug）
    c) 插件层没把 need_judge 接下去（bug）

  需要用实测区分这三种情况。
""")

st.con.close()

out = {"experiment": "E-248", "issues": issues,
       "meta_conflict_records": len(rows),
       "has_judge_param": bool(has_judge_param),
       "has_prepare": bool(has_prepare),
       "has_judge_call": bool(has_judge_call)}
p = os.path.join(_HERE, "results", "e248_conflict_diag.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e248_conflict_diag.json")
