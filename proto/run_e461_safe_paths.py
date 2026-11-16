"""E-461 安全修复硬编码路径（逐文件、逐验证）

教训（E-450~459 连续两次改坏代码）：
  · 第一次：把 r"...path..." 换成表达式，r 前缀残留 → 变字符串字面量
  · 第二次：替换规则互相污染 → id 变 dd、function 变 functdon

所以这次的做法：
  1. **只做最小替换**：只替换路径字符串本身，不动 r 前缀和引号
     → 用 `os.path.join(...)` 整体替换 r"C:\\...\\models\\bge-small-zh"
       这种"整个字面量连前缀一起换"的方式
  2. **每改一个文件，立刻 ast.parse 验证**
  3. 失败立即回滚该文件

只改真正影响可运行性的：核心模块（cosmos_mem/*.py）里的路径。
实验脚本（run_e*.py）不影响核心功能，留待后续。
"""
import os
import re
import ast
import json
import subprocess

ROOT = r"C:\Users\xuee\Desktop\肥鱼之心"

print("=" * 74)
print("E-461 安全修复核心模块路径")
print("=" * 74)

# 目标：核心模块（不是实验脚本）
CORE_DIR = os.path.join(ROOT, "proto", "cosmos_mem")

# 需要修的路径模式 → （文件深度决定上溯级数）
# proto/cosmos_mem/xxx.py 上溯 3 级 = 项目根
UPS = 3
ROOT_EXPR = ("os.path.dirname(" * UPS
             + "os.path.abspath(__file__)"
             + ")" * UPS)

# 匹配完整的原始字面量（含 r 前缀和引号）
# r"C:\Users\xuee\Desktop\肥鱼之心\models\bge-small-zh"
PATTERNS = [
    # models 目录
    (re.compile(r'r"C:\\Users\\xuee\\Desktop\\肥鱼之心\\models\\bge-small-zh"'),
     ROOT_EXPR + ', "models", "bge-small-zh"'),
    (re.compile(r'r"C:\\Users\\xuee\\Desktop\\肥鱼之心\\models\\bge-reranker-v2-m3-fp16"'),
     ROOT_EXPR + ', "models", "bge-reranker-v2-m3-fp16"'),
    (re.compile(r'r"C:\\Users\\xuee\\Desktop\\肥鱼之心\\models\\vad-macbert"'),
     ROOT_EXPR + ', "models", "vad-macbert"'),
    (re.compile(r'r"C:\\Users\\xuee\\Desktop\\肥鱼之心\\proto\\cosmos_mem"'),
     ROOT_EXPR + ', "proto", "cosmos_mem"'),
    (re.compile(r'r"C:\\Users\\xuee\\Desktop\\肥鱼之心"'),
     ROOT_EXPR),
]

# 扫描核心模块
targets = []
for fn in os.listdir(CORE_DIR):
    if fn.endswith(".py"):
        targets.append(os.path.join(CORE_DIR, fn))
for sub in ("archive_legacy",):
    d = os.path.join(CORE_DIR, sub)
    if os.path.isdir(d):
        for fn in os.listdir(d):
            if fn.endswith(".py"):
                targets.append(os.path.join(d, fn))

print(f"扫描 {len(targets)} 个核心模块文件")

changed = []
failed = []

for p in targets:
    try:
        with open(p, "rb") as f:
            raw = f.read()
        # 多编码
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
        for pat, repl in PATTERNS:
            text = pat.sub(repl, text)

        if text == orig:
            continue

        # ★ 关键：改完立刻验证语法
        try:
            ast.parse(text)
        except SyntaxError as e:
            failed.append({
                "file": os.path.basename(p),
                "error": f"line {e.lineno}: {e.msg}",
            })
            print(f"  ❌ {os.path.basename(p)} 语法错误，跳过: {e.msg}")
            continue

        with open(p, "wb") as f:
            f.write(text.encode(enc))
        changed.append(os.path.basename(p))
        print(f"  ✅ {os.path.basename(p)}")

    except Exception as e:
        failed.append({"file": os.path.basename(p), "error": str(e)[:80]})

print(f"\n修改 {len(changed)} 个，失败 {len(failed)} 个")

# ---------- 验证 ----------
print(f"\n{'='*74}")
print("验证")
print("=" * 74)

# 1. 全部核心模块语法
syn = []
for p in targets:
    try:
        ast.parse(open(p, encoding="utf-8", errors="replace").read())
    except SyntaxError as e:
        syn.append((os.path.basename(p), e.lineno, e.msg))
print(f"语法错误: {len(syn)}")
for f, l, m in syn:
    print(f"  ❌ {f}:{l} {m}")

# 2. 还有多少硬编码
remain = 0
for p in targets:
    c = open(p, encoding="utf-8", errors="replace").read()
    n = c.count("C:\\Users\\xuee")
    if n:
        remain += n
        print(f"  剩余 {os.path.basename(p)}: {n} 处")
print(f"\n核心模块剩余硬编码: {remain} 处")

# 3. 实测 import
print(f"\n{'='*74}")
print("实测 import")
print("=" * 74)

test = subprocess.run(
    ["python", "-c",
     "import sys, warnings; warnings.filterwarnings('ignore'); "
     f"sys.path.insert(0, r'{CORE_DIR}'); "
     "import store_v1; "
     "import os; "
     "print('MODEL_DIR =', store_v1.MODEL_DIR); "
     "print('exists:', os.path.exists(store_v1.MODEL_DIR))"],
    cwd=ROOT, capture_output=True, text=True,
    encoding="utf-8", errors="replace")
print((test.stdout or "")[-500:])
if test.returncode != 0:
    print("stderr:", (test.stderr or "")[-300:])

out = {
    "experiment": "E-461",
    "n_changed": len(changed),
    "changed": changed,
    "n_failed": len(failed),
    "failed": failed,
    "n_syntax": len(syn),
    "remain_hardcoded": remain,
}
p = os.path.join(ROOT, "proto", "results", "e461_safe_paths.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 {p}")
