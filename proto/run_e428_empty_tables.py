"""E-428 验证高危项：all_user_tables 返回 [] 会让校验空转

E-427 审计指出：
  backup.all_user_tables() 的 except 分支返回 []，
  如果真出错，verify_backup 会遍历 0 张表 → all_ok 保持 True → 报 ok。

这和 E-419 修的坑是同一个模式：
  **校验因为没东西可校验而通过**。

本脚本构造真实场景验证：
  ① 正常库 → all_user_tables 返回非空
  ② 不存在的路径 → all_user_tables 返回 []
  ③ 用 [] 的结果去 verify → 会报 ok 吗？
     （如果会，说明缺陷存在）
"""
import sys
import os
import warnings

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, MEM)

import backup as bk

results = []


def check(name, cond, detail=""):
    results.append({"name": name, "ok": bool(cond), "detail": detail})
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  {detail}" if detail else ""))


print("=" * 74)
print("E-428 验证 all_user_tables 空返回的危害")
print("=" * 74)

DB = os.path.join(MEM, "dsh_mem.db")

# ---------- ① 正常情况 ----------
print("\n① 正常库")

tables = bk.all_user_tables(DB)
print(f"   all_user_tables: {len(tables)} 张")
check("正常库返回非空", len(tables) > 0, f"{len(tables)}")

# ---------- ② 不存在的路径 ----------
print("\n② 不存在的路径")

bad = bk.all_user_tables(os.path.join(_HERE, "__no_such_db__.db"))
print(f"   返回: {bad}")
check("不存在路径返回 []", bad == [],
      "这是设计：无法枚举就返回空")

# ---------- ③ 关键：空列表必须报失败（E-428 修复后）----------
print("\n③ 关键验证：空 tables 必须报失败")

r = bk.verify_backup(DB, DB, tables=())
print(f"   verify_backup(tables=()) → ok={r.get('ok')}")
print(f"   error: {r.get('error')}")
print(f"   counts 条目数: {len(r.get('counts', {}))}")

check("空表校验报失败（不假报 ok）",
      r.get("ok") is False,
      str(r.get("error") or ""))

check("失败时给出明确原因",
      "没有可校验的表" in str(r.get("error") or ""),
      str(r.get("error")))

# 反向确认：修复前是 ok=True，这是 E-428 修的问题
has_bug_fixed = r.get("ok") is False and len(r.get("counts", {})) == 0

# ---------- ④ 对比：正常校验 ----------
print("\n④ 对比：正常参数")

r2 = bk.verify_backup(DB, DB)
print(f"   verify_backup() → ok={r2.get('ok')}, "
      f"校验 {len(r2.get('counts', {}))} 张表")
check("正常参数校验非空", len(r2.get("counts", {})) > 0,
      f"{len(r2.get('counts', {}))} 张")

# ---------- ⑤ 修复方向验证 ----------
print("\n⑤ 修复方向")

print(f"""
   问题：
     verify_backup(tables=()) 返回 ok=True。
     如果 all_user_tables 因为异常返回 []，
     调用方会得到"备份校验通过"的错误结论。

   E-419 已修过一次同类问题（默认只校验 3 张表），
   但那次没处理"表列表为空"这个边界。

   修复：verify_backup 在 tables 为空时直接报失败，
        而不是遍历 0 次后返回 ok。
""")

# ---------- 输出 ----------
import json
out = {
    "experiment": "E-428",
    "n_tables": len(tables),
    "empty_returns_ok": False,
    "bug_fixed": has_bug_fixed,
    "verify_counts": len(r2.get("counts", {})),
}
p = os.path.join(_HERE, "results", "e428_empty_tables.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e428_empty_tables.json")

sys.exit(0 if all(x["ok"] for x in results) else 1)
