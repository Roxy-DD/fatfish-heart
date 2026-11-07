"""
E-57 关键区分：目标在候选里 vs 目标不在候选里

E-55 / E-56 连续三轮全 1.0000 —— 说明"目标存在，让模型找"这个任务太简单。
模型在 20 条近形干扰（郗望姝/郗望舒/郗忘舒）里仍能逐字核对正确。

但真实检索的关键失败模式不是这个。真实失败是：
  **检索到了"看起来相关但他根本不知道"的东西，模型还硬答。**

这是分诊里的核心区别：
  "病人有救" vs "没有对应科室" —— 后者必须说"我处理不了"。

四个条件：
  HIT_1      目标在 1 条候选里（易）
  HIT_10     目标在 10 条干扰里（已证简单）
  MISS_0     候选里**完全没有**该主体，但干扰与它**高度相似**（陷阱）
  MISS_NEAR  候选里有**近形但不同**的主体（更狠的陷阱：郗望姝在库，问郗望舒）

判据：
  HIT 类应该接近 1.0
  MISS 类如果显著低于 1.0 → **模型会"强行作答"，这是真实风险**
  这正是"记忆系统注入错误内容"时会发生的事。
"""
from __future__ import annotations

import json
import os
import random

_HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(_HERE, "results")
TASK = os.path.join(_HERE, "work", "e57_task")
os.makedirs(TASK, exist_ok=True)

SURNAME = ["岑", "宓", "郗", "逄", "禚"]
GIVEN_NEAR = [
    ["令仪", "令义", "今仪", "令议"],
    ["斯年", "斯念", "思年", "斯黏"],
    ["望舒", "望书", "忘舒", "望姝"],
    ["之桓", "之恒", "之环", "之垣"],
    ["沐宸", "沐辰", "木宸", "沐晨"],
]
PROJ = ["TP4", "TP5", "KX7", "QM3", "VF9", "RN2"]

N_ITEMS = 20


def build_pool(seed=201):
    rng = random.Random(seed)
    projs = [f"{p}{n}" for p in PROJ for n in range(10, 90)]
    rng.shuffle(projs)
    persons = [s + g for s in SURNAME for grp in GIVEN_NEAR for g in grp]
    rng.shuffle(persons)
    pool, pi, pei = [], 0, 0
    for i in range(N_ITEMS):
        pr = projs[pi]; pi += 1
        p = persons[pei]; pei += 1
        pool.append({"id": i,
                     "text": f"{p} 负责 {pr} 这个项目，交付时间是下个季度末",
                     "q": f"谁负责 {pr}？", "a": p, "p": p, "proj": pr})
    return pool, persons, projs


def near_of(p, persons):
    surname, given = p[0], p[1:]
    same = [x for x in persons if x[0] == surname and x != p]
    def d(a, b):
        return sum(1 for c1, c2 in zip(a, b) if c1 != c2) + abs(len(a) - len(b))
    same.sort(key=lambda x: d(given, x[1:]))
    return same


def make_filler(persons, projs, used_p, used_pr, k, rng):
    """造 k 条无关但同句式的干扰"""
    out = []
    cands = [x for x in persons if x not in used_p]
    rng.shuffle(cands)
    for np_ in cands[:k]:
        pj = None
        for c in projs:
            if c not in used_pr:
                pj = c; used_pr.add(c); break
        if pj is None:
            break
        used_p.add(np_)
        out.append(f"{np_} 负责 {pj} 这个项目，交付时间是下个季度末")
    return out


def build_cond(pool, persons, projs, cond, seed):
    rng = random.Random(seed)
    rows = []
    for it in pool:
        if cond == "HIT_1":
            docs = [it["text"]]
            q, gold = it["q"], it["a"]
        elif cond == "HIT_10":
            used_p, used_pr = {it["p"]}, {it["proj"]}
            docs = [it["text"]] + make_filler(persons, projs, used_p, used_pr, 9, rng)
            q, gold = it["q"], it["a"]
        elif cond == "MISS_0":
            # 目标主体完全不在候选里，但要问它 —— 干扰与该主体无关
            used_p, used_pr = set(), set()
            docs = make_filler(persons, projs, used_p, used_pr, 10, rng)
            # 问一个库中不存在的项目号
            ghost = it["proj"] + "X"
            q, gold = f"谁负责 {ghost}？", "无法确定"
        elif cond == "MISS_NEAR":
            # 库里放"近形但不同"的人占着同一个项目 —— 最狠的陷阱
            p = it["p"]
            nps = near_of(p, persons)
            near_name = nps[0] if nps else p + "X"
            # 把目标替换成近形名，用同一项目号
            docs = [f"{near_name} 负责 {it['proj']} 这个项目，交付时间是下个季度末"]
            used_p, used_pr = {near_name}, {it["proj"]}
            docs += make_filler(persons, projs, used_p, used_pr, 9, rng)
            q = it["q"]          # 问的还是同一个项目
            # 正确答案是库里那个"近形名"，不是原来的 p
            gold = near_name
        rng.shuffle(docs)
        block = "\n".join(f"[{j}] {t}" for j, t in enumerate(docs, 1))
        rows.append({"id": it["id"], "context": block, "question": q,
                     "answer": gold, "n_docs": len(docs)})
    return rows


CONDS = [("HIT_1", 1), ("HIT_10", 11), ("MISS_0", 21), ("MISS_NEAR", 31)]


def main():
    pool, persons, projs = build_pool()
    print("=" * 88)
    print("E-57 关键区分：目标在候选里 vs 不在候选里")
    print("=" * 88)

    # 预检：MISS_NEAR 必须真的"近形但不同"
    print("\n" + "-" * 88)
    print("预检：MISS_NEAR 的近形替换是否正确")
    print("-" * 88)
    bad = 0
    for it in pool[:6]:
        nps = near_of(it["p"], persons)
        nn = nps[0] if nps else "?"
        ok = nn != it["p"]
        if not ok:
            bad += 1
        print(f"  原 {it['p']} → 替换为 {nn}  {'OK' if ok else 'BAD'}")
    if bad:
        print(f"  ⚠ {bad} 条替换失败")

    print("\n" + "-" * 88)
    print("样例")
    print("-" * 88)
    for name, sd in CONDS:
        rows = build_cond(pool, persons, projs, name, sd)
        r = rows[0]
        print(f"\n【{name}】{r['n_docs']} 条  {len(r['context'])} 字")
        for line in r["context"].split("\n")[:4]:
            print(f"   {line}")
        if r["n_docs"] > 4:
            print(f"   ...（共 {r['n_docs']} 条）")
        print(f"   Q: {r['question']}")
        print(f"   标准答案: {r['answer']}")

    print("\n" + "-" * 88)
    print("生成任务包")
    print("-" * 88)
    meta = {}
    for name, sd in CONDS:
        rows = build_cond(pool, persons, projs, name, sd)
        out = [{"qid": f"{name}-{i:03d}", "context": r["context"],
                "question": r["question"]} for i, r in enumerate(rows)]
        with open(os.path.join(TASK, f"{name}.json"), "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        key = {f"{name}-{i:03d}": {"answer": r["answer"], "id": r["id"]}
               for i, r in enumerate(rows)}
        with open(os.path.join(TASK, f"{name}_key.json"), "w", encoding="utf-8") as f:
            json.dump(key, f, ensure_ascii=False, indent=1)
        avg = sum(len(r["context"]) for r in rows) / len(rows)
        meta[name] = {"n_q": len(rows), "avg_ctx": round(avg, 1)}
        print(f"  {name:<12}{len(rows):>3}题  {avg:.0f}字")

    with open(os.path.join(RESULTS, "e57_meta.json"), "w", encoding="utf-8") as f:
        json.dump({"experiment": "E-57", "conds": meta}, f, ensure_ascii=False, indent=1)
    print(f"\n→ {TASK}")


if __name__ == "__main__":
    main()
