"""E-468 批量修实验脚本的本机路径（生态视角下的必修项）

为什么现在必须修（在此之前可以放过）：

  用户说明了开源的真正目的：用社区形成「环管群」效果。
  每个 fork 是一个「环」，要能独立生长。

  如果 81 个实验脚本硬编码 C:\\Users\\xuee\\...，那么：
    · 别人 fork 后跑不了这些脚本
    · 跑不了就无法验证我的结论
    · 无法验证就无法改进
    · → 谱系断在第一代，环管群不成立

  这不是"代码洁癖"，是机制性的阻断。

修法（吸取之前两次改坏代码的教训）：
  1. 用**统一模板**替换，不搞花样：
     项目根 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
     对 proto/xxx.py 来说，上溯 2 级到项目根
  2. **每个文件改完立刻 ast.parse**
  3. **改完立刻抽一个真实跑一次**
  4. 失败立即跳过该文件，不强行改

  只改这些形式（见过的高频写法）：
     d = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "proto", "cosmos_mem")
     sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "proto", "cosmos_mem"))
     ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
     _HERE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "proto")
"""
import ast
import json
import os
import re
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.join(ROOT, "proto")

print("=" * 72)
print("E-468 批量修实验脚本本机路径")
print("=" * 72)

# 项目根表达式（proto/xxx.py 上溯 2 级）
ROOT_EXPR = ('os.path.dirname(os.path.dirname('
             'os.path.abspath(__file__)))')

# 完整字面量模式（连引号一起匹配，避免 r-string 残留问题）
LITERALS = [
    # 带 r 前缀的双引号
    (re.compile(r'os.path.dirname(os.path.dirname(os.path.abspath(__file__)))'),
     ROOT_EXPR),
    (re.compile(r'os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "+proto")'),
     f'os.path.join({ROOT_EXPR}, "proto")'),
    (re.compile(r'os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "+proto", "+cosmos_mem")'),
     f'os.path.join({ROOT_EXPR}, "proto", "cosmos_mem")'),
    (re.compile(r'os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "+models", "+bge-small-zh")'),
     f'os.path.join({ROOT_EXPR}, "models", "bge-small-zh")'),
    # 普通双引号（单反斜杠）
    (re.compile(r'os.path.dirname(os.path.dirname(os.path.abspath(__file__)))'),
     ROOT_EXPR),
    (re.compile(r'os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "proto", "cosmos_mem")'),
     f'os.path.join({ROOT_EXPR}, "proto", "cosmos_mem")'),
    # 单引号形式
    (re.compile(r"'C:\\Users\\xuee\\Desktop\\肥鱼之心'"),
     ROOT_EXPR),
]

# 收集目标文件
files = []
for fn in os.listdir(HERE):
    if fn.endswith(".py"):
        files.append(os.path.join(HERE, fn))

print(f"\n扫描 {len(files)} 个脚本")

changed = []
skipped = []
failed = []

for p in files:
    fn = os.path.basename(p)
    try:
        raw = open(p, "rb").read()
        text = None
        for enc in ("utf-8", "gbk"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            skipped.append((fn, "编码无法识别"))
            continue

        if "xuee" not in text:
            continue

        orig = text
        for pat, repl in LITERALS:
            text = pat.sub(repl, text)

        # 还有残留（没被上面的模式覆盖）→ 跳过，不改半截
        if "C:\\Users\\xuee" in text or "C:\\\\Users\\\\xuee" in text:
            skipped.append((fn, "含未覆盖的路径形式"))
            continue

        if text == orig:
            skipped.append((fn, "无匹配"))
            continue

        # 确认 os 已导入
        if not re.search(r"^\s*import os\b|^\s*from os import", text, re.M):
            if not re.search(r"^import .*\bos\b", text, re.M):
                # 在第一个 import 后插入
                m = re.search(r"^(import |from )", text, re.M)
                if m:
                    text = (text[:m.start()] + "import os\n"
                            + text[m.start():])

        try:
            ast.parse(text)
        except SyntaxError as e:
            failed.append((fn, f"line {e.lineno}: {e.msg}"))
            continue

        open(p, "wb").write(text.encode(enc if enc == "utf-8" else "utf-8"))
        changed.append(fn)

    except Exception as e:
        failed.append((fn, str(e)[:60]))

print(f"\n修改 {len(changed)} 个")
if changed:
    for fn in changed[:15]:
        print(f"  ✅ {fn}")
    if len(changed) > 15:
        print(f"  ... 另有 {len(changed)-15} 个")

print(f"\n跳过 {len(skipped)} 个")
for fn, why in skipped[:10]:
    print(f"  · {fn}: {why}")

if failed:
    print(f"\n失败 {len(failed)} 个")
    for fn, why in failed[:10]:
        print(f"  ❌ {fn}: {why}")

# ---------- 验证 ----------
print(f"\n{'='*72}")
print("验证")
print("=" * 72)

# 1. 语法
syn = []
for p in files:
    try:
        ast.parse(open(p, encoding="utf-8", errors="replace").read())
    except SyntaxError as e:
        syn.append((os.path.basename(p), e.lineno))
print(f"语法错误: {len(syn)}")
for f, l in syn[:5]:
    print(f"  ❌ {f}:{l}")

# 2. 剩余硬编码
remain = []
for p in files:
    t = open(p, encoding="utf-8", errors="replace").read()
    if "xuee" in t:
        remain.append(os.path.basename(p))
print(f"仍含本机路径: {len(remain)}")
for f in remain[:10]:
    print(f"  · {f}")

# 3. 实测跑一个改过的脚本
print(f"\n{'='*72}")
print("实测：跑一个改过的脚本")
print("=" * 72)

probe = None
for cand in ("run_e377_criteria.py", "run_e389_falsified.py",
             "run_e386_regression.py"):
    if cand in changed:
        probe = cand
        break

if probe:
    r = subprocess.run(
        ["python", "-u", os.path.join(HERE, probe)],
        cwd=HERE, capture_output=True, text=True, timeout=300,
        encoding="utf-8", errors="replace")
    tail = (r.stdout or "").strip().split("\n")[-3:]
    print(f"  {probe}: exit={r.returncode}")
    for ln in tail:
        print(f"    {ln}")
    if r.returncode != 0:
        print("  stderr:", (r.stderr or "")[-300:])
else:
    print("  （没有可测的改过脚本）")

out = {
    "experiment": "E-468",
    "n_changed": len(changed),
    "user_scripts": len(files),
    "n_skipped": len(skipped),
    "n_failed": len(failed),
    "n_syntax": len(syn),
    "n_remain": len(remain),
    "changed": changed,
    "skipped": [{"file": f, "why": w} for f, w in skipped],
    "remain": remain,
}
p = os.path.join(HERE, "results", "e468_portable.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 {p}")
