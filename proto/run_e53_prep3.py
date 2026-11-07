"""
E-53 v3 端到端压缩验证 —— 让答案位于随机位置

v2 的缺陷：答案总在文本开头，截断后答案还在（"阚望舒 负责"）→ 只测出数据构造。

v3 修正：
  每条记忆由 3 个子句拼成，**答案所在的子句位置随机**（首/中/尾）。
  这样"朴素截断"会在答案不在开头时丢失关键信息。

对比两种压缩（同等字符预算约 55%）：
  TRUNC   朴素截断前 55%
  KEEP    保留 3 个子句中的 2 个（随机挑，但保证不总是前两个）
          更贴切地模拟"摘要/抽取式压缩"

测试：注入 5 条候选（1 目标 + 4 干扰），问目标的事实。
防作弊：全合成实体；可答"无法确定"（记0分）。
"""
from __future__ import annotations

import json
import os
import random

_HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(_HERE, "results")
WORK = os.path.join(_HERE, "work", "e53v3")
os.makedirs(WORK, exist_ok=True)

SURNAME = ["岑", "宓", "郗", "逄", "禚", "阚", "胥", "钭", "乜", "缑"]
GIVEN = ["令仪", "斯年", "望舒", "之桓", "沐宸", "砚清", "辞白", "叙礼", "澹宁", "屿舟"]
PROJ = ["KX7", "QM3", "VF9", "RN2", "WB5", "ZL8", "TP4", "HY6", "GD1", "CS0"]
DEPT = ["乙组", "丙组", "戊组", "庚组", "壬组"]
FILLER = ["这个阶段进度平稳", "上周开了两次对齐会", "相关资料已归档",
          "后续还要再评估", "目前没有明显风险", "预算还在审批中",
          "对接方反馈良好", "文档已经更新到最新版"]

# 四种条件：答案位置 × 压缩方式
POS = ["head", "mid", "tail"]
COMP = ["TRUNC", "KEEP"]

LEVELS = [f"{c}_{p}" for c in COMP for p in POS] + ["FULL"]
N_ITEM = 36
N_DISTRACT = 4


def build_items(seed=17):
    rng = random.Random(seed)
    items = []
    used = set()
    for i in range(N_ITEM):
        while True:
            p = rng.choice(SURNAME) + rng.choice(GIVEN)
            if p not in used:
                used.add(p)
                break
        proj = rng.choice(PROJ) + str(rng.randint(10, 99))
        dept = rng.choice(DEPT)
        kind = i % 4
        if kind == 0:
            key = f"{p} 负责 {proj}"
            q, ans = f"谁负责 {proj}？", p
        elif kind == 1:
            key = f"{proj} 隶属于 {dept}"
            q, ans = f"{proj} 属于哪个组？", dept
        elif kind == 2:
            key = f"{dept} 的负责人是 {p}"
            q, ans = f"{dept} 的负责人是谁？", p
        else:
            key = f"{p} 提出缩短 {proj} 的评审周期"
            q, ans = f"谁提出缩短 {proj} 的评审周期？", p
        f1, f2 = rng.sample(FILLER, 2)
        items.append({"id": i, "key": key, "f1": f1, "f2": f2,
                      "question": q, "answer": ans,
                      "pos": POS[i % 3]})
    return items


def assemble(it):
    """按 pos 拼成 3 句：key 在首/中/尾"""
    k, a, b = it["key"], it["f1"], it["f2"]
    if it["pos"] == "head":
        return [k, a, b]
    if it["pos"] == "mid":
        return [a, k, b]
    return [a, b, k]


def compress(clauses, mode):
    """mode: FULL / TRUNC / KEEP"""
    if mode == "FULL":
        return clauses
    if mode == "TRUNC":
        text = "，".join(clauses)
        return [text[:max(2, int(len(text) * 0.55))]]
    if mode == "KEEP":
        # 抽取式：保留 2 句（约 2/3 字符），丢弃 1 句
        # 丢弃规则固定为"丢最后一句"——这是朴素做法，不偷看答案
        return clauses[:2]
    raise ValueError(mode)


def build_pack(items, mode, pos_filter=None):
    rng = random.Random(123)
    sel = [it for it in items if pos_filter is None or it["pos"] == pos_filter]
    rows = []
    for it in sel:
        others = [x for x in items if x["id"] != it["id"]]
        distr = rng.sample(others, N_DISTRACT)
        docs = [("target", assemble(it))] + [("distract", assemble(d)) for d in distr]
        rng.shuffle(docs)
        block, clen, flen = [], 0, 0
        for j, (src, cl) in enumerate(docs, 1):
            c = compress(cl, mode)
            block.append(f"[{j}] " + "，".join(c))
            clen += len("，".join(c))
            flen += len("，".join(cl))
        rows.append({"id": it["id"], "pos": it["pos"], "context": "\n".join(block),
                     "question": it["question"], "answer": it["answer"],
                     "ctx_len": clen, "full_len": flen})
    return rows


def main():
    items = build_items()
    print("=" * 92)
    print("E-53 v3 端到端压缩验证 —— 答案位置随机")
    print("=" * 92)
    print(f"\n{N_ITEM} 条记忆；每题 {1+N_DISTRACT} 条候选\n")

    # 汇总：每种 (压缩方式 × 答案位置) 的包
    plan = []
    for m in ["FULL", "TRUNC", "KEEP"]:
        for p in (POS if m != "FULL" else POS):
            plan.append((m, p))

    print("-" * 92)
    print("样例（同一条记忆，答案在中间时）")
    print("-" * 92)
    demo = next(it for it in items if it["pos"] == "mid")
    cl = assemble(demo)
    print(f"  原文: {'，'.join(cl)}")
    for m in ["FULL", "TRUNC", "KEEP"]:
        print(f"  {m:<6}: {'，'.join(compress(cl, m))}")
    print(f"  Q: {demo['question']}  A: {demo['answer']}")

    print("\n" + "-" * 92)
    print("生成测试包")
    print("-" * 92)
    meta_rows = []
    for m, p in plan:
        rows = build_pack(items, m, None if m == "FULL" else p)
        # 每包最多 12 题
        for bi in range(0, len(rows), 12):
            batch = rows[bi:bi + 12]
            fn = f"{m}_{p}_{bi//12}.json"
            with open(os.path.join(WORK, fn), "w", encoding="utf-8") as f:
                json.dump(batch, f, ensure_ascii=False, indent=1)
        avg = sum(r["ctx_len"] for r in rows) / len(rows) if rows else 0
        full = sum(r["full_len"] for r in rows) / len(rows) if rows else 0
        meta_rows.append({"mode": m, "pos": p, "n": len(rows),
                          "avg_ctx": round(avg, 1), "avg_full": round(full, 1),
                          "ratio": round(avg / full, 3) if full else 0})
        print(f"  {m:<6}{p:<6} {len(rows):>3}题  上下文 {avg:>6.1f}/{full:.1f} 字 "
              f"({avg/full*100:>3.0f}%)")

    meta = {"experiment": "E-53", "version": 3, "design": "答案位置随机×压缩方式",
            "n_items": N_ITEM, "n_distract": N_DISTRACT, "rows": meta_rows}
    with open(os.path.join(RESULTS, "e53_meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    print(f"\n生成完毕 → {WORK}")


if __name__ == "__main__":
    main()
