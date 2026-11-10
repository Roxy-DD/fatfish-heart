"""E-338 探查真实系统结构，用于画架构图/模型图（只读）"""
import sqlite3
import os
import re
import json
import ast

HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(HERE, "cosmos_mem")
DB = os.path.join(MEM, "dsh_mem.db")
PLUGIN = os.path.join(os.path.dirname(HERE), "dsh-plugin")

print("=" * 78)
print("E-338 系统结构探查")
print("=" * 78)

# ---------- ① 数据库表 ----------
print("\n① 数据库表结构")
con = sqlite3.connect(DB)
tables = [r[0] for r in con.execute(
    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
for t in tables:
    cols = con.execute(f'PRAGMA table_info("{t}")').fetchall()
    n = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
    types = {c[1]: c[2] for c in cols}
    print(f"\n  [{t}]  {n} 行, {len(cols)} 列")
    print("    " + ", ".join(f"{c[1]}:{c[2]}" for c in cols))
    # 索引
    idx = con.execute(f'PRAGMA index_list("{t}")').fetchall()
    if idx:
        print("    索引: " + ", ".join(i[1] for i in idx))
con.close()

# ---------- ② 模块清单 ----------
print("\n② cosmos_mem 模块（当前生效的，排除 archive_legacy）")
for f in sorted(os.listdir(MEM)):
    if not f.endswith(".py") or f.startswith("_"):
        continue
    p = os.path.join(MEM, f)
    with open(p, "r", encoding="utf-8") as fh:
        src = fh.read()
    try:
        tree = ast.parse(src)
    except Exception:
        continue
    funcs = [n.name for n in tree.body
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    classes = [n.name for n in tree.body if isinstance(n, ast.ClassDef)]
    lines = len(src.split("\n"))
    print(f"  {f:<24} {lines:>5}行  类:{','.join(classes) or '-'}  "
          f"函数:{len(funcs)}个")

# ---------- ③ 插件结构 ----------
print("\n③ dsh-plugin 模块")
for f in sorted(os.listdir(PLUGIN)):
    if f.endswith(".js"):
        p = os.path.join(PLUGIN, f)
        with open(p, "r", encoding="utf-8") as fh:
            src = fh.read()
        lines = len(src.split("\n"))
        # 顶层函数/导出
        exports = re.findall(r"export (?:const|function|class) (\w+)", src)
        print(f"  {f:<20} {lines:>5}行  导出:{','.join(exports) or '-'}")

# ---------- ④ kernel_server 命令 ----------
print("\n④ kernel 命令注册")
ks = os.path.join(MEM, "kernel_server.py")
with open(ks, "r", encoding="utf-8") as f:
    src = f.read()
handlers = re.findall(r"register\(\s*['\"]([\w-]+)['\"]", src)
if not handlers:
    handlers = re.findall(r"['\"]([\w]+)['\"]\s*:\s*(?:cmd_|handler)", src)
print(f"  找到: {handlers}")

# ---------- ⑤ index.js 注册的工具与路由 ----------
print("\n⑤ 插件工具与路由")
idx = os.path.join(PLUGIN, "index.js")
with open(idx, "r", encoding="utf-8") as f:
    isrc = f.read()
tools = re.findall(r"name:\s*'(mem_\w+)'", isrc)
print(f"  工具({len(set(tools))}): {', '.join(sorted(set(tools)))}")
routes = re.search(r"const routes = \[([^\]]+)\]", isrc)
if routes:
    rl = re.findall(r"'([^']+)'", routes.group(1))
    print(f"  路由({len(rl)}): {', '.join(rl)}")
events = re.findall(r"ctx\.on\(['\"]([^'\"]+)['\"]", isrc)
print(f"  监听事件: {', '.join(sorted(set(events)))}")

# ---------- ⑥ 关键配置 ----------
print("\n⑥ 插件 DEFAULTS / Config 字段")
m = re.search(r"const DEFAULTS = \{([\s\S]*?)\n\}", isrc)
if m:
    keys = re.findall(r"^\s*(\w+):", m.group(1), re.M)
    print(f"  DEFAULTS({len(keys)}): {', '.join(keys)}")

# ---------- ⑦ 保护层级与种子 ----------
print("\n⑦ 分层保护")
import sys
sys.path.insert(0, MEM)
try:
    import hook as _hook
    from store_v2 import MemoryStore
    st = MemoryStore(DB)
    prot = _hook.protected_of(st)
    from collections import Counter
    c = Counter(p["level"] for p in prot)
    print(f"  受保护: {dict(c)}")
    st.con.close()
except Exception as e:
    print(f"  读取失败: {e}")

print("\n" + "=" * 78)
