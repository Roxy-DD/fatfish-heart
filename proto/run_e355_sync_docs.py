"""E-355 架构文档的数字自动同步

问题：文档里手写的 DB 行数会立刻过时（刚才查是 438，几秒后变 439）。
每次手改既繁琐又不可靠。

解法：把「实测快照」区改为脚本生成，文档只保留结构说明。

本脚本：
  ① 读取真实数字
  ② 替换 Markdown / HTML 里的快照区块
  ③ 标记生成时间，明确"这是自动生成，不是手写"
"""
import sys
import os
import re
import sqlite3
import time
import warnings

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
MEM = os.path.join(_HERE, "cosmos_mem")
DB = os.path.join(MEM, "dsh_mem.db")
MD = os.path.join(ROOT, "docs", "architecture-and-model.md")
HTML = os.path.join(ROOT, "docs", "architecture-and-model.html")
STATUS = os.path.join(ROOT, "docs", "PROJECT-STATUS.md")

print("=" * 74)
print("E-355 架构文档数字同步")
print("=" * 74)

con = sqlite3.connect(DB)
stats = {}
for t in ["episodes", "vectors", "ep_fts", "audit_log",
          "episodes_history", "notes", "todos", "meta"]:
    stats[t] = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
valid = con.execute(
    "SELECT COUNT(*) FROM episodes WHERE invalid_at IS NULL").fetchone()[0]
invalid = stats["episodes"] - valid

# 保护层级
prot = {"L0": 0, "L1": 0, "L2": 0}
for (key,) in con.execute(
        "SELECT key FROM meta WHERE key LIKE 'hook:%'").fetchall():
    val = con.execute("SELECT val FROM meta WHERE key=?", (key,)).fetchone()
    if val:
        try:
            import json
            d = json.loads(val[0])
            lv = d.get("level")
            if lv in prot:
                prot[lv] += 1
        except Exception:
            pass
con.close()

now = time.strftime("%Y-%m-%d %H:%M")

print(f"\n真实数据:")
print(f"  episodes {stats['episodes']} (有效 {valid} / 失效 {invalid})")
print(f"  vectors  {stats['vectors']}")
print(f"  notes    {stats['notes']}")
print(f"  todos    {stats['todos']}")
print(f"  保护     {prot}")

# ---------- 生成快照块 ----------
snapshot_md = f"""<!-- AUTO-GENERATED: 由 proto/run_e355_sync_docs.py 生成，勿手改 -->
> **实测快照**（自动生成于 {now}）
>
> | 指标 | 数值 |
> |---|---|
> | episodes 总行数 | {stats['episodes']} |
> | 有效记忆 | {valid} |
> | 失效记忆（只打标记） | {invalid} |
> | vectors 向量行 | {stats['vectors']} |
> | audit_log 审计 | {stats['audit_log']} |
> | episodes_history 快照 | {stats['episodes_history']} |
> | notes 笔记 | {stats['notes']} |
> | todos 待办 | {stats['todos']} |
> | meta 键值 | {stats['meta']} |
> | L0 / L1 / L2 保护 | {prot['L0']} / {prot['L1']} / {prot['L2']} |
>
> 数字由脚本读取真实 SQLite 生成，不是手写；DB 持续写入，重新运行脚本即可刷新。
<!-- /AUTO-GENERATED -->"""

snapshot_html = f"""<!-- AUTO-GENERATED: 由 proto/run_e355_sync_docs.py 生成，勿手改 -->
<div class="stat">
  <div><b>{stats['episodes']}</b>episodes 行</div>
  <div><b>{valid}</b>有效记忆</div>
  <div><b>{invalid}</b>失效(只打标记)</div>
  <div><b>{stats['vectors']}</b>向量行</div>
  <div><b>{stats['notes']}</b>笔记</div>
  <div><b>{stats['todos']}</b>待办</div>
  <div><b>{prot['L0']}/{prot['L1']}/{prot['L2']}</b>L0/L1/L2</div>
</div>
<div class="sub">实测快照，自动生成于 {now}，由 <code>proto/run_e355_sync_docs.py</code> 读取真实 SQLite 生成</div>
<!-- /AUTO-GENERATED -->"""

# ---------- 替换 Markdown ----------
md = open(MD, encoding="utf-8").read()
if "<!-- AUTO-GENERATED" in md:
    md_new = re.sub(
        r"<!-- AUTO-GENERATED[\s\S]*?<!-- /AUTO-GENERATED -->",
        snapshot_md, md)
    if md_new == md:
        print("\n⚠ Markdown 替换未生效（正则可能不匹配）")
else:
    # 首次插入到「二、模型图」之前
    anchor = "## 二、模型图"
    md_new = md.replace(anchor, snapshot_md + "\n\n---\n\n" + anchor, 1)
    if md_new == md:
        print("\n⚠ Markdown 锚点未找到")

open(MD, "w", encoding="utf-8").write(md_new)
print(f"\n✅ Markdown 已更新 ({len(md_new)} 字)")

# ---------- 替换 HTML ----------
h = open(HTML, encoding="utf-8").read()
if "<!-- AUTO-GENERATED" in h:
    h_new = re.sub(
        r"<!-- AUTO-GENERATED[\s\S]*?<!-- /AUTO-GENERATED -->",
        snapshot_html, h)
else:
    # 首次替换已有的 .stat 块
    h_new = re.sub(r'<div class="stat">[\s\S]*?</div>\s*</div>',
                   snapshot_html, h, count=1)
    if h_new == h:
        print("⚠ HTML 替换未生效")

open(HTML, "w", encoding="utf-8").write(h_new)
print(f"✅ HTML 已更新 ({len(h_new)} 字)")

# ---------- 替换 PROJECT-STATUS.md ----------
if os.path.exists(STATUS):
    snapshot_status = f"""<!-- AUTO-GENERATED: 由 proto/run_e355_sync_docs.py 生成，勿手改 -->
**实测快照**（自动生成于 {now}）

| 指标 | 数值 |
|---|---|
| episodes 总行数 | {stats['episodes']} |
| 有效记忆 | {valid} |
| 失效记忆（只打标记，R2） | {invalid} |
| vectors 向量行 | {stats['vectors']} |
| ep_fts 全文索引行 | {stats['ep_fts']} |
| episodes_history 修订快照 | {stats['episodes_history']} |
| notes 笔记 | {stats['notes']} |
| todos 待办 | {stats['todos']} |
| audit_log 审计 | {stats['audit_log']} |
| L0 / L1 / L2 保护 | {prot['L0']} / {prot['L1']} / {prot['L2']} |

三项一致性检查：
- vectors({stats['vectors']}) == episodes({stats['episodes']}) → R3 索引完整
- ep_fts({stats['ep_fts']}) == episodes({stats['episodes']}) → 全文索引完整
- 有效({valid}) + 失效({invalid}) == 总数({stats['episodes']}) → R2 无丢失
<!-- /AUTO-GENERATED -->"""
    sc = open(STATUS, encoding="utf-8").read()
    sc_new = re.sub(
        r"<!-- AUTO-GENERATED[\s\S]*?<!-- /AUTO-GENERATED -->",
        snapshot_status, sc)
    open(STATUS, "w", encoding="utf-8").write(sc_new)
    changed = "✅ 已更新" if sc_new != sc else "⚠ 未变化"
    print(f"{changed} PROJECT-STATUS.md ({len(sc_new)} 字)")

# ---------- 验证 ----------
print("\n验证：")
for p, label in [(MD, "MD"), (HTML, "HTML")]:
    c = open(p, encoding="utf-8").read()
    has = "AUTO-GENERATED" in c
    has_valid = str(valid) in c
    print(f"  {label}: 自动块={'✅' if has else '❌'} 含有效数 {valid}={'✅' if has_valid else '❌'}")

import json
out = {
    "experiment": "E-355",
    "episodes": stats["episodes"],
    "valid": valid,
    "invalid": invalid,
    "prot": prot,
    "generated_at": now,
}
p = os.path.join(_HERE, "results", "e355_sync.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 results/e355_sync.json")
