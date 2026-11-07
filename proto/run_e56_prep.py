"""
E-56 干扰假说（难度推到边界版）

E-55 全部 1.0000 —— 任务太简单，没有任何方差。
原因：实体名差异大、句式短、干扰项只有个位数。

E-56 把难度同时推到三个方向（这是"找边界"而不是"预设门槛"）：
  ① 实体更相似：同姓 + 近形名（岑令仪 / 岑令义 / 岑今仪）—— 一字之差
  ② 干扰更多：0 / 10 / 30 / 60 条
  ③ 数字改动：项目号相近（TP437 / TP438 / TP437A）

四个正交条件（每个都保证答案唯一）：
  N0     1 条（天花板）
  S10    10 条，**近形实体**（岑令仪/岑令义/岑今仪 混在上下文）
  D10    10 条，**远形实体**（岑令仪/郗望舒/缑砚清）
  S30    30 条，近形实体

判据（不预设）：
  若 S10 < D10 → 相似度是干扰主因
  若 S30 < S10 → 条数累积效应
"""
from __future__ import annotations

import json
import os
import random

_HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(_HERE, "results")
TASK = os.path.join(_HERE, "work", "e56_task")
os.makedirs(TASK, exist_ok=True)

# 近形字：同姓 + 只差一个字的 given name
SURNAME = ["岑", "宓", "郗", "逄", "禚"]
# 近形名组（每组内部只差一字）
GIVEN_NEAR = [
    ["令仪", "令义", "今仪", "令议"],
    ["斯年", "斯念", "思年", "斯黏"],
    ["望舒", "望书", "忘舒", "望姝"],
    ["之桓", "之恒", "之环", "之垣"],
    ["沐宸", "沐辰", "木宸", "沐晨"],
]
PROJ = ["TP4", "TP5", "KX7", "QM3", "VF9", "RN2"]
DEPT = ["甲组", "乙组", "丙组", "丁组", "戊组"]

N_ITEMS = 20


def build_pool(seed=101):
    """构造一批主体唯一的记忆"""
    rng = random.Random(seed)
    projs = []
    for p in PROJ:
        for n in range(10, 90):
            projs.append(f"{p}{n}")
    rng.shuffle(projs)
    persons = []
    for s in SURNAME:
        for grp in GIVEN_NEAR:
            for g in grp:
                persons.append(s + g)
    rng.shuffle(persons)
    depts = [f"{d}{i}" for d in DEPT for i in range(8)]
    rng.shuffle(depts)

    pool, pi, pei, di = [], 0, 0, 0
    for i in range(N_ITEMS):
        t = i % 2
        pr = projs[pi]; pi += 1
        p = persons[pei]; pei += 1
        if t == 0:
            text = f"{p} 负责 {pr} 这个项目，交付时间是下个季度末"
            q, a = f"谁负责 {pr}？", p
            subj = ("proj", pr)
        else:
            text = f"{p} 提出把 {pr} 的评审周期从两周缩短到一周"
            q, a = f"谁提出缩短 {pr} 的评审周期？", p
            subj = ("proj", pr)
        pool.append({"id": i, "t": t, "text": text, "q": q, "a": a,
                     "subj": subj, "p": p, "proj": pr})
    return pool, persons, projs


def near_person(p, persons):
    """从池里找与 p 同姓、given 只差一字的"""
    surname, given = p[0], p[1:]
    same = [x for x in persons if x[0] == surname and x != p]
    # 按编辑距离近的优先
    def dist(a, b):
        return sum(1 for c1, c2 in zip(a, b) if c1 != c2) + abs(len(a) - len(b))
    same.sort(key=lambda x: dist(given, x[1:]))
    return same


def far_person(p, persons):
    surname = p[0]
    return [x for x in persons if x[0] != surname]


def build_cond(pool, persons, projs, mode, n_distract, seed):
    rng = random.Random(seed)
    rows = []
    for it in pool:
        if n_distract == 0:
            docs = [it["text"]]
        else:
            docs = [it["text"]]
            used_proj = {it["proj"]}
            used_person = {it["p"]}
            if mode == "SIM":
                cands = near_person(it["p"], persons)
                # 造"近形"干扰记忆：同样的句式、不同的近形人名和项目号
                k = 0
                while len(docs) < n_distract + 1 and k < len(cands):
                    np_ = cands[k]; k += 1
                    if np_ in used_person:
                        continue
                    # 找一个没用过的项目号
                    pj = None
                    for cand in projs:
                        if cand not in used_proj:
                            pj = cand
                            used_proj.add(cand)
                            break
                    if pj is None:
                        break
                    used_person.add(np_)
                    if it["t"] == 0:
                        docs.append(f"{np_} 负责 {pj} 这个项目，交付时间是下个季度末")
                    else:
                        docs.append(f"{np_} 提出把 {pj} 的评审周期从两周缩短到一周")
            else:
                cands = far_person(it["p"], persons)
                rng.shuffle(cands)
                k = 0
                while len(docs) < n_distract + 1 and k < len(cands):
                    np_ = cands[k]; k += 1
                    if np_ in used_person:
                        continue
                    pj = None
                    for cand in projs:
                        if cand not in used_proj:
                            pj = cand
                            used_proj.add(cand)
                            break
                    if pj is None:
                        break
                    used_person.add(np_)
                    if it["t"] == 0:
                        docs.append(f"{np_} 负责 {pj} 这个项目，交付时间是下个季度末")
                    else:
                        docs.append(f"{np_} 提出把 {pj} 的评审周期从两周缩短到一周")
        rng.shuffle(docs)
        block = "\n".join(f"[{j}] {t}" for j, t in enumerate(docs, 1))
        rows.append({"id": it["id"], "context": block, "question": it["q"],
                     "answer": it["a"], "n_docs": len(docs)})
    return rows


CONDS = [
    ("N0",   "NONE", 0,  1),
    ("S10",  "SIM",  9,  11),
    ("D10",  "DIV",  9,  21),
    ("S30",  "SIM",  29, 31),
]


def main():
    pool, persons, projs = build_pool()
    print("=" * 88)
    print("E-56 干扰假说（难度推到边界：近形实体 + 多条干扰）")
    print("=" * 88)
    print(f"\n池 {len(pool)} 条，可用人名 {len(persons)}，项目号 {len(projs)}\n")

    print("-" * 88)
    print("样例：近形干扰长什么样")
    print("-" * 88)
    for name, mode, nd, sd in CONDS:
        rows = build_cond(pool, persons, projs, mode, nd, sd)
        r = rows[0]
        print(f"\n【{name}】{r['n_docs']} 条，{len(r['context'])} 字")
        for line in r["context"].split("\n")[:5]:
            print(f"   {line}")
        if r["n_docs"] > 5:
            print(f"   ...（共 {r['n_docs']} 条）")
        print(f"   Q: {r['question']}   A: {r['answer']}")

    print("\n" + "-" * 88)
    print("生成任务包")
    print("-" * 88)
    meta = {}
    for name, mode, nd, sd in CONDS:
        rows = build_cond(pool, persons, projs, mode, nd, sd)
        out = [{"qid": f"{name}-{i:03d}", "context": r["context"],
                "question": r["question"]} for i, r in enumerate(rows)]
        with open(os.path.join(TASK, f"{name}.json"), "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        key = {f"{name}-{i:03d}": {"answer": r["answer"], "id": r["id"]}
               for i, r in enumerate(rows)}
        with open(os.path.join(TASK, f"{name}_key.json"), "w", encoding="utf-8") as f:
            json.dump(key, f, ensure_ascii=False, indent=1)
        avg = sum(len(r["context"]) for r in rows) / len(rows)
        avgnd = sum(r["n_docs"] for r in rows) / len(rows)
        meta[name] = {"n_q": len(rows), "avg_docs": round(avgnd, 1),
                      "avg_ctx": round(avg, 1)}
        print(f"  {name:<6}{len(rows):>3}题  平均 {avgnd:.1f} 条/题  {avg:.0f} 字")

    with open(os.path.join(RESULTS, "e56_meta.json"), "w", encoding="utf-8") as f:
        json.dump({"experiment": "E-56", "conds": meta}, f, ensure_ascii=False, indent=1)
    print(f"\n→ {TASK}")


if __name__ == "__main__":
    main()
