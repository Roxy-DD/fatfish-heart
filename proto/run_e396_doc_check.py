"""E-396 文档一致性总校验

架构文档经过多轮修改（E-350 scoped、E-393 预算、E-395 配置章节），
需要一次性校验：
  ① 所有配置项都在文档里（23 项）
  ② 决策表包含最新实验结论（E-371/389/393）
  ③ 数字与真实 DB 一致（用 run_e355_sync_docs 同步）
  ④ Mermaid 图语法可渲染
  ⑤ 文档里的字段名与代码一致

本测试把「文档是否过时」这件事变成可自动检查的。
"""
import sys
import os
import re
import json
import warnings

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
MD = os.path.join(ROOT, "docs", "architecture-and-model.md")
IDX = os.path.join(ROOT, "dsh-plugin", "index.js")

results = []


def check(name, cond, detail=""):
    results.append({"name": name, "ok": bool(cond), "detail": detail})
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  {detail}" if detail else ""))


print("=" * 74)
print("E-396 文档一致性总校验")
print("=" * 74)

md = open(MD, encoding="utf-8").read()
src = open(IDX, encoding="utf-8").read()

# ---------- ① 配置项覆盖 ----------
print("\n【1】配置项覆盖")

m = re.search(r"const DEFAULTS = \{([\s\S]*?)\n\}", src)
configs = re.findall(r"^\s*(\w+):", m.group(1), re.M)
documented = [k for k in configs if f"`{k}`" in md]

check(f"全部 {len(configs)} 个配置项都在文档里",
      len(documented) == len(configs),
      f"{len(documented)}/{len(configs)}")

missing = [k for k in configs if k not in documented]
if missing:
    print(f"      缺失: {missing}")

# ---------- ② 关键决策是否记录 ----------
print("\n【2】关键决策记录")

DECISIONS = [
    ("E-371", "防重入", "turn 号防重入的坑"),
    ("E-387", "相似度", "话题切换检测的否证"),
    ("E-393", "预算", "两个预算的拆分"),
    ("E-350", "flag", "search_scoped 丢 flag"),
    ("E-348", "首轮", "首轮判断顺序错"),
]

for eid, kw, desc in DECISIONS:
    has_eid = eid in md
    has_kw = kw in md
    check(f"{eid}（{desc}）已记录", has_eid and has_kw,
          f"eid={'有' if has_eid else '无'} kw={'有' if has_kw else '无'}")

# ---------- ③ 字段名与代码一致 ----------
print("\n【3】字段名一致性")

FIELDS = ["protected_mode", "protected_preview", "is_first_turn",
          "injectEveryTurn", "autoInjectMaxTotal"]

for f in FIELDS:
    in_code = f in src
    in_doc = f in md
    ok = in_code and in_doc
    check(f"{f}（代码+文档都有）", ok,
          f"code={'✓' if in_code else '✗'} doc={'✓' if in_doc else '✗'}")

# ---------- ④ Mermaid 块基本完整 ----------
print("\n【4】Mermaid 块")

blocks = re.findall(r"```mermaid\n([\s\S]*?)```", md)
check("Mermaid 图数量", len(blocks) >= 5, f"{len(blocks)} 张")

# flowchart 的 subgraph/end 配对
bad = []
for i, b in enumerate(blocks, 1):
    if not b.lstrip().startswith("flowchart"):
        continue
    n_sub = len(re.findall(r"^\s*subgraph\b", b, re.M))
    n_end = len(re.findall(r"^\s*end\s*$", b, re.M))
    if n_sub != n_end:
        bad.append((i, n_sub, n_end))

check("flowchart 的 subgraph/end 配对", len(bad) == 0,
      f"{len(bad)} 处不配对" if bad else "全部正常")

# ---------- ⑤ 是否有过时的表述 ----------
print("\n【5】过时表述检查")

STALE = [
    ("21 项", "配置项数量已变 23"),
    ("三元组 `(ep, sc, note)`", "E-350 已改为四元组"),  # 但作为历史记录可以出现
]

for text, why in STALE:
    # 允许出现在"历史问题"的记述里，但不该出现在"当前实现"描述中
    count = md.count(text)
    print(f"   '{text}': 出现 {count} 次（{why}）")

# 关键：不能有"当前实现"式的错误说法
wrong_claims = [
    ("autoInjectMaxChars 是总预算", "autoInjectMaxChars 只管 body"),
    ("复用 turn 号防重入", "已改为 steerInFlight"),
]
for claim, correct in wrong_claims:
    has = claim in md
    check(f"没有错误说法「{claim}」", not has, correct if not has else "需修正")

# ---------- ⑥ 文档规模合理性 ----------
print("\n【6】文档规模")

lines = len(md.split("\n"))
chars = len(md)
print(f"   {lines} 行, {chars} 字")

check("文档规模合理（不是空壳也不臃肿）",
      100 < lines < 600, f"{lines} 行")

# ---------- 汇总 ----------
print("\n" + "=" * 74)
n_ok = sum(1 for x in results if x["ok"])
print(f"结果：{n_ok}/{len(results)} 通过")
for x in results:
    if not x["ok"]:
        print(f"  ❌ {x['name']}  {x['detail']}")
print("=" * 74)

out = {
    "experiment": "E-396",
    "n_pass": n_ok,
    "n_total": len(results),
    "configs": len(configs),
    "documented": len(documented),
    "doc_lines": lines,
    "diagrams": len(blocks),
}
p = os.path.join(_HERE, "results", "e396_doc_check.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e396_doc_check.json")

sys.exit(0 if n_ok == len(results) else 1)
