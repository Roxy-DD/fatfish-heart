"""E-458 改名 B1：文档与显示名

映射表：
  肥鱼之心       → Fat Fish Heart   （文档正文、README）
  cosmos-mem     → fatfish-heart    （插件名、路由、UI 显示）
  cosmos_mem     → fatfish_heart    （路径、目录名）
  @local/dsh-cosmos-mem → @local/dsh-fatfish-heart

★ 注意保留中文名「肥鱼之心」的地方：
  用户说「项目名字统一叫做肥鱼之心，英文名也是翻译过去」
  意思是：中文名保留，同时提供英文名。不是删掉中文。

  所以：README 用「肥鱼之心（Fat Fish Heart）」
       包名/slug 用 fatfish-heart
"""
import os
import re
import json
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 显示名替换规则（文档类）
DOC_REPL = [
    # 首次出现：中英并列
    ("# 肥鱼之心 · 情景记忆系统",
     "# 肥鱼之心（Fat Fish Heart）· 情景记忆系统"),
    # 插件名
    ("cosmos-mem 插件", "fatfish-heart 插件"),
    ("cosmos-mem", "fatfish-heart"),
    # 模块目录
    ("cosmos_mem", "fatfish_heart"),
]

print("=" * 74)
print("E-458 改名 B1：文档")
print("=" * 74)

r = subprocess.run(["git", "ls-files"], cwd=ROOT,
                   capture_output=True, text=True, encoding="utf-8")
files = [f for f in r.stdout.split("\n") if f.strip()]

changed = []
for f in files:
    if not f.endswith((".md", ".html")):
        continue
    p = os.path.join(ROOT, f)
    if not os.path.exists(p):
        continue
    try:
        c = open(p, encoding="utf-8", errors="replace").read()
    except Exception:
        continue

    orig = c
    for a, b in DOC_REPL:
        c = c.replace(a, b)

    if c != orig:
        open(p, "w", encoding="utf-8", newline="\n").write(c)
        changed.append(f)

print(f"修改 {len(changed)} 个文档文件")
for f in changed[:12]:
    print(f"  {f}")

# ---------- 目录重命名（cosmos_mem → fatfish_heart）----------
print(f"\n{'='*74}")
print("目录重命名")
print("=" * 74)

import shutil
src = os.path.join(ROOT, "proto", "cosmos_mem")
dst = os.path.join(ROOT, "proto", "fatfish_heart")
if os.path.exists(src):
    if os.path.exists(dst):
        print(f"  ⚠ 目标已存在: {dst}")
    else:
        # 用 git mv 保留历史
        rr = subprocess.run(
            ["git", "mv", "proto/cosmos_mem", "proto/fatfish_heart"],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        if rr.returncode == 0:
            print(f"  ✅ git mv proto/cosmos_mem → proto/fatfish_heart")
        else:
            print(f"  ⚠ git mv 失败: {rr.stderr[:200]}")
else:
    print(f"  源不存在: {src}")

out = {
    "experiment": "E-458",
    "n_doc_changed": len(changed),
    "docs": changed,
}
p = os.path.join(ROOT, "proto", "results", "e458_rename_b1.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 {p}")
