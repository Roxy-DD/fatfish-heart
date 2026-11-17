"""E-508 核对 README 里的声称数字是否真实

动机：README 是对外承诺。如果写着「44 项回归」「10 表逐字段」，
但实际不符，那就是虚假宣传 —— 比文笔差严重得多。

用户要求「数字 > 论证」，那 README 里的数字必须能被验证。
"""
import os
import re
import sqlite3
import sys
import json

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

# ---------- E-513：扩展到所有硬编码数字 ----------
#
# 背景：reviewer 审计发现 5 处「同一份 README 内部前后矛盾」，
# 根因不是文笔，是**同一事实在多处手写**。
# 我已把能删的硬编码删掉，留下的必须能被脚本核对。
# 这一节就是那个核对器 —— 从代码/文件系统取实数，不信手写。
print(f"\n【E-513 扩展核对：所有硬编码数字】")

# (a) Tab 数
try:
    _cj = open(os.path.join(ROOT, "dsh-plugin", "client.js"),
               encoding="utf-8").read()
    _i = _cj.find("function renderTab")
    _seg = _cj[_i:_cj.find("function renderList", _i)]
    _tabs = re.findall(r"tab === '([a-z]+)'", _seg)
    n_tabs = len(_tabs)
    for claim in re.findall(r'(\d+)\s*个\s*Tab', readme):
        check(f"README 声称「{claim} 个 Tab」",
              int(claim) == n_tabs, f"实际 {n_tabs} 个")
except Exception as e:
    check("Tab 数核对", False, f"{type(e).__name__}: {e}")

# (b) 种子条数
try:
    _seed = json.load(open(os.path.join(HERE, "cosmos_mem", "seed_memory.json"),
                           encoding="utf-8"))
    n_seed = sum(len(v) for v in _seed.values() if isinstance(v, list))
    for claim in re.findall(r'(\d+)\s*条种子', readme):
        check(f"README 声称「{claim} 条种子」",
              int(claim) == n_seed, f"实际 {n_seed} 条")
except Exception as e:
    check("种子条数核对", False, f"{type(e).__name__}: {e}")

# (c) Python 版本要求（doctor.py 是权威）
try:
    _doc = open(os.path.join(HERE, "doctor.py"), encoding="utf-8").read()
    _m = re.search(r"Python\s*>=\s*(\d+)\.(\d+)", _doc)
    if _m:
        need = f"{_m.group(1)}.{_m.group(2)}+"
        # README 里提到 Python 版本时必须包含这个下限
        for claim in re.findall(r'Python\s*(\d+\.\d+)\+?', readme):
            check(f"README 声称 Python {claim} 不低于 {need}",
                  tuple(map(int, claim.split("."))) >=
                  (int(_m.group(1)), int(_m.group(2))),
                  f"doctor 要求 {need}")
except Exception as e:
    check("Python 版本核对", False, f"{type(e).__name__}: {e}")

# (d) 路由前缀（最严重的一类：照做会 404）
#
# reviewer 发现 README 写 POST /fatfish-heart/seed，实际是 /cosmos-mem。
# 这里从插件源码取真实前缀，核对 README 里所有 /xxx/ 形式的路由。
try:
    _idx = open(os.path.join(ROOT, "dsh-plugin", "index.js"),
                encoding="utf-8").read()
    _hb = re.search(r"httpBase:\s*'([^']+)'", _idx)
    if _hb:
        real_base = _hb.group(1)          # 例如 /cosmos-mem
        # README 里出现的、带 seed|stats|config 后缀的路由
        for m_ in re.finditer(r'(/[a-z-]+)/(seed|stats|config|note|todo|audit)\b',
                              readme):
            prefix = m_.group(1)
            check(f"README 路由前缀 {prefix} 与插件一致",
                  prefix == real_base,
                  f"真实前缀 {real_base}")
except Exception as e:
    check("路由前缀核对", False, f"{type(e).__name__}: {e}")

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
