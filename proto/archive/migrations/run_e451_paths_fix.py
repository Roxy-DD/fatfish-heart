"""E-451 批量修复个人绝对路径

策略（按文件类型）：

  A. proto/*.py 脚本（run_e*.py 等）
     这些脚本本来就在项目里跑，用 os.path 相对推导最稳。
     把 C:\\Users\\xuee\\Desktop\\肥鱼之心 替换成
       os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
     （proto/ 的上一级 = 项目根）

  B. proto/cosmos_mem/*.py
     同上，但层级不同（再上一层）

  C. README.md / docs/*.md
     改成占位符 <项目路径>，因为是给人看的示例

  D. dsh-plugin/*.js
     检查上下文：如果是注释示例 → 占位符；如果是运行时 → 用 __dirname

  E. proto/results/*.json
     这些是历史实验记录，含绝对路径是"当时的真实记录"。
     不修改（改了等于篡改历史数据），但在 README 里说明。
"""
import os
import re
import json
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

print("=" * 74)
print("E-451 批量修复绝对路径")
print("=" * 74)

r = subprocess.run(["git", "ls-files"], cwd=ROOT,
                   capture_output=True, text=True, encoding="utf-8")
files = [f for f in r.stdout.split("\n") if f.strip()]

stats = {"py_proto": 0, "py_mem": 0, "md": 0, "js": 0,
         "json_skip": 0, "other": 0}
changed = []

for f in files:
    p = os.path.join(ROOT, f)
    if not os.path.exists(p):
        continue
    if not f.endswith((".py", ".js", ".md", ".mjs")):
        if f.endswith(".json"):
            try:
                c = open(p, encoding="utf-8", errors="replace").read()
                if PB in c or PB.replace("\\", "\\\\") in c:
                    stats["json_skip"] += 1
            except Exception:
                pass
        continue

    try:
        c = open(p, encoding="utf-8", errors="replace").read()
    except Exception:
        continue
    if PB not in c and PB.replace("\\", "\\\\") not in c:
        continue

    orig = c

    if f.startswith("proto/") and f.endswith(".py"):
        depth = f.count("/")          # proto/xxx.py → 1；proto/a/b.py → 2
        ups = "os.path.dirname(" * (depth - 1)
        expr = (ups + 'os.path.abspath(__file__)'
                + ")" * (depth - 1))
        # 替换硬编码为相对推导
        for pat in (PB.replace("\\", "\\\\"), PB):
            c = c.replace(f'r"{pat}"', expr)
            c = c.replace(f"'{pat}'", expr)
            c = c.replace(f'"{pat}"', expr)
        if c != orig:
            if "/cosmos_mem/" in f:
                stats["py_mem"] += 1
            else:
                stats["py_proto"] += 1

    elif f.endswith((".md", ".mjs")) or f.endswith(".js"):
        for pat in (PB.replace("\\", "\\\\"), PB):
            c = c.replace(pat, "<项目路径>")
        if c != orig:
            if f.endswith(".md"):
                stats["md"] += 1
            else:
                stats["js"] += 1

    if c != orig:
        open(p, "w", encoding="utf-8", newline="\n").write(c)
        changed.append(f)

print(f"\n修改统计：")
for k, v in stats.items():
    print(f"  {k:<12} {v}")
print(f"\n总修改文件: {len(changed)}")

# 验证：再扫一遍还剩多少
print(f"\n{'='*74}")
print("复查剩余绝对路径")
print("=" * 74)

remain = []
for f in files:
    if not f.endswith((".py", ".js", ".md", ".mjs", ".html")):
        continue
    p = os.path.join(ROOT, f)
    if not os.path.exists(p):
        continue
    try:
        c = open(p, encoding="utf-8", errors="replace").read()
    except Exception:
        continue
    if PB in c:
        n = c.count(PB)
        remain.append((f, n))

print(f"仍含绝对路径: {len(remain)} 个文件")
for f, n in remain[:15]:
    print(f"  {n:>3}  {f}")

out = {
    "experiment": "E-451",
    "stats": stats,
    "n_changed": len(changed),
    "n_remaining": len(remain),
    "remaining": [{"file": f, "count": n} for f, n in remain],
}
p = os.path.join(ROOT, "proto", "results", "e451_paths_fix.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 {p}")
