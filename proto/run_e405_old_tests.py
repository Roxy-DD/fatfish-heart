"""E-405 找出真正的旧测试记忆（早期实验残留）

E-404 发现 audit_log 里有：
  seq=55  写入 测试服务器的登录密码是 8888
  seq=56  写入 测试服务器的登录密码改成了 9999
  seq=67  新建随笔 测试笔记
  seq=68  升层到笔记 测试笔记

这些是**早期测试**（11-07 左右）写入的，可能还在有效记忆里。
E-403 没查出来是因为我的标记只覆盖了最近几轮实验。

必须查：这些旧测试数据还在不在？如果在，是污染。
"""
import sys
import os
import json
import warnings
import sqlite3

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
DB = os.path.join(MEM, "dsh_mem.db")

print("=" * 78)
print("E-405 查早期测试记忆")
print("=" * 78)

con = sqlite3.connect(DB)

# ---------- ① 找 episodes 里的旧测试 ----------
print("\n① episodes 里的旧测试内容")

PATTERNS = [
    ("测试服务器的登录密码", "E-22b 集成测试的假密码"),
    ("测试密码是", "测试假密码"),
    ("测试笔记", "测试笔记"),
    ("登录密码是 8848", "集成测试数据"),
    ("登录密码改成 9999", "集成测试数据"),
    ("项目A做完了", "集成测试数据"),
]

found = []
for pat, desc in PATTERNS:
    rows = con.execute(
        "SELECT ep_id, raw_text, invalid_at, created_at FROM episodes "
        "WHERE raw_text LIKE ?", (f"%{pat}%",)).fetchall()
    for eid, txt, inv, cat in rows:
        found.append({
            "ep_id": eid, "text": txt[:80], "invalid": inv is not None,
            "created_at": cat, "pattern": pat, "desc": desc,
        })

print(f"   找到 {len(found)} 条")
import time
for f in found:
    t = time.strftime("%m-%d %H:%M", time.localtime(f["created_at"])) \
        if f["created_at"] else "?"
    mark = "已失效" if f["invalid"] else "⚠ 仍有效"
    print(f"     [{f['ep_id'][:8]}] {t} {mark}")
    print(f"       匹配: {f['pattern']}（{f['desc']}）")
    print(f"       {f['text']}")

# ---------- ② 找 notes 里的测试 ----------
print("\n② notes 里的测试条目")

notes = con.execute(
    "SELECT note_id, title, tier, invalid_at FROM notes").fetchall()
note_test = []
for nid, title, tier, inv in notes:
    if "测试" in (title or ""):
        note_test.append({"note_id": nid, "title": title,
                          "tier": tier, "invalid": inv is not None})
        print(f"     [{nid[:8]}] [{tier}] {title} "
              f"{'已失效' if inv else '⚠ 仍有效'}")

# ---------- ③ 找 todos 里的测试 ----------
print("\n③ todos 里的测试条目")

todos = con.execute(
    "SELECT todo_id, title, status, invalid_at FROM todos").fetchall()
todo_test = []
for tid, title, status, inv in todos:
    if "测试" in (title or ""):
        todo_test.append({"todo_id": tid, "title": title,
                          "status": status,
                          "invalid": inv is not None})
        print(f"     [{tid[:8]}] [{status}] {title} "
              f"{'已失效' if inv else '⚠ 仍有效'}")

# ---------- ④ 统计 ----------
print("\n④ 统计")

ep_valid_test = [f for f in found if not f["invalid"]]
print(f"   episodes 旧测试: {len(found)} 条 "
      f"（其中仍有效 {len(ep_valid_test)} 条）")
print(f"   notes 旧测试:    {len(note_test)} 条 "
      f"（仍有效 {sum(1 for n in note_test if not n['invalid'])} 条）")
print(f"   todos 旧测试:    {len(todo_test)} 条 "
      f"（仍有效 {sum(1 for t in todo_test if not t['invalid'])} 条）")

con.close()

# ---------- 结论 ----------
print("\n" + "=" * 78)
print("结论")
print("=" * 78)

total_valid = (len(ep_valid_test) +
               sum(1 for n in note_test if not n["invalid"]) +
               sum(1 for t in todo_test if not t["invalid"]))

print(f"""
  仍有效的旧测试数据: {total_valid} 条

  {"✅ 都已被历史清理（失效）" if total_valid == 0 else "⚠ 有残留，应清理"}
""")

if total_valid > 0:
    print("  清理方式（遵守 R2 不物理删除）：")
    print("    · 标 invalid_at，不删行")
    print("    · 加 note 说明是测试数据")

out = {
    "experiment": "E-405",
    "episodes_found": len(found),
    "episodes_valid": len(ep_valid_test),
    "notes_found": len(note_test),
    "notes_valid": sum(1 for n in note_test if not n["invalid"]),
    "todos_found": len(todo_test),
    "todos_valid": sum(1 for t in todo_test if not t["invalid"]),
    "total_valid": total_valid,
    "details": found[:20],
}
p = os.path.join(_HERE, "results", "e405_old_tests.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e405_old_tests.json")
