"""E-265 todo note_hint 自动生成

背景：
  E-263 让 knowledge_hint 把"该整理了"送到模型面前，
  但模型未必会真的去写笔记（这是未验证的假设）。

  我的判断：即使模型没主动写，也不该漏掉。
  应该由系统机械地创建 note_hint 待办，
  这样待办列表里始终有"待整理"的项目，不会丢。

设计（纯机械，无判断）：
  当 knowledge_hint 发现某主题记忆够多时：
    ① 检查 todos 表里是否已有该主题的 note_hint
    ② 没有则创建一条（避免重复）
    ③ 有则跳过

  模型看到待办后自行决定要不要做。
"""
import sys
import os
import json

_HERE = os.path.dirname(os.path.abspath(__file__))
_MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, _MEM)

from store_v2 import MemoryStore
import notebook as _nb
import triage as _tg
import compose as _cp

DB = os.path.join(_HERE, "work", "test_db", "e265.db")
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
print("E-265 todo note_hint 自动生成")
print("=" * 74)


def sync_note_hints(st, min_count: int = 3) -> dict:
    """
    把"该整理了"的信号同步成 note_hint 待办（机械去重）。

    返回 {"created": n, "skipped": n, "items": [...]}
    """
    _nb._init(st)

    # 现有 note_hint 待办的主题（去重用）
    existing = set()
    rows = st.con.execute(
        "SELECT title, ref_note_ids FROM todos WHERE invalid_at IS NULL "
        "AND kind='note_hint'").fetchall()
    for title, refs in rows:
        existing.add(title)

    created, skipped = [], []
    candidates = []

    # 信号 1：主题累积
    try:
        for x in _nb.suggest_note_topics(st, min_count=min_count)[:5]:
            candidates.append({
                "title": f"整理主题「{x['topic']}」为笔记",
                "detail": f"该主题已有 {x['count']} 条记忆",
                "priority": min(10, x["count"]),
                "topic": x["topic"],
            })
    except Exception:
        pass

    # 信号 2：少见但重要的记忆
    try:
        for x in _tg.suggest_from_triage(st, note_hint_min=min_count):
            if x["suggest_tier"] != "note":
                continue
            candidates.append({
                "title": f"整理 {x['count']} 条重要但少用的记忆为详细笔记",
                "detail": f"{x['quadrant']} 类（少见但重要）",
                "priority": min(10, x["count"]),
                "topic": "critical",
            })
    except Exception:
        pass

    # 信号 3：随笔可装订
    try:
        for x in _cp.compile_all_topics(st, tier="scrap", min_items=min_count)[:3]:
            candidates.append({
                "title": f"装订主题「{x['topic']}」的随笔成册",
                "detail": f"已有 {x['count']} 条随笔",
                "priority": min(10, x["count"]),
                "topic": x["topic"],
            })
    except Exception:
        pass

    for c in candidates:
        if c["title"] in existing:
            skipped.append(c["title"])
            continue
        try:
            _nb.todo_add(st, title=c["title"], detail=c["detail"],
                         kind="note_hint", priority=c["priority"])
            created.append(c["title"])
            existing.add(c["title"])
        except Exception as e:
            print(f"     创建失败: {c['title'][:30]} — {e}")

    return {"created": len(created), "skipped": len(skipped),
            "items": created, "skipped_items": skipped}


# ============ 准备数据 ============
print("\n【准备】造多主题数据")
texts = [
    "记忆系统的检索通道使用向量相似度",
    "记忆系统的检索还包含关键词 FTS5",
    "记忆系统的检索需要融合多通道结果",
    "记忆系统的检索性能需要优化",
    "版本回退支持任意跨度迁移",
    "版本回退需要在宿主重建后重新注册",
    "版本回退测试表明 WeakMap 方案有效",
    "前端崩溃是回调参数遮蔽导致的",
    "前端崩溃有三类：作用域、遮蔽、重复注册",
    "前端检查脚本可以发现这类问题",
]
for t in texts:
    st.add(t, resolve_conflict=False)
print(f"   写入 {len(texts)} 条记忆")

# ============ ① 首次同步 ============
print("\n【1】首次同步 note_hint")
r1 = sync_note_hints(st, min_count=2)
print(f"   创建 {r1['created']} 条，跳过 {r1['skipped']} 条")
for it in r1["items"]:
    print(f"     + {it}")

check("创建了待办", r1["created"] > 0, f"{r1['created']} 条")

# 验证确实写入了 todos 表
todos = st.con.execute(
    "SELECT title, kind, status FROM todos WHERE invalid_at IS NULL"
).fetchall()
check("todos 表有记录", len(todos) > 0, f"{len(todos)} 条")
check("kind 都是 note_hint",
      all(t[1] == "note_hint" for t in todos),
      str([t[1] for t in todos][:3]))
check("状态都是 pending",
      all(t[2] == "pending" for t in todos))

# ============ ② 幂等：再同步一次不应重复 ============
print("\n【2】幂等性（重复调用不应重复创建）")
r2 = sync_note_hints(st, min_count=2)
print(f"   创建 {r2['created']} 条，跳过 {r2['skipped']} 条")

check("第二次创建 0 条", r2["created"] == 0, f"{r2['created']}")
check("第二次全部跳过", r2["skipped"] > 0, f"{r2['skipped']}")

n_todos = st.con.execute(
    "SELECT COUNT(*) FROM todos WHERE invalid_at IS NULL").fetchone()[0]
check("待办总数未增加", n_todos == len(todos), f"{n_todos} vs {len(todos)}")

# ============ ③ 新主题应能创建 ============
print("\n【3】新增 موضوع后应能创建新待办")
for t in ["数据库连接池配置要点", "数据库连接池大小设置",
          "数据库连接池超时处理"]:
    st.add(t, resolve_conflict=False)

r3 = sync_note_hints(st, min_count=2)
print(f"   创建 {r3['created']} 条")
for it in r3["items"]:
    print(f"     + {it}")
check("新主题创建了待办", r3["created"] > 0, f"{r3['created']}")

# ============ ④ 空库边界 ============
print("\n【4】空库不应产生待办")
tmp = os.path.join(_HERE, "work", "test_db", "e265_empty.db")
for suf in ("", "-wal", "-shm"):
    p = tmp + suf
    if os.path.exists(p):
        os.remove(p)
st2 = MemoryStore(tmp)
r4 = sync_note_hints(st2, min_count=2)
check("空库创建 0 条", r4["created"] == 0, f"{r4['created']}")
st2.con.close()

st.con.close()

# ============ 汇总 ============
print("\n" + "=" * 74)
n_ok = sum(1 for r in results if r["ok"])
print(f"结果：{n_ok}/{len(results)} 通过")
for r in results:
    if not r["ok"]:
        print(f"  ❌ {r['name']}  {r['detail']}")
print("=" * 74)

out = {"experiment": "E-265", "n_pass": n_ok, "n_total": len(results),
       "first_created": r1["created"], "second_created": r2["created"],
       "results": results}
p = os.path.join(_HERE, "results", "e265_todo_hint.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e265_todo_hint.json")

sys.exit(0 if n_ok == len(results) else 1)
