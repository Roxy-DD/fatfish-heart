"""E-445 生命周期图文档回归（只读）

防止四类问题复发：

  ① 图里的 census 数字（tag 数/事件数/行数）又漂了，但没人发现
     —— E-444 实测：同一批数字在半小时内从 80 漂到 82

  ② 有人又把 census 数字写回 Mermaid 代码块里
     —— 结构与数据必须分离，图只写关系

  ③ AUTO-GENERATED 标记块被破坏 / 重复 / 消失
     —— 块没了，E-444 的同步就静默失效

  ④ Mermaid 语法坏掉（subgraph/end 不配对、块头非法）
     —— 贴进思源渲染不出来

★ 不检查"图好不好看"，只检查"会不会骗人"。
"""
from __future__ import annotations

import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
DOC = os.path.join(_ROOT, "docs", "78 生命周期图-环管群.md")

sys.path.insert(0, _HERE)

failures = []
passes = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passes
    if cond:
        passes += 1
        print(f"  ✅ {name}" + (f"  {detail}" if detail else ""))
    else:
        failures.append(f"{name} {detail}")
        print(f"  ❌ {name}" + (f"  {detail}" if detail else ""))


print("=" * 70)
print("E-445 生命周期图文档回归")
print("=" * 70)

# ---------------------------------------------------------------- ① 文件存在
print("\n① 文件与结构")
if not os.path.exists(DOC):
    print(f"  ❌ 文档不存在: {DOC}")
    sys.exit(1)
src = open(DOC, encoding="utf-8").read()
check("文档存在", True, f"{len(src)} 字符")

# ---------------------------------------------------------------- ② 三层都讲到
print("\n② 环/管/群三层都在")
for layer in ("环", "管", "群"):
    check(f"提到「{layer}」", layer in src)

# 关键章节
for sec in ("## 1. 环", "## 3. 管", "## 5. 群"):
    check(f"有章节 {sec}", sec in src)

# ---------------------------------------------------------------- ③ AUTO-GENERATED 块
print("\n③ AUTO-GENERATED 块")
BEGIN = "<!-- AUTO-GENERATED: 由 proto/run_e444_sync_lifecycle_doc.py 生成，勿手改 -->"
END = "<!-- /AUTO-GENERATED -->"
n_begin, n_end = src.count(BEGIN), src.count(END)
check("起始标记唯一", n_begin == 1, f"{n_begin} 处")
check("结束标记唯一", n_end == 1, f"{n_end} 处")

if n_begin == 1 and n_end == 1:
    i, j = src.index(BEGIN), src.index(END)
    check("标记顺序正确", i < j)
    block = src[i:j + len(END)]
    check("块内含表格", "| 指标 |" in block)
    check("块内含测量时刻", re.search(r"2026-\d\d-\d\d \d\d:\d\d", block) is not None)

    # ---- 块内数字必须与实时数据一致 ----
    try:
        import run_e444_sync_lifecycle_doc as sync
        live = sync.collect()
        ok = True
        detail = []
        for pat, want, label in [
            (r"git tag 总数 \| (\d+)", live["tags"], "tag 数"),
            (r"登记版本 \| (\d+)", live["registered"], "登记数"),
            (r"`episodes` 行数 \| (\d+)", live["episodes"], "episodes"),
            (r"有效条数 \| (\d+)", live["env_valid"], "环境事件"),
            (r"HEAD 所在 tag \| (\S+)", live["head_tag"], "HEAD tag"),
        ]:
            m = re.search(pat, block)
            if not m:
                ok = False
                detail.append(f"{label} 匹配不到")
            elif str(m.group(1)).strip() != str(want).strip():
                ok = False
                detail.append(f"{label}: 文档 {m.group(1)} ≠ 实测 {want}")
        check("块内数字与实测一致", ok, "；".join(detail))

        # 回归套件条目数也要对
        m = re.search(r"= \*\*(\d+) 项\*\*", block)
        check("回归套件数量与 iterate.py 一致",
              bool(m) and int(m.group(1)) == live["n_total"],
              f"文档 {m.group(1) if m else '?'} / 实测 {live['n_total']}")
    except Exception as e:
        check("能导入同步脚本并比对", False, f"{type(e).__name__}: {e}")
else:
    check("块内数字与实测一致", False, "标记块不完整，跳过比对")

# ---------------------------------------------------------------- ④ Mermaid 块
print("\n④ Mermaid 块合法性")
blocks = re.findall(r"```mermaid\n([\s\S]*?)```", src)
check("至少 8 张图", len(blocks) >= 8, f"{len(blocks)} 张")

LEGAL_HEADS = ("flowchart", "stateDiagram-v2", "sequenceDiagram", "erDiagram",
               "graph", "classDiagram", "mindmap")

bad_head, bad_pair, census = [], [], []
for k, b in enumerate(blocks, 1):
    head = b.strip().split("\n")[0].strip()
    if not head.startswith(LEGAL_HEADS):
        bad_head.append((k, head[:40]))

    if head.startswith(("flowchart", "graph")):
        n_sub = len(re.findall(r"^\s*subgraph\b", b, re.M))
        n_end = len(re.findall(r"^\s*end\s*$", b, re.M))
        if n_sub != n_end:
            bad_pair.append((k, n_sub, n_end))

    # census 数字不许出现在图里：形如 "N 个 git tag" / "实查 N 条" / "缺 N 个"
    for line in b.split("\n"):
        if re.search(r"(实查|缺)\s*\d+\s*(条|个)", line) or \
           re.search(r"\d+\s*个\s*(git\s*)?tag", line):
            census.append((k, line.strip()[:70]))

check("所有块头合法", not bad_head, str(bad_head[:3]))
check("subgraph/end 配对", not bad_pair, str(bad_pair[:3]))
check("图里没有 census 数字", not census, str(census[:3]))

# ---------------------------------------------------------------- ⑤ 概念一致性
print("\n⑤ 概念表述一致")
# "环" 有两种粒度这件事必须写清楚，否则读者会把会话和版本混为一谈
check("说明环有两个粒度", "两个粒度" in src or "有两级" in src)
check("区分会话级与版本级", "会话级" in src and "版本级" in src)
# 诚实标注：群层缺口必须写出来，不能只画好看的图
check("标注群层未实现的主张4", "主张 4" in src or "主张4" in src)
check("标注待裁定项", "待你裁定" in src or "待用户裁定" in src)

# ---------------------------------------------------------------- 汇总
print("\n" + "=" * 70)
print(f"结果：{passes} 通过 / {len(failures)} 失败")
if failures:
    print("\n失败项：")
    for f in failures:
        print("  ·", f)
print("=" * 70)

# 记录结果（与其它实验一致，落 results/）
import json
out = {"experiment": "E-445", "n_pass": passes,
       "n_fail": len(failures), "n_blocks": len(blocks),
       "failures": failures}
p = os.path.join(_HERE, "results", "e445_lifecycle_doc.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)

sys.exit(0 if not failures else 1)
