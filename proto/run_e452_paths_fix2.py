"""E-452 编码感知的路径修复（E-451 失败原因：GBK 文件）

E-451 失败根因：
  文件实际是 GBK 编码（中文 Windows 默认），
  我用 UTF-8 读 → "肥鱼之心" 变乱码 → 字符串匹配失败。

  ★ 这是「假设文件是 UTF-8」导致的静默失败：
    替换函数跑了，改了 0 个文件，但没报错。

修法：
  ① 尝试多种编码读（utf-8 / gbk / latin-1）
  ② 记住实际编码，写回时用同一编码
  ③ 用 **只含 ASCII 的部分** 做匹配锚点：
     C:\\Users\\xuee\\Desktop\\  ← 这部分无中文，不受编码影响
     然后连同后面的中文目录一起替换（按字节边界处理）

更稳的做法：
  用正则匹配 `C:\\\\Users\\\\xuee\\\\Desktop\\\\[^"'\n]*`
  这样不管中文怎么编码，都能截到引号为止。
"""
import os
import re
import json
import subprocess

ROOT = r"C:\Users\xuee\Desktop\肥鱼之心"

print("=" * 74)
print("E-452 编码感知修复")
print("=" * 74)

r = subprocess.run(["git", "ls-files"], cwd=ROOT,
                   capture_output=True, text=True, encoding="utf-8")
files = [f for f in r.stdout.split("\n") if f.strip()]

# 只匹配 ASCII 前缀 + 任意到引号/换行前的内容
PAT = re.compile(r'C:\\\\Users\\\\xuee\\\\Desktop\\\\[^"\'\n]*'
                 r'|C:\\Users\\xuee\\Desktop\\[^"\'\n]*')

ENCODINGS = ("utf-8", "gbk", "latin-1")

stats = {"fixed": 0, "skipped_json": 0, "remain": 0}
fixed_files = []
enc_count = {}

for f in files:
    if not f.endswith((".py", ".js", ".mjs", ".md", ".html")):
        continue
    p = os.path.join(ROOT, f)
    if not os.path.exists(p):
        continue

    # 多编码尝试读
    raw = open(p, "rb").read()
    text = None
    enc_used = None
    for enc in ENCODINGS:
        try:
            text = raw.decode(enc)
            enc_used = enc
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        continue

    if not PAT.search(text):
        continue

    enc_count[enc_used] = enc_count.get(enc_used, 0) + 1

    # 计算替换表达式
    if f.startswith("proto/") and f.endswith(".py"):
        depth = f.count("/")
        ups = "os.path.dirname(" * (depth - 1)
        expr = ups + "os.path.abspath(__file__)" + ")" * (depth - 1)
        repl = expr
    else:
        repl = "<项目路径>"

    new_text = PAT.sub(repl, text)
    if new_text == text:
        continue

    # 确保替换后语法合理：若原来在字符串里，替换后仍是字符串
    try:
        open(p, "wb").write(new_text.encode(enc_used))
        fixed_files.append({"file": f, "encoding": enc_used})
        stats["fixed"] += 1
    except Exception as e:
        print(f"  写回失败 {f}: {e}")

print(f"\n修复 {stats['fixed']} 个文件")
print(f"编码分布: {enc_count}")

# 复查
print(f"\n{'='*74}")
print("复查")
print("=" * 74)

remain = []
for f in files:
    if not f.endswith((".py", ".js", ".mjs", ".md", ".html")):
        continue
    p = os.path.join(ROOT, f)
    if not os.path.exists(p):
        continue
    raw = open(p, "rb").read()
    for enc in ENCODINGS:
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            text = None
    if not text:
        continue
    m = PAT.findall(text)
    if m:
        remain.append({"file": f, "count": len(m),
                       "sample": m[0][:60]})

print(f"仍含绝对路径: {len(remain)} 个文件")
for it in remain[:12]:
    print(f"  {it['count']:>3}  {it['file']}")

# 语法检查（Python 文件）
print(f"\n{'='*74}")
print("Python 语法检查")
print("=" * 74)
import ast
bad = []
for it in fixed_files:
    if not it["file"].endswith(".py"):
        continue
    p = os.path.join(ROOT, it["file"])
    raw = open(p, "rb").read()
    try:
        src = raw.decode(it["encoding"])
        ast.parse(src)
    except Exception as e:
        bad.append((it["file"], str(e)[:80]))

print(f"语法错误: {len(bad)} 个")
for f, e in bad[:10]:
    print(f"  ❌ {f}: {e}")

out = {
    "experiment": "E-452",
    "n_fixed": stats["fixed"],
    "encodings": enc_count,
    "n_remain": len(remain),
    "remain": remain[:20],
    "syntax_errors": bad,
}
p = os.path.join(ROOT, "proto", "results", "e452_paths_fix2.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 {p}")
