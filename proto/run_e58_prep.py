"""
E-58 时间冲突：过时信息会不会被当成当前事实

为什么换到这个问题：
  E-55/56/57 连续三轮全 1.0 —— 我测的是"字符串匹配"（近形名、找实体），
  这对当前模型太简单。假设错了，不是方法不够。

真正的难点（也是用户最早提出的关切）：
  "防止它一直把之前已经忘记的东西，还认为是对的。"

  这正是 Graphiti 双时态（bi-temporal）要解决的，也是我们的差距。

设计：同一实体在不同时间有不同值，上下文里新旧并存、顺序打乱。
  T_LATEST   问"现在"，应取**最新**那条
  T_PAST     问"上个月"，应取**当时**那条
  T_CONFLICT 新旧值冲突且没标时间 → 无法判断，应说"无法确定"
  T_SEQ      两条更新，问中间时刻 → 需要顺序推理

判据：
  若 T_LATEST < 1.0 → 模型会被旧值干扰（真实风险）
  若 T_CONFLICT < 1.0 → 模型会强行给一个值（更危险）
"""
from __future__ import annotations

import json
import os
import random

_HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(_HERE, "results")
TASK = os.path.join(_HERE, "work", "e58_task")
os.makedirs(TASK, exist_ok=True)

SURNAME = ["岑", "宓", "郗", "逄", "禚", "阚", "胥", "钭", "乜", "缑"]
GIVEN = ["令仪", "斯年", "望舒", "之桓", "沐宸", "砚清",
         "辞白", "叙礼", "澹宁", "屿舟"]
DEPT = ["甲组", "乙组", "丙组", "戊组", "庚组", "辛组"]
MONTHS = ["一月", "二月", "三月", "四月", "五月", "六月",
          "七月", "八月", "九月", "十月", "十一月", "十二月"]

N_ITEMS = 20


def build(seed=301):
    rng = random.Random(seed)
    persons = [s + g for s in SURNAME for g in GIVEN]
    rng.shuffle(persons)
    items, pi = [], 0
    for i in range(N_ITEMS):
        proj = f"{rng.choice(['KX','TP','QM','VF','RN'])}{rng.randint(100,999)}"
        a1 = persons[pi]; pi += 1
        a2 = persons[pi]; pi += 1
        a3 = persons[pi]; pi += 1
        m1, m2, m3 = sorted(rng.sample(range(1, 13), 3))
        items.append({"id": i, "proj": proj, "old": a1, "new": a2, "mid": a3,
                      "m1": MONTHS[m1 - 1], "m2": MONTHS[m2 - 1], "m3": MONTHS[m3 - 1],
                      "o1": m1, "o2": m2, "o3": m3})
    return items


def cond_rows(items, cond, seed):
    rng = random.Random(seed)
    rows = []
    for it in items:
        pr = it["proj"]
        if cond == "T_LATEST":
            docs = [
                f"{it['m1']}，{pr} 的负责人是 {it['old']}",
                f"{it['m3']}，{pr} 的负责人变更为 {it['new']}",
                f"{it['m2']}，{pr} 做了一次例行评审",
            ]
            q = f"现在 {pr} 的负责人是谁？"
            gold = it["new"]
        elif cond == "T_PAST":
            docs = [
                f"{it['m1']}，{pr} 的负责人是 {it['old']}",
                f"{it['m3']}，{pr} 的负责人变更为 {it['new']}",
                f"{it['m2']}，{pr} 做了一次例行评审",
            ]
            q = f"{it['m1']} 时 {pr} 的负责人是谁？"
            gold = it["old"]
        elif cond == "T_CONFLICT":
            # 两条冲突记录，都没有明确时间标记 → 无法判断
            docs = [
                f"{pr} 的负责人是 {it['old']}",
                f"{pr} 的负责人是 {it['new']}",
                f"{pr} 最近做了一次例行评审",
            ]
            q = f"{pr} 的负责人是谁？"
            gold = "无法确定"
        elif cond == "T_SEQ":
            # 两次变更，问中间时刻
            docs = [
                f"{it['m1']}，{pr} 的负责人是 {it['old']}",
                f"{it['m2']}，{pr} 的负责人变更为 {it['mid']}",
                f"{it['m3']}，{pr} 的负责人变更为 {it['new']}",
            ]
            q = f"{it['m2']} 之后、{it['m3']} 之前，{pr} 的负责人是谁？"
            gold = it["mid"]
        rng.shuffle(docs)
        block = "\n".join(f"[{j}] {t}" for j, t in enumerate(docs, 1))
        rows.append({"id": it["id"], "context": block, "question": q,
                     "answer": gold})
    return rows


CONDS = [("T_LATEST", 1), ("T_PAST", 2), ("T_CONFLICT", 3), ("T_SEQ", 4)]


def main():
    items = build()
    print("=" * 88)
    print("E-58 时间冲突：过时信息会不会被当成当前事实")
    print("=" * 88)

    print("\n" + "-" * 88)
    print("样例")
    print("-" * 88)
    for name, sd in CONDS:
        rows = cond_rows(items, name, sd)
        r = rows[0]
        print(f"\n【{name}】")
        for line in r["context"].split("\n"):
            print(f"   {line}")
        print(f"   Q: {r['question']}")
        print(f"   标准答案: {r['answer']}")

    print("\n" + "-" * 88)
    print("生成任务包")
    print("-" * 88)
    meta = {}
    for name, sd in CONDS:
        rows = cond_rows(items, name, sd)
        out = [{"qid": f"{name}-{i:03d}", "context": r["context"],
                "question": r["question"]} for i, r in enumerate(rows)]
        with open(os.path.join(TASK, f"{name}.json"), "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        key = {f"{name}-{i:03d}": {"answer": r["answer"], "id": r["id"]}
               for i, r in enumerate(rows)}
        with open(os.path.join(TASK, f"{name}_key.json"), "w", encoding="utf-8") as f:
            json.dump(key, f, ensure_ascii=False, indent=1)
        meta[name] = {"n_q": len(rows)}
        print(f"  {name:<12}{len(rows):>3} 题")

    with open(os.path.join(RESULTS, "e58_meta.json"), "w", encoding="utf-8") as f:
        json.dump({"experiment": "E-58", "conds": meta}, f, ensure_ascii=False, indent=1)
    print(f"\n→ {TASK}")


if __name__ == "__main__":
    main()
