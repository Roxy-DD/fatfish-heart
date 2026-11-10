"""E-403 审计：我的实验是否污染了生产库

背景：
  这些天的实验大量直接操作生产库 proto/cosmos_mem/dsh_mem.db。
  比如 E-399 往 environment_events 写了 __e399_test__，
  E-391 往 audit_log 写了 __e391_test__。

虽然每次都在脚本里清理，但**没有系统性验证**。
如果清理漏了，测试数据会被当成真实记忆。

本审计查：
  ① episodes 里有没有测试文本
  ② audit_log 里有没有测试 sid
  ③ environment_events 里有没有测试 source
  ④ notes / todos 里有没有测试条目
"""
import sys
import os
import re
import json
import warnings
import sqlite3

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
DB = os.path.join(MEM, "dsh_mem.db")

print("=" * 78)
print("E-403 生产库污染审计")
print("=" * 78)

con = sqlite3.connect(DB)

# 测试标记（我实验里用过的）
TEST_MARKERS = [
    "__e3", "__e4", "e399_test", "e391_test", "e390",
    "测试记忆", "test_db", "E-391 测试", "E-399 end-to-end",
    "验证链路", "验证修复逻辑", "验证注入修复",
]

# ---------- ① episodes ----------
print("\n① episodes 里的测试文本")

rows = con.execute(
    "SELECT ep_id, raw_text, invalid_at FROM episodes").fetchall()
print(f"   总行数: {len(rows)}")

suspicious = []
for eid, txt, inv in rows:
    for m in TEST_MARKERS:
        if m in (txt or ""):
            suspicious.append((eid, txt[:70], m, inv is not None))
            break

print(f"   疑似测试数据: {len(suspicious)} 条")
for eid, txt, m, is_inv in suspicious:
    print(f"     [{eid[:8]}] 匹配'{m}' {'(已失效)' if is_inv else '(有效!)'}")
    print(f"       {txt}")

# ---------- ② audit_log ----------
print("\n② audit_log 里的测试记录")

test_sids = con.execute(
    "SELECT sid, COUNT(*) FROM audit_log WHERE sid LIKE '%__e%' "
    "OR sid LIKE '%test%' GROUP BY sid").fetchall()
print(f"   测试 sid: {len(test_sids)} 个")
for sid, n in test_sids:
    print(f"     {sid}: {n} 条")

test_msgs = con.execute(
    "SELECT COUNT(*) FROM audit_log WHERE msg LIKE '%测试%' "
    "OR msg LIKE '%__e%'").fetchone()[0]
print(f"   含测试字样的 msg: {test_msgs} 条")

# ---------- ③ environment_events ----------
print("\n③ environment_events 里的测试数据")

try:
    env_rows = con.execute(
        "SELECT event_id, kind, source, task FROM environment_events"
    ).fetchall()
    print(f"   总行数: {len(env_rows)}")

    env_test = [r for r in env_rows
                if any(m in str(r[2]) for m in TEST_MARKERS)
                or str(r[3]) in ("E-391", "E-399", "E-402")]
    print(f"   疑似测试: {len(env_test)} 条")
    for eid, kind, src, task in env_test:
        print(f"     [{eid[:8]}] {kind} | {src[:40]} | task={task}")
except Exception as e:
    print(f"   表不存在或读取失败: {e}")
    env_rows = []
    env_test = []

# ---------- ④ notes / todos ----------
print("\n④ notes / todos 里的测试条目")

notes = con.execute(
    "SELECT note_id, title FROM notes WHERE invalid_at IS NULL").fetchall()
note_test = [n for n in notes
             if any(m in n[1] for m in TEST_MARKERS)]
print(f"   notes: {len(notes)} 条，疑似测试 {len(note_test)} 条")
for nid, title in note_test:
    print(f"     [{nid[:8]}] {title}")

todos = con.execute(
    "SELECT todo_id, title FROM todos WHERE invalid_at IS NULL").fetchall()
todo_test = [t for t in todos
             if any(m in t[1] for m in TEST_MARKERS)]
print(f"   todos: {len(todos)} 条，疑似测试 {len(todo_test)} 条")
for tid, title in todo_test:
    print(f"     [{tid[:8]}] {title}")

con.close()

# ---------- 结论 ----------
print("\n" + "=" * 78)
print("审计结论")
print("=" * 78)

total_pollution = (len(suspicious) + len(test_sids) +
                   len(env_test) + len(note_test) + len(todo_test))

print(f"""
  episodes 疑似测试:        {len(suspicious)} 条
  audit_log 测试 sid:       {len(test_sids)} 个
  environment_events 测试:  {len(env_test)} 条
  notes 测试:               {len(note_test)} 条
  todos 测试:               {len(todo_test)} 条
  ─────────────────────────────────
  合计: {total_pollution}

  {"✅ 没有污染" if total_pollution == 0 else "⚠ 需要清理"}
""")

out = {
    "experiment": "E-403",
    "episodes_total": len(rows),
    "episodes_suspicious": len(suspicious),
    "audit_test_sids": len(test_sids),
    "env_total": len(env_rows),
    "env_test": len(env_test),
    "notes_test": len(note_test),
    "todos_test": len(todo_test),
    "total_pollution": total_pollution,
    "details": {
        "episodes": [{"ep_id": e, "text": t[:60], "marker": m,
                      "invalid": i} for e, t, m, i in suspicious],
        "env": [{"event_id": e, "kind": k, "source": s[:50],
                 "task": tk} for e, k, s, tk in env_test],
    },
}
p = os.path.join(_HERE, "results", "e403_pollution_audit.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e403_pollution_audit.json")
