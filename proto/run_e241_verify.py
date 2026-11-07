"""E-241 验证：笔记 + 待办功能

验证：
  ① 笔记 CRUD（add/revise/invalidate/search/suggest）
  ② 待办 CRUD（add/done/update/list）
  ③ 红线 R1 不受影响（新表不干扰 episodes/vectors）
  ④ R2 不遗忘（只打 invalid_at）
  ⑤ suggest_note_topics 能发现该整理的主题
"""
import sys
import os
import json

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))

from store_v2 import MemoryStore
import cli_mcp
import notebook as nb

DB = os.path.join(_HERE, "work", "test_db", "e241_note.db")
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


print("=" * 72)
print("E-241 验证：笔记 + 待办")
print("=" * 72)

# ============ ① 笔记 ============
print("\n【1】笔记 CRUD")
r = cli_mcp.cmd_note(st, {
    "action": "add",
    "title": "Python 装饰器",
    "body": "## 本质\n装饰器是语法糖，等价于 f = deco(f)。\n\n## 用途\n- 日志\n- 缓存\n- 权限",
    "topic": "python",
    "tags": ["语法", "进阶"],
})
check("新建笔记", r.get("ok"), f"{r.get('note', {}).get('note_id', '')[:8]}")
nid = r.get("note", {}).get("note_id")

r = cli_mcp.cmd_note(st, {"action": "get", "note_id": nid})
check("读取笔记", r.get("ok") and r["note"]["title"] == "Python 装饰器")

r = cli_mcp.cmd_note(st, {
    "action": "revise", "note_id": nid,
    "body": "## 本质\n装饰器是接受函数返回函数的可调用对象。",
})
check("修订笔记", r.get("ok") and r["note"]["revision"] == 2,
      f"rev={r.get('note', {}).get('revision')}")

r = cli_mcp.cmd_note(st, {"action": "search", "query": "装饰器"})
check("搜索笔记", r.get("ok") and len(r.get("notes", [])) == 1)

# ============ ② 待办 ============
print("\n【2】待办 CRUD")
r = cli_mcp.cmd_todo(st, {
    "action": "add", "title": "整理 Python 知识成笔记",
    "kind": "note_hint", "priority": 5,
})
check("新建待办", r.get("ok"), f"{r.get('todo', {}).get('todo_id', '')[:8]}")
tid = r.get("todo", {}).get("todo_id")

r = cli_mcp.cmd_todo(st, {"action": "list"})
check("列出待办", r.get("ok") and len(r.get("todos", [])) == 1)

r = cli_mcp.cmd_todo(st, {"action": "done", "todo_id": tid})
check("标记完成", r.get("ok") and r["todo"]["status"] == "done")

r = cli_mcp.cmd_todo(st, {"action": "stats"})
check("待办统计", r.get("ok") and r["stats"]["done"] == 1)

# 默认列表不含已完成
r = cli_mcp.cmd_todo(st, {"action": "list"})
check("默认不含已完成", len(r.get("todos", [])) == 0)
r = cli_mcp.cmd_todo(st, {"action": "list", "include_done": True})
check("含已完成时能列出", len(r.get("todos", [])) == 1)

# ============ ③ suggest_note_topics ============
print("\n【3】发现该整理的主题")
# 造一批同主题记忆
_trig = '["python", "装饰器"]'
for i in range(6):
    eid = st.add(f"python 装饰器笔记 {i}", resolve_conflict=False).ep_id
    st.con.execute("UPDATE episodes SET triggers=? WHERE ep_id=?", (_trig, eid))
st.con.commit()

r = cli_mcp.cmd_note(st, {"action": "suggest", "min_count": 5})
topics = r.get("topics", [])
check("发现待整理主题", len(topics) > 0, f"{len(topics)} 个")
if topics:
    print(f"     建议: {topics[0]['topic']} ({topics[0]['count']} 条记忆)")

# ============ ④ R1 红线不受影响 ============
print("\n【4】R1 红线：新表不干扰 episodes/vectors")
# 记录当前 episodes/vectors 状态
n_ep = st.con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
n_vec = st.con.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
# 重开连接比对
st.con.close()
st2 = MemoryStore(DB)
n_ep2 = st2.con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
n_vec2 = st2.con.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
check("episodes 数量一致", n_ep == n_ep2, f"{n_ep} → {n_ep2}")
check("vectors 数量一致", n_vec == n_vec2, f"{n_vec} → {n_vec2}")
check("notes 表独立存在",
      st2.con.execute("SELECT COUNT(*) FROM notes").fetchone()[0] >= 1)
check("todos 表独立存在",
      st2.con.execute("SELECT COUNT(*) FROM todos").fetchone()[0] >= 1)

# ============ ⑤ R2 不遗忘 ============
print("\n【5】R2 不遗忘：只打标记不删除")
cli_mcp.cmd_note(st2, {"action": "invalidate", "note_id": nid})
row = st2.con.execute(
    "SELECT invalid_at, body FROM notes WHERE note_id=?", (nid,)).fetchone()
check("笔记仍存在", row is not None)
check("已打失效标记", row and row[0] is not None)
check("正文未丢失", row and len(row[1]) > 0)

st2.con.close()

# ============ 汇总 ============
print("\n" + "=" * 72)
n_ok = sum(1 for r in results if r["ok"])
print(f"结果：{n_ok}/{len(results)} 通过")
for r in results:
    if not r["ok"]:
        print(f"  ❌ {r['name']}  {r['detail']}")
print("=" * 72)

out = {"experiment": "E-241", "n_pass": n_ok, "n_total": len(results),
       "results": results}
p = os.path.join(_HERE, "results", "e241_notebook.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 results/e241_notebook.json")

sys.exit(0 if n_ok == len(results) else 1)
