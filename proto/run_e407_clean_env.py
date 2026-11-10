"""E-407 清理残留的 2 条测试环境事件

E-403 发现 environment_events 里有 2 条测试数据：
  [c2964319] test_pass | E-399 end-to-end | task=E-399
  [bb16fe80] test_pass | mem_environment 工具自检 | task=E-402

这两条是我验证工具时写的，不是真实环境反馈。

★ 清理方式选择：
  environment_events 表没有 invalid_at 字段（它是观测日志，不是记忆）。
  对观测日志来说，"删掉"和"标记"哪个对？

  思考：
    · R2「不遗忘」是针对**记忆**的红线，不是观测日志
    · 观测日志的价值在于真实性——留着假数据反而破坏它
    · 但直接删也不可追溯

  折中：
    · 加 invalid_at 字段（与其他表一致）
    · 标记而非删除
    · 查询默认过滤掉已失效的
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

print("=" * 74)
print("E-407 清理测试环境事件")
print("=" * 74)

con = sqlite3.connect(DB)

# ---------- ① 先看当前表结构 ----------
print("\n① 当前表结构")
cols = con.execute("PRAGMA table_info(environment_events)").fetchall()
print(f"   字段: {[c[1] for c in cols]}")

has_invalid = any(c[1] == "invalid_at" for c in cols)

# ---------- ② 加 invalid_at 字段（如果没有）----------
if not has_invalid:
    print("\n② 加 invalid_at 字段（标记而不删除）")
    try:
        con.execute(
            "ALTER TABLE environment_events ADD COLUMN invalid_at REAL")
        con.commit()
        print("   ✅ 已添加")
    except Exception as e:
        print(f"   ❌ 失败: {e}")
else:
    print("\n② invalid_at 字段已存在")

# ---------- ③ 标记测试数据 ----------
print("\n③ 标记测试数据为失效")

TEST_SOURCES = [
    "E-399 end-to-end",
    "mem_environment 工具自检",
]

marked = 0
for src in TEST_SOURCES:
    rows = con.execute(
        "SELECT event_id, kind, source, task FROM environment_events "
        "WHERE source = ? AND invalid_at IS NULL", (src,)).fetchall()
    for eid, kind, source, task in rows:
        con.execute(
            "UPDATE environment_events SET invalid_at = ? "
            "WHERE event_id = ?", (time.time(), eid))
        print(f"   标记 [{eid[:8]}] {kind} | {source} | task={task}")
        marked += 1

con.commit()
print(f"\n   共标记 {marked} 条")

# ---------- ④ 验证 ----------
print("\n④ 验证清理结果")

total = con.execute(
    "SELECT COUNT(*) FROM environment_events").fetchone()[0]
valid = con.execute(
    "SELECT COUNT(*) FROM environment_events "
    "WHERE invalid_at IS NULL").fetchone()[0]
invalid = total - valid

print(f"   总行数: {total}（未物理删除，遵守 R2 精神）")
print(f"   有效: {valid}")
print(f"   已标记失效: {invalid}")

# 看剩余的有效事件
print(f"\n   剩余有效事件:")
rows = con.execute(
    "SELECT event_id, kind, source, outcome, task "
    "FROM environment_events WHERE invalid_at IS NULL "
    "ORDER BY at DESC LIMIT 10").fetchall()
for eid, kind, src, out, task in rows:
    print(f"     [{eid[:8]}] {kind} | {str(src)[:40]} | {out}")

con.close()

# ---------- 结论 ----------
print("\n" + "=" * 74)
print("结论")
print("=" * 74)

print(f"""
  标记了 {marked} 条测试事件
  剩余有效: {valid} 条（都是真实环境反馈）

  ★ 为什么加 invalid_at 而不是直接删：
    · 与 episodes/notes 的设计一致（R4 可塑性：标记不删除）
    · 观测日志虽然性质不同，但"可追溯"这个原则通用
    · 以后如果想审计"哪些数据被清理过"还能查到

  ★ 但要注意一个区别：
    episodes 的 R2「不遗忘」是记忆红线，
    environment_events 是观测日志，没有同样的约束。
    加 invalid_at 是为了可追溯，不是为了遵守 R2。
    （R2 只约束记忆，不该被滥用到所有表）
""")

out = {
    "experiment": "E-407",
    "marked": marked,
    "total": total,
    "valid": valid,
    "invalid": invalid,
}
p = os.path.join(_HERE, "results", "e407_clean_env.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e407_clean_env.json")
