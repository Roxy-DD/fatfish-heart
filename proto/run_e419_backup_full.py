"""E-419 回归：备份必须校验全部用户表（R1 红线）

发现的问题：
  verify_backup 原来只校验 episodes/vectors/meta 三张表，
  但实际有 10 张用户表，未校验的 7 张共 1631 行：
    notes / todos / episodes_history / audit_log /
    environment_events / ep_fts / notes_history

  → 如果 notes 或 todos 丢失，校验仍然报 ok。这是 R1「无损」的实际漏洞。

本测试固化：
  ① all_user_tables 返回全部用户表（排除 FTS 影子表）
  ② verify_backup 默认校验全部表
  ③ **故意删一张表的记录，verify 必须报失败**
     （能发现丢失，才是有效的校验）
"""
import sys
import os
import shutil
import sqlite3
import warnings

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, MEM)

import backup as bk

DB = os.path.join(MEM, "dsh_mem.db")
TMP = os.path.join(_HERE, "results", "_e419_tmp.db")

results = []


def check(name, cond, detail=""):
    results.append({"name": name, "ok": bool(cond), "detail": detail})
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  {detail}" if detail else ""))


print("=" * 74)
print("E-419 备份全表校验回归（R1 红线）")
print("=" * 74)

# ---------- ① all_user_tables ----------
print("\n【1】用户表枚举")

tables = bk.all_user_tables(DB)
print(f"   共 {len(tables)} 张: {tables}")

# 必须包含关键表
KEY_TABLES = ["episodes", "vectors", "meta", "notes", "todos",
              "episodes_history", "audit_log", "environment_events"]
for t in KEY_TABLES:
    check(f"包含 {t}", t in tables)

# 不应包含 FTS 影子表
SHADOW = ["ep_fts_data", "ep_fts_idx", "ep_fts_docsize",
          "ep_fts_config"]
for s in SHADOW:
    check(f"排除影子表 {s}", s not in tables)

# ---------- ② 全表校验通过 ----------
print("\n【2】全表备份+校验")

if os.path.exists(TMP):
    os.remove(TMP)

r = bk.backup_db(DB, TMP, verify=True)
check("备份成功", r.get("ok") is True, str(r.get("error") or ""))

v = bk.verify_backup(DB, TMP)
check("全表校验通过", v.get("ok") is True, str(v.get("error") or ""))
check("校验覆盖全部用户表",
      len(v.get("tables", [])) == len(tables),
      f"{len(v.get('tables', []))} vs {len(tables)}")

# ---------- ③ 关键：能发现丢失 ----------
print("\n【3】关键测试：能发现记录丢失")

# 故意在备份里删掉 notes 表的记录
try:
    c = sqlite3.connect(TMP)
    c.execute("DELETE FROM notes")
    c.commit()
    c.close()

    v2 = bk.verify_backup(DB, TMP)
    check("删除 notes 后校验失败", v2.get("ok") is False)

    notes_check = v2.get("counts", {}).get("notes")
    if notes_check:
        src, bak = notes_check
        check("且能指出是 notes 不一致",
              src != bak, f"源={src} 备份={bak}")
        print(f"      notes: 源 {src} vs 备份 {bak}")

except Exception as e:
    check("删除记录测试", False, f"{type(e).__name__}: {e}")

# 再测一个：删 todos
try:
    c = sqlite3.connect(TMP)
    c.execute("DELETE FROM todos")
    c.commit()
    c.close()

    v3 = bk.verify_backup(DB, TMP)
    check("删除 todos 后校验仍失败", v3.get("ok") is False)
    tc = v3.get("counts", {}).get("todos")
    if tc:
        print(f"      todos: 源 {tc[0]} vs 备份 {tc[1]}")
except Exception as e:
    check("删除 todos 测试", False, str(e))

# ---------- ④ 旧行为对比（证明这个修复有意义）----------
print("\n【4】对比：旧的默认值会漏掉")

# 模拟旧行为：只校验 3 张表
v_old = bk.verify_backup(DB, TMP,
                         tables=("episodes", "vectors", "meta"))
check("旧的三表校验在数据丢的情况下仍报 ok（证明有盲区）",
      v_old.get("ok") is True,
      "这正是 E-419 修的问题")

v_new = bk.verify_backup(DB, TMP)
check("新的全表校验正确报失败",
      v_new.get("ok") is False)

# ---------- 清理 ----------
if os.path.exists(TMP):
    os.remove(TMP)
    print("\n   已删除临时备份")

# ---------- 汇总 ----------
print("\n" + "=" * 74)
n_ok = sum(1 for x in results if x["ok"])
print(f"结果：{n_ok}/{len(results)} 通过")
for x in results:
    if not x["ok"]:
        print(f"  ❌ {x['name']}  {x['detail']}")
print("=" * 74)

print(f"""
  ★ 这个修复对应 R1「无损迁移」：

    原实现的校验只覆盖 3/10 张表。
    如果 notes 或 todos 在迁移中丢失，校验会报 ok ——
    那就不是"无损"，而是"核心三张表无损"。

    R1 要求的是**全部无损**，不是部分。

  ★ 为什么排除 FTS 影子表：
    它们由 FTS5 内部维护，不能按行数比较；
    ep_fts 本身能代表内容，且索引可确定性重建（R3）。
""")

import json
out = {
    "experiment": "E-419",
    "n_pass": n_ok,
    "n_total": len(results),
    "n_tables": len(tables),
    "tables": tables,
    "old_verified": 3,
    "new_verified": len(tables),
}
p = os.path.join(_HERE, "results", "e419_backup_full.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e419_backup_full.json")

sys.exit(0 if n_ok == len(results) else 1)
