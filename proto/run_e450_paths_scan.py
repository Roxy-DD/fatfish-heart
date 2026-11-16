"""E-450 修复个人绝对路径（发版前必须）

问题：72 个已跟踪文件含 C:\\Users\\xuee\\... 硬编码路径。
后果：别人 clone 后跑不起来，且暴露用户名。

修复策略（按文件类型分开处理）：
  A. 实验脚本 run_e*.py / run_*.py
     → 用 os.path 相对推导，不写死
  B. 文档 README.md / docs/*.md
     → 改成占位符 <你的路径>
  C. 代码 dsh-plugin/*.js
     → 检查是否是注释里的示例，是则改占位符

★ 不动 core 模块里的运行时逻辑，只改示例/文档/脚本入口。
★ 改完必须跑全量回归（36 项）。
"""
import os
import re
import subprocess
import sys

ROOT = r"C:\Users\xuee\Desktop\肥鱼之心"
PAT = re.compile(r"C:\\\\Users\\\\xuee\\\\[^\"'\s,)\]]*"
              r"|C:\\Users\\xuee\\[^\"'\s,)\]]*")

print("=" * 74)
print("E-450 个人绝对路径扫描")
print("=" * 74)

# 收集已跟踪文件
r = subprocess.run(["git", "ls-files"], cwd=ROOT,
                   capture_output=True, text=True, encoding="utf-8")
files = [f for f in r.stdout.split("\n") if f.strip()]

hits = []
for f in files:
    if not f.endswith((".py", ".js", ".md", ".json", ".html", ".mjs")):
        continue
    p = os.path.join(ROOT, f)
    if not os.path.exists(p):
        continue
    try:
        c = open(p, encoding="utf-8", errors="replace").read()
    except Exception:
        continue
    for m in PAT.finditer(c):
        line = c[:m.start()].count("\n") + 1
        hits.append((f, line, m.group(0)))

print(f"命中 {len(hits)} 处，分布在 "
      f"{len(set(h[0] for h in hits))} 个文件")

# 分类统计
from collections import Counter
by_file = Counter(h[0] for h in hits)
print(f"\n前 15 个文件：")
for f, n in by_file.most_common(15):
    print(f"  {n:>3}  {f}")

# 看每类文件里的典型写法
print(f"\n典型写法（按文件类型）：")
seen_types = set()
for f, line, txt in hits:
    ext = os.path.splitext(f)[1]
    if ext in seen_types:
        continue
    seen_types.add(ext)
    print(f"\n  [{ext}] {f}:{line}")
    print(f"       {txt[:100]}")

out = {
    "experiment": "E-450",
    "n_hits": len(hits),
    "n_files": len(set(h[0] for h in hits)),
    "by_file": dict(by_file),
    "samples": [{"file": f, "line": l, "text": t[:120]}
                for f, l, t in hits[:30]],
}
import json
p = os.path.join(ROOT, "proto", "results", "e450_paths_scan.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 {p}")
