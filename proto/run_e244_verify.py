"""E-244 验证：内容历史版本 + 程序拼接 merge

修复的两个 HIGH 级问题：
  ① revise 覆盖旧文本，无历史（违反 R4）
  ② sleep merge 让模型重写 keep 原文（与 E-243 原则冲突）
"""
import sys
import os
import json

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))

from store_v2 import MemoryStore
import cli_mcp
import history as _hist
import consolidate as _con
import notebook as nb

DB = os.path.join(_HERE, "work", "test_db", "e244.db")
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
print("E-244 验证：历史版本 + 程序拼接 merge")
print("=" * 74)

# ============ ① 记忆修订保留历史 ============
print("\n【1】记忆修订：旧文本可追溯")
ep = st.add("登录密码是 8848", resolve_conflict=False)
check("原文正确", ep.raw_text == "登录密码是 8848")

st.revise(ep.ep_id, new_text="登录密码改成 9999 了")
h = _hist.episode_history(st, ep.ep_id)
check("有 2 个版本", len(h) == 2, f"{len(h)}")
check("rev1 是旧文本", h[0]["raw_text"] == "登录密码是 8848",
      h[0]["raw_text"])
check("rev2 是新文本", h[1]["raw_text"] == "登录密码改成 9999 了")
check("当前版本标记正确", h[1]["is_current"] and not h[0]["is_current"])

# 多次修订
st.revise(ep.ep_id, new_text="登录密码最终是 7777")
h2 = _hist.episode_history(st, ep.ep_id)
check("三次修订后有 3 个版本", len(h2) == 3, f"{len(h2)}")
check("中间版本也在", h2[1]["raw_text"] == "登录密码改成 9999 了")

# ============ ② 笔记修订保留历史 ============
print("\n【2】笔记修订：旧内容可追溯")
n = nb.note_add(st, "FTS5 优化", "第一版内容：JOIN 会回表。",
                tier="note", topic="sqlite")
nb.note_revise(st, n["note_id"], body="第二版：两段式查询快 87 倍。")
nh = _hist.note_history(st, n["note_id"])
check("笔记有 2 个版本", len(nh) == 2, f"{len(nh)}")
check("旧正文保留", "JOIN 会回表" in nh[0]["body"], nh[0]["body"][:30])
check("新正文生效", "两段式" in nh[1]["body"])

# ============ ③ 程序拼接 merge（不改原文） ============
print("\n【3】sleep merge：程序拼接，原文不改")
a = st.add("测试服务器在 A 机房", resolve_conflict=False)
b = st.add("A 机房今天停电了", resolve_conflict=False)

r = _con.apply_sleep_verdict(st, [
    {"op": "merge", "keep_id": a.ep_id, "drop_id": b.ep_id},
])
check("merge 成功", len(r["merged"]) == 1)
check("标注为程序拼接", r["merged"][0].get("mode") == "program_append",
      r["merged"][0].get("mode"))

keep_now = st.get(a.ep_id)
check("keep 原文开头未被改写",
      keep_now.raw_text.startswith("测试服务器在 A 机房"),
      keep_now.raw_text[:30])
check("drop 内容被追加", "A 机房今天停电了" in keep_now.raw_text)
check("有合并标注", "｜合并:" in keep_now.raw_text)

drop_now = st.get(b.ep_id)
check("drop 已失效", drop_now.invalid_at is not None)
check("drop 指向 keep", drop_now.superseded_by == a.ep_id)

# 合并前的原文也在历史里
ah = _hist.episode_history(st, a.ep_id)
check("合并前原文进历史", any(x["raw_text"] == "测试服务器在 A 机房"
                              for x in ah), f"{len(ah)} 个版本")

# ============ ④ 模型给的 merged_text 不会覆盖原文 ============
print("\n【4】即使模型给了 merged_text，也不覆盖原文")
c = st.add("项目使用 Python 3.12", resolve_conflict=False)
d = st.add("项目依赖 onnxruntime", resolve_conflict=False)
r2 = _con.apply_sleep_verdict(st, [
    {"op": "merge", "keep_id": c.ep_id, "drop_id": d.ep_id,
     "merged_text": "完全重写的新内容，原文消失"},
])
c_now = st.get(c.ep_id)
check("原文开头仍在", c_now.raw_text.startswith("项目使用 Python 3.12"),
      c_now.raw_text[:30])
check("模型文本未出现（完全忽略 merged_text）",
      "完全重写的新内容" not in c_now.raw_text,
      c_now.raw_text[:80])
check("也没有备注行", "｜备注" not in c_now.raw_text)

# ============ ⑤ 历史查询命令 ============
print("\n【5】历史查询命令")
r3 = cli_mcp.cmd_history(st, {"kind": "episode", "id": ep.ep_id})
check("查询记忆历史", r3.get("ok") and r3.get("count") == 3)
r4 = cli_mcp.cmd_history(st, {"kind": "note", "id": n["note_id"]})
check("查询笔记历史", r4.get("ok") and r4.get("count") == 2)
r5 = cli_mcp.cmd_history(st, {"kind": "stats"})
check("历史统计", r5.get("ok") and
      r5["stats"]["episodes"]["history_records"] > 0,
      json.dumps(r5.get("stats", {}), ensure_ascii=False)[:80])

# ============ ⑥ R1 红线：历史表不影响 episodes/vectors ============
print("\n【6】R1：历史表是独立的")
n_ep_total = st.con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
n_hist = st.con.execute("SELECT COUNT(*) FROM episodes_history").fetchone()[0]
check("history 表独立存在", n_hist > 0, f"{n_hist} 条历史")
# 本测试写入 5 条记忆：1（修订）+ 2（merge）+ 2（merge）
check("episodes 总数符合预期（5 条写入）", n_ep_total == 5,
      f"{n_ep_total} 条（失效的也在库内）")
valid_n = st.con.execute(
    "SELECT COUNT(*) FROM episodes WHERE invalid_at IS NULL").fetchone()[0]
check("失效的仍在库（R2 不遗忘）", n_ep_total > valid_n,
      f"总 {n_ep_total} / 有效 {valid_n}")

st.con.close()

# ---------- 汇总 ----------
print("\n" + "=" * 74)
n_ok = sum(1 for r in results if r["ok"])
print(f"结果：{n_ok}/{len(results)} 通过")
for r in results:
    if not r["ok"]:
        print(f"  ❌ {r['name']}  {r['detail']}")
print("=" * 74)

out = {"experiment": "E-244", "n_pass": n_ok, "n_total": len(results),
       "results": results}
p = os.path.join(_HERE, "results", "e244_history.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 results/e244_history.json")

sys.exit(0 if n_ok == len(results) else 1)
