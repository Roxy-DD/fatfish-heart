"""E-469 修剩下 61 个脚本（用字面量解析，不用正则猜转义）

E-468 为什么只修好 21/82：

  我用正则匹配特定路径形式，比如：
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "+proto", "+cosmos_mem")
  但这个正则对 `\\proto\\cosmos_mem` 这种双反斜杠形式不匹配，
  而实际文件里就是这么写的。

  根本问题：**我在猜转义层级**，而不是解析。

正确做法：
  1. 用正则只匹配「整个字符串字面量的边界」：
       r"C:...content..."  或  "C:...content..."
     不关心里面有多少反斜杠
  2. 把匹配到的内容做规范化（\\ → /），得到相对路径
  3. 用规范化的结果生成 os.path.join 表达式

这样不管原文是单反斜杠、双反斜杠、还是混合，都能正确处理。
"""
import ast
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.join(ROOT, "proto")

print("=" * 72)
print("E-469 用字面量解析修剩余脚本")
print("=" * 72)

# 只匹配字面量边界，不关心内部转义
LIT = re.compile(r'r?"(C:[^"]*?)"')

ROOT_EXPR = ('os.path.dirname(os.path.dirname('
             'os.path.abspath(__file__)))')

files = [os.path.join(HERE, f) for f in os.listdir(HERE)
         if f.endswith(".py")]

changed = []
failed = []
still = []

for p in files:
    fn = os.path.basename(p)
    raw = open(p, "rb").read()
    text = None
    for enc in ("utf-8", "gbk"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None or "xuee" not in text:
        continue

    def repl(m):
        s = m.group(1)
        # 规范化：把任意多反斜杠变单个，再转 /
        norm = re.sub(r"\\+", "/", s)
        # norm 形如 C:/Users/xuee/Desktop/肥鱼之心/proto/cosmos_mem
        idx = norm.find("肥鱼之心")
        if idx < 0:
            return m.group(0)     # 不是本项目路径，不动
        rest = norm[idx + len("肥鱼之心"):].strip("/")
        if not rest:
            return ROOT_EXPR
        parts = [x for x in rest.split("/") if x]
        quoted = ", ".join(f'"{x}"' for x in parts)
        return f'os.path.join({ROOT_EXPR}, {quoted})'

    new_text = LIT.sub(repl, text)

    if new_text == text:
        continue

    # 确认 os 已导入
    if not re.search(r"^\s*import os\b|^\s*from os import", new_text, re.M):
        m = re.search(r"^(import |from )", new_text, re.M)
        if m:
            new_text = (new_text[:m.start()] + "import os\n"
                        + new_text[m.start():])

    try:
        ast.parse(new_text)
    except SyntaxError as e:
        failed.append((fn, f"line {e.lineno}: {e.msg}"))
        continue

    open(p, "wb").write(new_text.encode("utf-8"))
    changed.append(fn)

print(f"\n修改 {len(changed)} 个")
for fn in changed[:20]:
    print(f"  ✅ {fn}")
if len(changed) > 20:
    print(f"  ... 另 {len(changed)-20} 个")

if failed:
    print(f"\n失败 {len(failed)}:")
    for fn, why in failed[:8]:
        print(f"  ❌ {fn}: {why}")

# ---------- 验证 ----------
print(f"\n{'='*72}")
print("验证")
print("=" * 72)

syn = 0
for p in files:
    try:
        ast.parse(open(p, encoding="utf-8", errors="replace").read())
    except SyntaxError:
        syn += 1
print(f"语法错误: {syn}")

for p in files:
    t = open(p, encoding="utf-8", errors="replace").read()
    if "xuee" in t:
        still.append(os.path.basename(p))
print(f"仍含本机路径: {len(still)}")
for f in still[:10]:
    print(f"  · {f}")

# 抽一个验证路径表达式能正确求值
print(f"\n{'='*72}")
print("实测：路径表达式求值")
print("=" * 72)

probe = None
for cand in ("run_e110_dedup.py", "run_e390_"):
    if cand in changed:
        probe = cand
        break
if not probe and changed:
    probe = changed[0]

if probe:
    pp = os.path.join(HERE, probe)
    t = open(pp, encoding="utf-8", errors="replace").read()
    # 找含 ROOT_EXPR 的行
    for line in t.split("\n"):
        if "dirname(os.path.dirname" in line and "=" in line:
            print(f"  {probe}:")
            print(f"    {line.strip()[:110]}")
            break

out = {
    "experiment": "E-469",
    "n_changed": len(changed),
    "n_failed": len(failed),
    "n_syntax": syn,
    "n_still": len(still),
    "changed": changed,
    "still": still,
}
p = os.path.join(HERE, "results", "e469_portable2.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 {p}")
