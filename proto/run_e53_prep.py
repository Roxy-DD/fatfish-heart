"""
E-53 端到端验证：压缩后的内容，模型还能读出信息吗

E-52 只证明"选中的条数变多"，没证明"信息传到了模型"。
本实验补上这一段：把压缩后的内容交给真实模型，看它能否答对。

关键设计（防作弊）：
  记忆内容用**合成实体**（随机生成的人名/编号/项目名），
  模型不可能靠先验知识答对，只能从上下文里读。
  这是本实验可信的前提。

五个压缩档位（对同一条记忆）：
  FULL   完整原文
  P60    前 60%
  P30    前 30%
  P10    前 10%
  C3     前 3 字

每题问的是"该记忆里的关键事实"，答案是一个合成实体串。
评分：答案是否包含该实体串（大小写/空格归一化）。

输出：每个档位的正确率。目的是定位"信息在哪一档开始丢失"。
"""
from __future__ import annotations

import json
import os
import random
import string
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(_HERE, "results")
WORK = os.path.join(_HERE, "work", "e53")
os.makedirs(WORK, exist_ok=True)

# 合成实体池（刻意不常见，避免先验知识）
SURNAME = ["岑", "宓", "郗", "逄", "禚", "阚", "胥", "钭", "乜", "缑"]
GIVEN = ["令仪", "斯年", "望舒", "之桓", "沐宸", "砚清", "辞白", "叙礼", "澹宁", "屿舟"]
PROJ = ["KX7", "QM3", "VF9", "RN2", "WB5", "ZL8", "TP4", "HY6", "GD1", "CS0"]
DEPT = ["乙组", "丙组", "戊组", "庚组", "壬组"]


def make_person(rng):
    return rng.choice(SURNAME) + rng.choice(GIVEN)


def build_items(n=20, seed=7):
    """每条记忆一个事实 + 一个问题 + 标准答案"""
    rng = random.Random(seed)
    items = []
    for i in range(n):
        p = make_person(rng)
        proj = rng.choice(PROJ) + str(rng.randint(10, 99))
        dept = rng.choice(DEPT)
        kind = i % 4
        if kind == 0:
            text = f"{p} 负责 {proj} 这个项目，交付时间是下个季度末"
            q = f"谁负责 {proj}？"
            ans = p
        elif kind == 1:
            text = f"{proj} 隶属于 {dept}，目前正在做接口联调"
            q = f"{proj} 属于哪个组？"
            ans = dept
        elif kind == 2:
            text = f"{dept} 的负责人是 {p}，同时兼管预算审批"
            q = f"{dept} 的负责人是谁？"
            ans = p
        else:
            text = f"{p} 提出把 {proj} 的评审周期从两周缩短到一周"
            q = f"谁提出缩短 {proj} 的评审周期？"
            ans = p
        items.append({"id": i, "text": text, "question": q, "answer": ans})
    return items


def compress(text, level):
    if level == "FULL":
        return text
    if level == "P60":
        return text[:max(2, int(len(text) * 0.60))]
    if level == "P30":
        return text[:max(2, int(len(text) * 0.30))]
    if level == "P10":
        return text[:max(2, int(len(text) * 0.10))]
    if level == "C3":
        return text[:3]
    raise ValueError(level)


LEVELS = ["FULL", "P60", "P30", "P10", "C3"]


def main():
    items = build_items(20)
    print("=" * 92)
    print("E-53 端到端压缩验证 —— 生成测试包")
    print("=" * 92)
    print(f"\n{len(items)} 条合成记忆，{len(LEVELS)} 个压缩档位\n")

    packs = {}
    for lv in LEVELS:
        rows = []
        for it in items:
            ctx = compress(it["text"], lv)
            rows.append({"id": it["id"], "context": ctx,
                         "question": it["question"], "answer": it["answer"],
                         "ctx_len": len(ctx), "full_len": len(it["text"])})
        packs[lv] = rows

    # 打印样例
    print("-" * 92)
    print("样例（第 0 条在各档位下剩什么）")
    print("-" * 92)
    for lv in LEVELS:
        r = packs[lv][0]
        print(f"  {lv:<6}[{r['ctx_len']:>3}/{r['full_len']}字]  {r['context']}")
        print(f"        Q: {r['question']}   A: {r['answer']}")

    print("\n" + "-" * 92)
    print("各档位的平均上下文长度")
    print("-" * 92)
    stats = {}
    for lv in LEVELS:
        avg = sum(r["ctx_len"] for r in packs[lv]) / len(packs[lv])
        full = sum(r["full_len"] for r in packs[lv]) / len(packs[lv])
        stats[lv] = {"avg_ctx_len": round(avg, 1), "avg_full_len": round(full, 1)}
        print(f"  {lv:<6}{avg:>6.1f} 字  （原 {full:.1f} 字，{avg/full*100:.0f}%）")

    # 导出：每个档位拆成 2 批（每批 10 题），供 subagent 作答
    for lv in LEVELS:
        rows = packs[lv]
        for bi in range(2):
            batch = rows[bi * 10:(bi + 1) * 10]
            path = os.path.join(WORK, f"pack_{lv}_{bi}.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(batch, f, ensure_ascii=False, indent=1)

    meta = {"experiment": "E-53", "levels": LEVELS, "n_items": len(items),
            "stats": stats,
            "note": "合成实体，防先验知识；每档拆2批各10题"}
    with open(os.path.join(WORK, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    with open(os.path.join(RESULTS, "e53_meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)

    print(f"\n已生成测试包 → {WORK}")
    print("  pack_<档位>_<0|1>.json，每包 10 题")


if __name__ == "__main__":
    main()
