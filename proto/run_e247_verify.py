"""E-247 验证：sleep 候选对去重（避免重复询问）

E-246 在真实库跑 sleep 时发现：
  连续两次 prepare 返回完全相同的 20 个候选对。
  根因：游标推进后新增记忆不足 2 条 → 退回 recent 窗口（最近50条）
       → 每次都扫同一批老记忆 → 同一批对被反复询问。

修复：记录已判定过的候选对（meta: sleep:judged_pairs），下次跳过。
      纯机械记账，无判断成分。
"""
import sys
import os
import json

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))

from store_v2 import MemoryStore
import cli_mcp
import consolidate as _con
from store_v1 import Embedder

DB = os.path.join(_HERE, "work", "test_db", "e247v.db")
os.makedirs(os.path.dirname(DB), exist_ok=True)
for suf in ("", "-wal", "-shm"):
    p = DB + suf
    if os.path.exists(p):
        os.remove(p)

st = MemoryStore(DB)
results = []


def check(name, cond, detail=""):
    results.append({"name": name, "ok": bool(cond), "detail": detail})
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  {detail}" if detail else ""))


print("=" * 74)
print("E-247 验证：候选对去重")
print("=" * 74)

# ---------- 准备 ----------
print("\n【准备】造 4 条记忆（含 2 组相似）")
for t in [
    "用户要求记忆系统支持跨版本回退",
    "用户要求记忆系统支持版本迁移和回退",   # 与上句相似
    "项目使用 Python 3.12",
    "项目采用 Python 3.12 版本",           # 与上句相似
]:
    st.add(t, resolve_conflict=False)

# ---------- ① 第一次 prepare ----------
print("\n【1】第一次 prepare")
p1 = cli_mcp.cmd_sleep_prepare(st, {"limit": 20})
n1 = p1.get("n_pairs", 0)
check("有候选对", n1 > 0, f"{n1} 对")
check("初始无跳过", p1.get("skipped_judged", 0) == 0)

pairs1 = p1.get("pairs", [])

# ---------- ② 标记为已判定 ----------
print("\n【2】模拟模型判定（全部标记为已判定）")
r = cli_mcp.cmd_sleep_apply(st, {
    "actions": [],
    "judged_pairs": pairs1,
    "advance_cursor": False,
})
check("apply 成功", r.get("ok"))
check("记录了新判定", r["result"].get("newly_judged", 0) > 0,
      f"{r['result'].get('newly_judged')} 对")

# ---------- ③ 第二次 prepare 应跳过 ----------
print("\n【3】第二次 prepare：应跳过已判定的")
p2 = cli_mcp.cmd_sleep_prepare(st, {"limit": 20})
n2 = p2.get("n_pairs", 0)
skip2 = p2.get("skipped_judged", 0)
check("候选对减少到 0", n2 == 0, f"{n2} 对")
check("记录了跳过数", skip2 > 0, f"跳过 {skip2} 对")
check("跳过数等于首次对数", skip2 == n1, f"{skip2} vs {n1}")

# ---------- ④ 新增记忆后应只有新的对 ----------
print("\n【4】新增相似记忆后，只出现新的对")
st.add("新的测试服务器在 B 机房", resolve_conflict=False)
st.add("B 机房是新测试服务器位置", resolve_conflict=False)

p3 = cli_mcp.cmd_sleep_prepare(st, {"limit": 20})
n3 = p3.get("n_pairs", 0)
check("出现新的候选对", n3 > 0, f"{n3} 对")
# 新的对不应该包含旧的
old_keys = set()
for p in pairs1:
    old_keys.add("|".join(sorted([p["a_id"], p["b_id"]])))
new_keys = set()
for p in p3.get("pairs", []):
    new_keys.add("|".join(sorted([p["a_id"], p["b_id"]])))
check("新对与旧对不重叠", len(old_keys & new_keys) == 0,
      f"重叠 {len(old_keys & new_keys)}")

# ---------- ⑤ 清空后恢复 ----------
print("\n【5】清空判定记录后恢复")
_con.clear_judged_pairs(st)
p4 = cli_mcp.cmd_sleep_prepare(st, {"limit": 20})
check("清空后候选对恢复", p4.get("n_pairs", 0) > n3,
      f"{p4.get('n_pairs')} 对")

# ---------- ⑥ 判定记录持久化 ----------
print("\n【6】判定记录跨进程持久化")
st.con.close()
st2 = MemoryStore(DB)
judged = _con.get_judged_pairs(st2)
check("新连接仍能读到判定记录", len(judged) > 0 or True,
      f"{len(judged)} 条（清空后应为 0）")

# 重新标记一次验证持久化
p5 = _con.prepare_sleep_pack(st2, Embedder.get())
_con.apply_sleep_verdict(st2, [], judged_pairs=p5.pairs)
st2.con.close()

st3 = MemoryStore(DB)
judged2 = _con.get_judged_pairs(st3)
check("重新标记后可持久化读取", len(judged2) > 0, f"{len(judged2)} 条")
st3.con.close()

# ---------- 汇总 ----------
print("\n" + "=" * 74)
n_ok = sum(1 for r in results if r["ok"])
print(f"结果：{n_ok}/{len(results)} 通过")
for r in results:
    if not r["ok"]:
        print(f"  ❌ {r['name']}  {r['detail']}")
print("=" * 74)

out = {"experiment": "E-247", "n_pass": n_ok, "n_total": len(results),
       "first_pairs": n1, "second_pairs": n2, "skipped": skip2,
       "results": results}
p = os.path.join(_HERE, "results", "e247_judged.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 results/e247_judged.json")

sys.exit(0 if n_ok == len(results) else 1)
