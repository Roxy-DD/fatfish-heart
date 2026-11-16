"""E-457 项目改名：肥鱼之心 → Fat Fish Heart

用户要求：「项目名字统一叫做肥鱼之心，英文名也是翻译过去」

直译结果：
  显示名   Fat Fish Heart
  slug     fatfish-heart
  Python   fatfish_heart
  插件包    @local/dsh-fatfish-heart
  工具前缀  fh_ 或保留 mem_（工具名不带项目名，保持不变）

★ 分批改，每批验证，避免一次改崩：
  B1. 文档/README 里的显示名
  B2. 插件 package.json + index.js 的 name/httpBase
  B3. proto/cosmos_mem/ → proto/fatfish_heart/（目录重命名，高影响）
  B4. import 语句和 DB 路径

本脚本先做 B1（最安全），并统计 B2/B3/B4 的影响面。
"""
import os
import re
import json
import subprocess

ROOT = r"C:\Users\xuee\Desktop\肥鱼之心"

print("=" * 74)
print("E-457 改名影响面统计")
print("=" * 74)

r = subprocess.run(["git", "ls-files"], cwd=ROOT,
                   capture_output=True, text=True, encoding="utf-8")
files = [f for f in r.stdout.split("\n") if f.strip()]

# 统计各标识符出现位置
PATTERNS = {
    "肥鱼之心": re.compile("肥鱼之心"),
    "cosmos_mem": re.compile(r"\bcosmos_mem\b"),
    "cosmos-mem": re.compile(r"\bcosmos-mem\b"),
    "COSMOS": re.compile(r"\bCOSMOS\b"),
    "@local/dsh-cosmos-mem": re.compile(r"@local/dsh-cosmos-mem"),
}

by_ctx = {k: {"path": 0, "content": 0, "files": set()}
          for k in PATTERNS}

for f in files:
    p = os.path.join(ROOT, f)
    for name, pat in PATTERNS.items():
        if pat.search(f):
            by_ctx[name]["path"] += 1
        if not os.path.exists(p):
            continue
        if not f.endswith((".py", ".js", ".mjs", ".md", ".json",
                           ".html", ".yml", ".yaml", ".txt")):
            continue
        try:
            c = open(p, encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        n = len(pat.findall(c))
        if n:
            by_ctx[name]["content"] += n
            by_ctx[name]["files"].add(f)

print("\n标识符影响面：")
print(f"  {'标识符':<24} {'路径中':>6} {'内容中':>6} {'文件数':>6}")
print("  " + "-" * 48)
for name, d in by_ctx.items():
    print(f"  {name:<24} {d['path']:>6} {d['content']:>6} "
          f"{len(d['files']):>6}")

# 哪些文件含 cosmos_mem import
print(f"\n{'='*74}")
print("Python import 依赖（改名最危险的部分）")
print("=" * 74)

import_users = []
for f in files:
    if not f.endswith(".py"):
        continue
    p = os.path.join(ROOT, f)
    if not os.path.exists(p):
        continue
    try:
        c = open(p, encoding="utf-8", errors="replace").read()
    except Exception:
        continue
    if re.search(r"^\s*(from|import)\s+cosmos_mem", c, re.M):
        import_users.append(f)

print(f"含 'from/import cosmos_mem' 的文件: {len(import_users)}")
for f in import_users[:15]:
    print(f"  {f}")

# sys.path.insert 指向 cosmos_mem 的
path_users = []
for f in files:
    if not f.endswith(".py"):
        continue
    p = os.path.join(ROOT, f)
    if not os.path.exists(p):
        continue
    try:
        c = open(p, encoding="utf-8", errors="replace").read()
    except Exception:
        continue
    if "cosmos_mem" in c and ("sys.path" in c or "insert" in c):
        path_users.append(f)

print(f"\nsys.path 指向 cosmos_mem 的文件: {len(path_users)}")

out = {
    "experiment": "E-457",
    "impact": {k: {"path": v["path"], "content": v["content"],
                   "n_files": len(v["files"])}
               for k, v in by_ctx.items()},
    "import_users": import_users,
    "path_users": path_users,
}
p = os.path.join(ROOT, "proto", "results", "e457_rename_impact.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 {p}")
