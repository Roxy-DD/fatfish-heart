"""E-455 统一修正路径层级：一律上溯到项目根

E-453/454 的错误：
  我按"文件所在目录层级"机械上溯，结果：
    proto/xxx.py          → 上溯 1 级 → proto/  ❌ 应该到项目根
    proto/cosmos_mem/xx.py → 上溯 2 级 → proto/  ❌ 应该到项目根

  原始路径全部指向**项目根**下的东西：
    C:\\...\\肥鱼之心\\proto\\cosmos_mem
    C:\\...\\肥鱼之心\\models\\bge-small-zh
    C:\\...\\肥鱼之心\\.hf

  所以统一规则：
    从 __file__ 上溯到**项目根**，然后拼原子路径。

  项目根的判定：
    proto/xxx.py            → 上 1 级是 proto/，再上 1 级是根 → 共 2 级
    proto/cosmos_mem/xx.py  → 上 1 级 cosmos_mem，2 级 proto，3 级根 → 共 3 级

  通用公式：上溯级数 = 文件路径中的 '/' 数量 + 1
    proto/xxx.py           → 1 + 1 = 2
    proto/cosmos_mem/xx.py → 2 + 1 = 3
"""
import os
import re
import json
import subprocess

ROOT = r"C:\Users\xuee\Desktop\肥鱼之心"

print("=" * 74)
print("E-455 统一修正路径层级")
print("=" * 74)

r = subprocess.run(["git", "ls-files"], cwd=ROOT,
                   capture_output=True, text=True, encoding="utf-8")
files = [f for f in r.stdout.split("\n") if f.strip()]

# 找形如 os.path.dirname(...os.path.abspath(__file__)...) 的表达式
# 包含可能多层嵌套
EXPR_PAT = re.compile(
    r"os\.path\.dirname\((?:\s*os\.path\.dirname\()*\s*"
    r"os\.path\.abspath\(__file__\)\s*\)*")

fixed = []
for f in files:
    if not f.endswith(".py"):
        continue
    p = os.path.join(ROOT, f)
    if not os.path.exists(p):
        continue
    try:
        text = open(p, encoding="utf-8", errors="replace").read()
    except Exception:
        continue
    if "abspath(__file__)" not in text:
        continue

    # 该文件正确的上溯级数
    correct_ups = f.count("/") + 1
    correct_expr = ("os.path.dirname(" * correct_ups
                    + "os.path.abspath(__file__)"
                    + ")" * correct_ups)

    def repl(m):
        return correct_expr

    new_text = EXPR_PAT.sub(repl, text)
    if new_text != text:
        open(p, "w", encoding="utf-8", newline="\n").write(new_text)
        fixed.append(f)

print(f"修正 {len(fixed)} 个文件")
for f in fixed[:10]:
    print(f"  {f}")

# ---------- 验证：所有路径拼接是否指向存在的位置 ----------
print(f"\n{'='*74}")
print("验证实际指向")
print("=" * 74)

bad = []
for f in files:
    if not f.endswith(".py"):
        continue
    p = os.path.join(ROOT, f)
    if not os.path.exists(p):
        continue
    try:
        text = open(p, encoding="utf-8", errors="replace").read()
    except Exception:
        continue

    # 检查 os.path.join(<expr>, "seg", ...) 形式
    for m in re.finditer(
            r'os\.path\.join\((?:\s*os\.path\.dirname\()+\s*'
            r'os\.path\.abspath\(__file__\)\s*\)*'
            r'\s*,\s*"([^"]+)"', text):
        seg = m.group(1)
        line = text[:m.start()].count("\n") + 1
        # 算目标
        filedir = os.path.dirname(os.path.join(ROOT, f))
        ups = f.count("/") + 1
        tgt = filedir
        for _ in range(ups):
            tgt = os.path.dirname(tgt)
        cand = os.path.join(tgt, seg)
        if not os.path.exists(cand):
            bad.append({"file": f, "line": line, "seg": seg,
                        "candidate": cand})

print(f"指向不存在的: {len(bad)}")
for it in bad[:15]:
    print(f"  {it['file']}:{it['line']}  '{it['seg']}' → 不存在")

# ---------- 语法检查 ----------
import ast
syn = []
for f in files:
    if not f.endswith(".py"):
        continue
    p = os.path.join(ROOT, f)
    if not os.path.exists(p):
        continue
    try:
        ast.parse(open(p, encoding="utf-8", errors="replace").read())
    except SyntaxError as e:
        syn.append((f, e.lineno, e.msg))
print(f"\n语法错误: {len(syn)}")
for f, l, m in syn[:5]:
    print(f"  {f}:{l} {m}")

out = {
    "experiment": "E-455",
    "n_fixed": len(fixed),
    "n_bad_paths": len(bad),
    "bad": bad[:20],
    "n_syntax": len(syn),
}
p = os.path.join(ROOT, "proto", "results", "e455_fix_levels.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 {p}")
