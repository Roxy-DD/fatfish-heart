"""E-459 改名 B2：插件对外标识符

改名映射（只改**对外可见**的标识符）：
  export const name = 'cosmos-mem'    → 'fatfish-heart'
  httpBase: '/cosmos-mem'             → '/fatfish-heart'
  client.js: BASE = '/cosmos-mem'     → '/fatfish-heart'
  client.js: id: '@local/dsh-cosmos-mem' → '@local/dsh-fatfish-heart'
  context name / UI 显示              → 'fatfish-heart'

★ 不改的：
  · Python 目录名 cosmos_mem（被运行中的进程锁定，且不对外可见）
  · 工具名 mem_*（工具名不带项目前缀，改了会破坏现有会话）
  · 历史 commit 与文档里的旧记录

★ 风险：改 httpBase 会让前端请求路径变，必须两边同时改。
  验证方式：重载后 curl 新路由，确认 200。
"""
import os
import re
import json
import subprocess

ROOT = r"C:\Users\xuee\Desktop\肥鱼之心"
PLUGIN = os.path.join(ROOT, "dsh-plugin")

print("=" * 74)
print("E-459 改名 B2：插件标识符")
print("=" * 74)

RULES = [
    ("export const name = 'cosmos-mem'",
     "export const name = 'fatfish-heart'"),
    ("httpBase: '/cosmos-mem'", "httpBase: '/fatfish-heart'"),
    ("cfg.httpBase || '/cosmos-mem'", "cfg.httpBase || '/fatfish-heart'"),
    ("id: '@local/dsh-cosmos-mem'", "id: '@local/dsh-fatfish-heart'"),
    ("const BASE = '/cosmos-mem';", "const BASE = '/fatfish-heart';"),
    ("{ name: 'cosmos-mem', text:", "{ name: 'fatfish-heart', text:"),
    ("## 记忆（cosmos-mem）", "## 记忆（fatfish-heart）"),
    ("* cosmos-mem 管理界面", "* fatfish-heart 管理界面"),
    ("id: 'cosmos-mem',", "id: 'fatfish-heart',"),
    ("key: 'cosmos-mem',", "key: 'fatfish-heart',"),
]

changed = []
for fn in ("index.js", "client.js", "lifecycle.js", "selfreload.js"):
    p = os.path.join(PLUGIN, fn)
    if not os.path.exists(p):
        continue
    try:
        c = open(p, encoding="utf-8", errors="replace").read()
    except Exception:
        continue
    orig = c
    for a, b in RULES:
        c = c.replace(a, b)
    if c != orig:
        open(p, "w", encoding="utf-8", newline="\n").write(c)
        n = sum(orig.count(a) - c.count(a) for a, _ in RULES)
        changed.append({"file": fn, "replacements": n})
        print(f"  {fn}: 修改")

print(f"\n修改 {len(changed)} 个文件")

# ---------- 验证 ----------
print(f"\n{'='*74}")
print("验证")
print("=" * 74)

# 语法检查
syn = []
for fn in ("index.js", "client.js", "lifecycle.js", "selfreload.js"):
    p = os.path.join(PLUGIN, fn)
    if not os.path.exists(p):
        continue
    r = subprocess.run(["node", "-c", p],
                       capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        syn.append((fn, (r.stderr or "")[:150]))
print(f"语法错误: {len(syn)}")
for f, e in syn:
    print(f"  ❌ {f}: {e}")

# 剩余 cosmos-mem
remain = []
for fn in ("index.js", "client.js", "lifecycle.js", "selfreload.js"):
    p = os.path.join(PLUGIN, fn)
    if not os.path.exists(p):
        continue
    c = open(p, encoding="utf-8", errors="replace").read()
    n = c.count("cosmos-mem")
    if n:
        remain.append((fn, n))
print(f"\n插件里剩余 'cosmos-mem': {sum(x[1] for x in remain)} 处")
for f, n in remain:
    print(f"  {f}: {n}")

# 包名一致性
pkg = json.load(open(os.path.join(PLUGIN, "package.json"),
                     encoding="utf-8"))
print(f"\npackage.json name: {pkg['name']}")
cl = open(os.path.join(PLUGIN, "client.js"),
          encoding="utf-8", errors="replace").read()
m = re.search(r"id:\s*'([^']+)'", cl)
print(f"client.js 首个 id: {m.group(1) if m else '?'}")

out = {
    "experiment": "E-459",
    "changed": changed,
    "syntax_errors": syn,
    "remaining_cosmos_mem": remain,
    "pkg_name": pkg["name"],
}
p = os.path.join(ROOT, "proto", "results", "e459_rename_b2.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 {p}")
