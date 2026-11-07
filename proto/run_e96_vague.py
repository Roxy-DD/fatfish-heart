"""
E-96 "长而空"（vague_summary）——规则测不出的信息密度问题

E-75 的 vague_summary 规则：句长>45 且无专名无数字无日期 且空泛词>=2
这是纯长度+词表启发式，E-73 里漏掉了 R20 这条"长而空"。

本实验：构造一批"长而空" vs "长但有信息"的对照句，
看规则能否区分，以及模型的判断是否可靠（决定要不要模型精筛）。
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
RESULTS = os.path.join(_HERE, "results")
TASK = os.path.join(_HERE, "work", "e96")
os.makedirs(TASK, exist_ok=True)

# 长而空（信息密度零）
EMPTY = [
    "今天开完会之后感觉整体方向还行，但是细节还需要再讨论一下，"
    "特别是关于接下来的排期和资源分配，可能需要再找几个人对一下",
    "最近这段时间感觉状态还可以，就是有些事情需要再想想，"
    "具体怎么弄还没想清楚，到时候再说吧",
    "大家讨论了半天，总体上觉得方案还可以，但有些地方还需要再完善，"
    "回头再看看情况",
    "这件事我考虑了一下，觉得可以试试，不过也要看后续进展，"
    "反正先做着看看",
]
# 长但有信息（有具体内容）
FULL = [
    "这个项目的服务器部署在阿里云华东区，用的是 16 核 64G 的机器，"
    "数据库是 MySQL 8.0，每天凌晨两点做全量备份",
    "我女儿在城南路 18 号的向日葵幼儿园上中班，班主任是王老师，"
    "每天下午四点半放学",
    "客户那边反馈说登录页在 iOS 17 上会白屏，需要我们这周五之前修复，"
    "已经派给了前端的小李",
    "体检报告出来了，血压偏高 150/95，医生让我少吃盐，"
    "下个月 10 号复查",
]


def main():
    rows = []
    for i, t in enumerate(EMPTY):
        rows.append({"rid": f"E{i:02d}", "memory": t, "kind": "empty"})
    for i, t in enumerate(FULL):
        rows.append({"rid": f"F{i:02d}", "memory": t, "kind": "full"})

    with open(os.path.join(TASK, "corpus.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)

    from capability import full_check, _chk_vague_summary

    print("=" * 92)
    print("E-96 长而空 vs 长但有信息")
    print("=" * 92)
    print(f"\n{len(rows)} 条\n")
    print("-" * 92)
    print(f"{'rid':<6}{'类型':<8}{'vague_summary规则':<20}{'其他缺陷':<24}{'内容'}")
    print("-" * 92)
    for r in rows:
        t = r["memory"]
        vs = _chk_vague_summary(t)
        other = [x for x in full_check(t) if x != "vague_summary"]
        mark = "✓判空泛" if vs else "·"
        print(f"{r['rid']:<6}{r['kind']:<8}{mark:<20}"
              f"{'/'.join(other) or '-':<24}{t[:28]}")
    print("-" * 92)

    # 统计
    tp = sum(1 for r in rows if r["kind"] == "empty" and
             _chk_vague_summary(r["memory"]))
    fn = sum(1 for r in rows if r["kind"] == "empty" and
             not _chk_vague_summary(r["memory"]))
    fp = sum(1 for r in rows if r["kind"] == "full" and
             _chk_vague_summary(r["memory"]))
    tn = sum(1 for r in rows if r["kind"] == "full" and
             not _chk_vague_summary(r["memory"]))

    print(f"\n  TP={tp}（长空判空泛） FN={fn}（长空漏） FP={fp}（长有信息误判空泛） TN={tn}")
    if fn or fp:
        print("  → 规则有漏/误，需要模型精筛")
    else:
        print("  → 规则完美区分")

    out = {"experiment": "E-96", "n": len(rows),
           "tp": tp, "fn": fn, "fp": fp, "tn": tn}
    with open(os.path.join(RESULTS, "e96_vague_summary.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e96_vague_summary.json')}")


if __name__ == "__main__":
    main()
