"""E-462 修正 E-461 的元组 bug（漏了 os.path.join）

E-461 的替换产生了：
  MODEL_DIR = os.path.dirname(os.path.dirname(os.path.dirname(
      os.path.abspath(__file__)))), "models", "bge-small-zh"
  → 这是**元组**，不是路径！

实测报错：
  TypeError: path should be string..., not tuple

★ 根因：我的替换模板是 ROOT_EXPR + ', "models", "bge-small-zh"'
  但忘了外面套 os.path.join(...)。

修法：精确匹配这个错误模式，替换为带 join 的正确形式。
每个文件改完立刻 ast.parse。
"""
import os
import re
import ast
import json

ROOT = r"C:\Users\xuee\Desktop\肥鱼之心"
CORE = os.path.join(ROOT, "proto", "cosmos_mem")

print("=" * 74)
print("E-462 修正元组 bug")
print("=" * 74)

# 匹配错误形式：<expr>, "seg1", "seg2"
# 其中 expr 是 os.path.dirname(...) 嵌套
BAD = re.compile(
    r'(?<!\()'                       # 前面不是 (
    r'(os\.path\.dirname\((?:os\.path\.dirname\()*os\.path\.abspath\(__file__\)\)*\))'
    r'\s*,\s*'                       # 逗号
    r'((?:"[^"]+"\s*,\s*)*"[^"]+")'  # 一个或多个 "seg"
)

targets = []
for fn in os.listdir(CORE):
    if fn.endswith(".py"):
        targets.append(os.path.join(CORE, fn))
for sub in ("archive_legacy",):
    d = os.path.join(CORE, sub)
    if os.path.isdir(d):
        for fn in os.listdir(d):
            if fn.endswith(".py"):
                targets.append(os.path.join(d, fn))

changed = []
failed = []

for p in targets:
    raw = open(p, "rb").read()
    text = None
    for enc in ("utf-8", "gbk"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        continue

    orig = text

    def repl(m):
        expr = m.group(1)
        segs = m.group(2)
        return f"os.path.join({expr}, {segs})"

    # 只替换那些确实在赋值左侧是 MODEL_DIR 等路径变量的
    lines = text.split("\n")
    out_lines = []
    for line in lines:
        if ("MODEL_DIR" in line or "DEFAULT_MODEL" in line
                or "MODEL_PATH" in line) and BAD.search(line):
            line = BAD.sub(repl, line, count=1)
        out_lines.append(line)
    text = "\n".join(out_lines)

    if text == orig:
        continue

    try:
        ast.parse(text)
    except SyntaxError as e:
        failed.append((os.path.basename(p), e.lineno, e.msg))
        print(f"  ❌ {os.path.basename(p)}: {e.msg}")
        continue

    open(p, "wb").write(text.encode(enc))
    changed.append(os.path.basename(p))
    print(f"  ✅ {os.path.basename(p)}")

print(f"\n修改 {len(changed)} 个，失败 {len(failed)} 个")

# ---------- 验证 ----------
print(f"\n{'='*74}")
print("验证")
print("=" * 74)

syn = 0
for p in targets:
    try:
        ast.parse(open(p, encoding="utf-8", errors="replace").read())
    except SyntaxError:
        syn += 1
print(f"语法错误: {syn}")

# 检查剩余元组模式
remain = []
for p in targets:
    c = open(p, encoding="utf-8", errors="replace").read()
    for i, line in enumerate(c.split("\n"), 1):
        if ("MODEL_DIR" in line or "DEFAULT_MODEL" in line):
            if re.search(r'\)\)\),\s*"', line):
                remain.append((os.path.basename(p), i, line.strip()[:80]))
print(f"剩余元组模式: {len(remain)}")
for f, i, l in remain:
    print(f"  {f}:{i} {l}")

# 实测
import subprocess
test = subprocess.run(
    ["python", "-c",
     "import sys, warnings, os; warnings.filterwarnings('ignore'); "
     f"sys.path.insert(0, r'{CORE}'); "
     "import store_v1; "
     "print('MODEL_DIR =', store_v1.MODEL_DIR); "
     "print('exists:', os.path.exists(store_v1.MODEL_DIR))"],
    cwd=ROOT, capture_output=True, text=True,
    encoding="utf-8", errors="replace")
print(f"\n实测:")
print((test.stdout or "").strip()[-300:])
if test.returncode != 0:
    print("stderr:", (test.stderr or "")[-300:])

out = {
    "experiment": "E-462",
    "changed": changed,
    "failed": failed,
    "n_syntax": syn,
    "remain_tuple": len(remain),
    "import_ok": test.returncode == 0,
}
p = os.path.join(ROOT, "proto", "results", "e462_fix_tuple.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 {p}")
