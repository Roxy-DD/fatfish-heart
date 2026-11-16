"""E-453 修正替换残留：r"os.path.dirname(...)" 变成字符串字面量

E-452 的问题：
  原文    MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models", "bge-small-zh")
  替换后  MODEL_DIR = r"os.path.dirname(os.path.abspath(__file__))"

  ★ 多了 r"..."，表达式成了字符串！

  实测：MODEL_DIR = 'os.path.dirname(...)'  （字符串）
       exists: False

根因：我的正则只匹配了路径本身，没处理前后的 r" 和 "。

修法：
  匹配完整的字符串字面量形式：
    r"C:\\Users\\xuee\\Desktop\\..."  →  表达式
    "C:\\Users\\xuee\\Desktop\\..."   →  表达式
  连前缀和后引号一起替换。
"""
import os
import re
import json
import subprocess
import ast

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

print("=" * 74)
print("E-453 修正 r\"...\" 残留")
print("=" * 74)

# 找出所有被误替换成字符串的
BAD_PAT = re.compile(
    r'r"(os\.path\.dirname\((?:os\.path\.abspath\(__file__\))?\)+)"'
    r'|"(os\.path\.dirname\((?:os\.path\.abspath\(__file__\))?\)+)"'
)

r = subprocess.run(["git", "ls-files"], cwd=ROOT,
                   capture_output=True, text=True, encoding="utf-8")
files = [f for f in r.stdout.split("\n") if f.strip()]

fixed = []
for f in files:
    if not f.endswith((".py", ".js", ".mjs")):
        continue
    p = os.path.join(ROOT, f)
    if not os.path.exists(p):
        continue
    raw = open(p, "rb").read()
    try:
        text = raw.decode("utf-8")
        enc = "utf-8"
    except UnicodeDecodeError:
        try:
            text = raw.decode("gbk")
            enc = "gbk"
        except UnicodeDecodeError:
            continue

    if not BAD_PAT.search(text):
        continue

    new_text = BAD_PAT.sub(lambda m: m.group(1) or m.group(2), text)
    if new_text != text:
        open(p, "wb").write(new_text.encode(enc))
        fixed.append(f)

print(f"修正 {len(fixed)} 个文件")
for f in fixed[:10]:
    print(f"  {f}")

# 复查是否还有残留
print(f"\n{'='*74}")
print("复查残留")
print("=" * 74)
remain = []
for f in files:
    if not f.endswith((".py", ".js", ".mjs")):
        continue
    p = os.path.join(ROOT, f)
    if not os.path.exists(p):
        continue
    try:
        text = open(p, encoding="utf-8", errors="replace").read()
    except Exception:
        continue
    if BAD_PAT.search(text):
        remain.append(f)
print(f"仍残留: {len(remain)} 个")

# 语法检查
print(f"\n{'='*74}")
print("Python 语法检查")
print("=" * 74)
bad = []
for f in fixed:
    if not f.endswith(".py"):
        continue
    p = os.path.join(ROOT, f)
    try:
        ast.parse(open(p, encoding="utf-8", errors="replace").read())
    except Exception as e:
        bad.append((f, str(e)[:100]))
print(f"语法错误: {len(bad)} 个")
for f, e in bad[:10]:
    print(f"  ❌ {f}: {e}")

out = {
    "experiment": "E-453",
    "n_fixed": len(fixed),
    "n_remain": len(remain),
    "syntax_errors": bad,
}
p = os.path.join(ROOT, "proto", "results", "e453_fix_rstring.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 {p}")
