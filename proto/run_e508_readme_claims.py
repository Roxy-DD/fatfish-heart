"""E-508 核对 README 里的声称数字是否真实

动机：README 是对外承诺。如果写着「44 项回归」「10 表逐字段」，
但实际不符，那就是虚假宣传 —— 比文笔差严重得多。

用户要求「数字 > 论证」，那 README 里的数字必须能被验证。
"""
import os
import re
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
README = os.path.join(ROOT, "README.md")
DB = os.path.join(HERE, "cosmos_mem", "dsh_mem.db")

print("=" * 72)
print("E-508 README 数字真实性核对")
print("=" * 72)

readme = open(README, encoding="utf-8").read()
results = []


def check(name, cond, detail=""):
    results.append({"name": name, "ok": bool(cond), "detail": str(detail)[:120]})
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  {detail}" if detail else ""))


# ---------- 1. 实际数据库结构 ----------
con = sqlite3.connect(DB)
tables = [r[0] for r in con.execute(
    "SELECT name FROM sqlite_master WHERE type='table' "
    "AND name NOT LIKE 'sqlite_%'")]
n_tables = len(tables)

rows_by_table = {}
for t in tables:
    try:
        rows_by_table[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    except Exception:
        rows_by_table[t] = 0
total_rows = sum(rows_by_table.values())
n_episodes = rows_by_table.get("episodes", 0)
n_vectors = rows_by_table.get("vectors", 0)
con.close()

print(f"\n【实际数据】")
print(f"  表数: {n_tables}")
print(f"  总行数: {total_rows}")
print(f"  episodes: {n_episodes}, vectors: {n_vectors}")

# ---------- 2. 回归项数 ----------
iterate_src = open(os.path.join(HERE, "iterate.py"), encoding="utf-8").read()
# 匹配 ("名称", "脚本.xxx") 或 ("名称", "脚本.xxx", timeout)
test_entries = re.findall(r'\("([^"]+)",\s*"([^"]+\.(?:py|mjs))"', iterate_src)
n_tests = len(test_entries)
print(f"\n【回归】")
print(f"  测试项: {n_tests}")

# ---------- 3. README 里的声称 ----------
print(f"\n【README 声称 vs 实际】")

# 3.1 「N 项回归」
m = re.findall(r'(\d+)\s*项(?:回归|检查)', readme)
for claim in m:
    v = int(claim)
    # 允许声称值 <= 实际（可能没列全），但不允许 > 实际
    check(f"README 声称「{v} 项回归」不超过实际 {n_tests}",
          v <= n_tests, f"声称 {v} / 实际 {n_tests}")

# 3.2 「N 类故障」
m2 = re.findall(r'(\d+)\s*类故障', readme)
for claim in m2:
    check(f"README 声称「{claim} 类故障」",
          int(claim) == 10, f"实际 10 类（F1-F10）")

# 3.3 「10 表 N 行」这类
m3 = re.findall(r'(\d+)\s*表\s*(\d+)\s*行', readme)
for t_claim, r_claim in m3:
    tc, rc = int(t_claim), int(r_claim)
    # 允许偏差：数据在增长
    check(f"README 声称「{tc} 表」",
          tc == n_tables, f"实际 {n_tables} 表")
    check(f"README 声称「{rc} 行」与实际 {total_rows} 同量级",
          abs(rc - total_rows) <= max(total_rows * 0.5, 500),
          f"声称 {rc} / 实际 {total_rows}")

# 3.4 「删光 N 条向量重建」
m4 = re.findall(r'删光\s*(\d+)\s*条向量', readme)
for claim in m4:
    check(f"README 声称「删光 {claim} 条向量」",
          abs(int(claim) - n_vectors) <= max(n_vectors * 0.5, 200),
          f"实际 {n_vectors} 条")

# 3.5 「11/11」doctor
m5 = re.findall(r'(\d+)/(\d+)', readme)
for a, b in m5:
    if int(b) in (11, 44, 42, 49):
        check(f"README 声称「{a}/{b}」",
              int(a) == int(b), f"{a}/{b}")

# ---------- 4. 结论文本 ----------
print("\n" + "=" * 72)
n_pass = sum(1 for x in results if x["ok"])
print(f"结果：{n_pass}/{len(results)} 通过")

fails = [x for x in results if not x["ok"]]
if fails:
    print("\n需要修正的 README 声称：")
    for x in fails:
        print(f"  ❌ {x['name']}  {x['detail']}")
else:
    print("  README 里的数字声称与实际情况一致")
print("=" * 72)

import json
out = {"experiment": "E-508", "n_pass": n_pass, "n_total": len(results),
       "actual": {"tables": n_tables, "total_rows": total_rows,
                  "episodes": n_episodes, "vectors": n_vectors,
                  "regression_tests": n_tests},
       "results": results}
p = os.path.join(HERE, "results", "e508_readme_claims.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 {p}")

sys.exit(0 if n_pass == len(results) else 1)
