"""E-28 记忆分层保护验证（L0~L4）

docs/72 设计：
  L0 不可变    不可失效/合并/修订 + 强制在场
  L1 常驻准则  不可失效/合并，可修订 + 强制在场
  L2 设计基石  不可失效/合并，可修订 + 按需检索
  L3 阶段性    可失效/合并/修订
  L4 一次性    可清理

验证每层的实际行为是否符合设计。
"""
import sys
import os
import json

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))

from store_v2 import MemoryStore
import cli_mcp
import hook as _hook
import consolidate as _con

db = os.path.join(_HERE, "work", "e28_levels.db")
for suf in ("", "-wal", "-shm"):
    p = db + suf
    if os.path.exists(p):
        os.remove(p)

st = MemoryStore(db)
results = []


def check(name, cond, detail=""):
    results.append({"name": name, "ok": bool(cond), "detail": detail})
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  {detail}" if detail else ""))


print("=" * 76)
print("E-28 记忆分层保护验证（L0~L4）")
print("=" * 76)

# ===== 准备：五个层级各一条 =====
print("\n【准备】创建 L0~L4 各一条")
specs = [
    ("L0", "红线R1：记忆必须能无损迁移，逐字段逐向量完全一致"),
    ("L1", "一切判断以实验数据为准，不凭主观推测"),
    ("L2", "一条记忆不是一条事实，而是一个情景"),
    ("L3", "项目当前状态：四条红线全过，召回0.881"),
    (None, "测试数据：登录密码是 8848"),   # L4 不标记
]
ids = {}
for lv, text in specs:
    r = cli_mcp.cmd_add(st, {"text": text})
    eid = r["ep_id"]
    key = lv or "L4"
    ids[key] = eid
    if lv:
        cli_mcp.cmd_hook(st, {"action": "lock", "ep_id": eid, "level": lv})
    print(f"  {key}: {text[:40]}")

# ===== 1. 层级查询 =====
print("\n【1】层级识别")
for lv, eid in ids.items():
    got = _hook.level_of(st, eid)
    # L3/L4 不标记，level_of 返回 None（未保护即 L3）
    exp = None if lv in ("L3", "L4") else lv
    check(f"{lv} 层级正确", got == exp, f"得到 {got}")
# 确认 L3 的行为等同未保护
check("L3 可失效（等同未保护）", _hook.can_invalidate(st, ids["L3"]) is True)
check("L3 可合并", _hook.can_merge(st, ids["L3"]) is True)
check("L3 可修订", _hook.can_revise(st, ids["L3"]) is True)
check("L3 不强制在场", _hook.always_present(st, ids["L3"]) is False)

# ===== 2. invalidate 行为 =====
print("\n【2】invalidate：L0/L1/L2 拒绝，L3/L4 允许")
for lv in ["L0", "L1", "L2"]:
    ok = st.invalidate(ids[lv])
    ep = st.get(ids[lv])
    check(f"{lv} invalidate 被拒", ok is False and ep.invalid_at is None)
for lv in ["L3", "L4"]:
    ok = st.invalidate(ids[lv])
    ep = st.get(ids[lv])
    check(f"{lv} invalidate 成功", ok is True and ep.invalid_at is not None)

# 恢复 L3/L4 供后续测试
for lv in ["L3", "L4"]:
    st.con.execute("UPDATE episodes SET invalid_at=NULL WHERE ep_id=?", (ids[lv],))
st.con.commit()
st.invalidate_vector_cache()

# ===== 3. revise 行为 =====
print("\n【3】revise：仅 L0 拒绝")
ok0 = st.revise(ids["L0"], new_text="改了的红线")
check("L0 revise 被拒", ok0 is False)
check("L0 原文未变", st.get(ids["L0"]).raw_text.startswith("红线R1"))
for lv in ["L1", "L2", "L3"]:
    ok = st.revise(ids[lv], new_text=st.get(ids[lv]).raw_text + "（已修订）")
    check(f"{lv} revise 允许", ok is True)

# ===== 4. supersede 行为 =====
print("\n【4】supersede：L0/L1/L2 拒绝")
for lv in ["L0", "L1", "L2"]:
    ok, ne = st.supersede(ids[lv], "取代内容")
    check(f"{lv} supersede 被拒", ok is False and ne is None)

# ===== 5. 睡眠整理跳过 =====
print("\n【5】睡眠整理：L0/L1/L2 被跳过")
r = _con.apply_sleep_verdict(st, [
    {"op": "invalidate", "ep_id": ids["L0"]},
    {"op": "invalidate", "ep_id": ids["L1"]},
    {"op": "invalidate", "ep_id": ids["L2"]},
])
check("全部被跳过", len(r.get("invalidated", [])) == 0,
      f"skipped={len(r.get('skipped_hooks', []))}")
for lv in ["L0", "L1", "L2"]:
    ep = st.get(ids[lv])
    check(f"{lv} 仍有效", ep.invalid_at is None)

# merge 跳过
r2 = _con.apply_sleep_verdict(st, [
    {"op": "merge", "keep_id": ids["L0"], "drop_id": ids["L1"],
     "merged_text": "错误合并"}
])
check("merge 被跳过", len(r2.get("merged", [])) == 0)

# ===== 6. 检索强制在场 =====
print("\n【6】检索强制在场：仅 L0/L1")
res = st.search("完全无关的问题xyz", top=3)
got = {ep.ep_id for ep, _, _ in res}
check("L0 在场", ids["L0"] in got)
check("L1 在场", ids["L1"] in got)
check("L2 不在场（按需检索）", ids["L2"] not in got,
      "L2 不该强制出现")
flag2 = {ep.ep_id for ep, _, fl in res if fl == 2}
check("flag=2 只标 L0/L1", flag2 == {ids["L0"], ids["L1"]},
      f"flag2={len(flag2)} 条")

# ===== 7. L0 不可解锁 =====
print("\n【7】L0 不可解锁")
r3 = cli_mcp.cmd_hook(st, {"action": "unlock", "ep_id": ids["L0"]})
check("L0 unlock 被拒", r3.get("ok") is False, r3.get("error", "")[:30])
r4 = cli_mcp.cmd_hook(st, {"action": "unlock", "ep_id": ids["L1"]})
check("L1 unlock 允许", r4.get("ok") is True)

# ===== 8. 统计 =====
print("\n【8】统计")
s = _hook.stats(st)
check("统计正确", s["total"] >= 2, json.dumps(s, ensure_ascii=False))

st.con.close()

# ===== 汇总 =====
print("\n" + "=" * 76)
n_ok = sum(1 for r in results if r["ok"])
print(f"结果：{n_ok}/{len(results)} 通过")
for r in results:
    if not r["ok"]:
        print(f"  ❌ {r['name']}  {r['detail']}")
print("=" * 76)

out = {"experiment": "E-28", "n_pass": n_ok, "n_total": len(results),
       "results": results}
os.makedirs(os.path.join(_HERE, "results"), exist_ok=True)
with open(os.path.join(_HERE, "results", "e28_levels.json"), "w",
          encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 results/e28_levels.json")
sys.exit(0 if n_ok == len(results) else 1)
