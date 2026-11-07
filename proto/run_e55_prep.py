"""
E-55 干扰假说（修正版）—— 干扰项必须"相似但不冲突"

E-54 的缺陷：
  B_SIM5 的弃答全部来自"同一 dept 的负责人出现多次"→ 这是**真答案冲突**，
  不是检索失败。所以那个 -0.167 测的是"数据本身有歧义"，不是干扰效应。
  也解释了为什么 D_SIM10（0.958）反而比 B_SIM5（0.833）好——纯属构造噪声。

修正：
  干扰项**必须是不同的主体**，保证任何问题在上下文里有且仅有一个正确答案。
  相似性只来自**句式模板**，不来自主体重复。

四个条件：
  A_1        1 条（天花板）
  B_SIM5     目标 + 4 条同模板干扰（主体不同，答案唯一）
  C_DIV5     目标 + 4 条异模板干扰
  D_SIM10    目标 + 9 条同模板干扰

所有条件都满足："每个问题在 context 里只有一个正确答案"。
"""
from __future__ import annotations

import json
import os
import random

_HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(_HERE, "results")
TASK = os.path.join(_HERE, "work", "e55_task")
os.makedirs(TASK, exist_ok=True)

SURNAME = ["岑", "宓", "郗", "逄", "禚", "阚", "胥", "钭", "乜", "缑"]
GIVEN = ["令仪", "斯年", "望舒", "之桓", "沐宸", "砚清", "辞白", "叙礼", "澹宁", "屿舟"]
PROJ = ["KX7", "QM3", "VF9", "RN2", "WB5", "ZL8", "TP4", "HY6", "GD1", "CS0"]
DEPT = ["甲组", "乙组", "丙组", "戊组", "庚组", "辛组", "壬组", "癸组", "子组", "丑组"]

# 模板：(生成函数, 问题类型)
# 主体键用于保证不冲突 —— 每个模板的主体唯一标识
T0 = lambda p, pr, dp: f"{p} 负责 {pr} 这个项目，交付时间是下个季度末"
T1 = lambda p, pr, dp: f"{pr} 隶属于 {dp}，目前正在做接口联调"
T2 = lambda p, pr, dp: f"{dp} 的负责人是 {p}，同时兼管预算审批"
T3 = lambda p, pr, dp: f"{p} 提出把 {pr} 的评审周期从两周缩短到一周"
TEMPLATES = [T0, T1, T2, T3]

N_ITEMS = 24


def build_pool(seed=33):
    """生成记忆池；同一模板内主体唯一（proj 唯一 / dept 唯一 / person 唯一）"""
    rng = random.Random(seed)
    # 所有 proj 唯一
    projs = []
    for p in PROJ:
        for n in range(10, 100):
            projs.append(f"{p}{n}")
    rng.shuffle(projs)
    # dept 池要够 24 条（t1/t2 都用 dept）
    depts = [f"{d}{i}" for d in DEPT for i in range(3)]
    rng.shuffle(depts)
    persons = []
    for s in SURNAME:
        for g in GIVEN:
            persons.append(s + g)
    rng.shuffle(persons)

    pool = []
    pi = di = pei = 0
    for i in range(N_ITEMS):
        t = i % 4
        if t == 0:
            pr = projs[pi]; pi += 1
            p = persons[pei]; pei += 1
            dp = "-"
            text = T0(p, pr, dp)
            q, a, subj = f"谁负责 {pr}？", p, ("proj", pr)
        elif t == 1:
            pr = projs[pi]; pi += 1
            dp = depts[di]; di += 1
            p = persons[pei]; pei += 1
            text = T1(p, pr, dp)
            q, a, subj = f"{pr} 属于哪个组？", dp, ("proj", pr)
        elif t == 2:
            dp = depts[di]; di += 1
            p = persons[pei]; pei += 1
            pr = projs[pi]; pi += 1
            text = T2(p, pr, dp)
            q, a, subj = f"{dp} 的负责人是谁？", p, ("dept", dp)
        else:
            pr = projs[pi]; pi += 1
            p = persons[pei]; pei += 1
            dp = "-"
            text = T3(p, pr, dp)
            q, a, subj = f"谁提出缩短 {pr} 的评审周期？", p, ("proj", pr)
        pool.append({"id": i, "t": t, "text": text, "q": q, "a": a,
                     "subj": subj, "p": p, "proj": pr, "dept": dp})
    return pool


def pick_distractors(pool, target, kind, n, rng):
    """选干扰项，保证不与目标主体冲突，且干扰项之间也不冲突于该问题"""
    tkey, tval = target["subj"]

    def conflicts(x):
        # 与目标主体相同 → 会造成答案冲突，排除
        return x["subj"] == (tkey, tval)

    if kind == "SIM":
        cands = [x for x in pool if x["id"] != target["id"]
                 and x["t"] == target["t"] and not conflicts(x)]
    else:
        cands = [x for x in pool if x["id"] != target["id"]
                 and x["t"] != target["t"] and not conflicts(x)]

    # 二次保险：干扰项之间也不能在目标问题上冲突
    picked, used_subj = [], {target["subj"]}
    rng.shuffle(cands)
    for x in cands:
        if x["subj"] in used_subj:
            continue
        picked.append(x)
        used_subj.add(x["subj"])
        if len(picked) >= n:
            break
    return picked


def build_cond(pool, kind, n_distract, seed):
    rng = random.Random(seed)
    rows = []
    for it in pool:
        if n_distract == 0:
            docs = [it["text"]]
        else:
            ds = pick_distractors(pool, it, kind, n_distract, rng)
            docs = [it["text"]] + [d["text"] for d in ds]
        rng.shuffle(docs)
        block = "\n".join(f"[{j}] {t}" for j, t in enumerate(docs, 1))
        rows.append({"id": it["id"], "context": block,
                     "question": it["q"], "answer": it["a"],
                     "n_docs": len(docs)})
    return rows


CONDS = [("A_1", "NONE", 0, 1), ("B_SIM5", "SIM", 4, 11),
         ("C_DIV5", "DIV", 4, 22), ("D_SIM10", "SIM", 9, 33)]


def main():
    pool = build_pool()
    print("=" * 88)
    print("E-55 干扰假说（修正版，保证答案唯一）")
    print("=" * 88)
    print(f"\n记忆池 {len(pool)} 条\n")

    print("-" * 88)
    print("样例")
    print("-" * 88)
    for name, kind, nd, sd in CONDS:
        rows = build_cond(pool, kind, nd, sd)
        r = rows[0]
        print(f"\n【{name}】{r['n_docs']} 条")
        for line in r["context"].split("\n")[:6]:
            print(f"   {line}")
        if r["n_docs"] > 6:
            print(f"   ...（共 {r['n_docs']} 条）")
        print(f"   Q: {r['question']}   A: {r['answer']}")

    print("\n" + "-" * 88)
    print("生成任务包")
    print("-" * 88)
    meta = {}
    for name, kind, nd, sd in CONDS:
        rows = build_cond(pool, kind, nd, sd)
        out = [{"qid": f"{name}-{i:03d}", "context": r["context"],
                "question": r["question"]} for i, r in enumerate(rows)]
        with open(os.path.join(TASK, f"{name}.json"), "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        key = {f"{name}-{i:03d}": {"answer": r["answer"], "id": r["id"]}
               for i, r in enumerate(rows)}
        with open(os.path.join(TASK, f"{name}_key.json"), "w", encoding="utf-8") as f:
            json.dump(key, f, ensure_ascii=False, indent=1)
        avg = sum(len(r["context"]) for r in rows) / len(rows)
        meta[name] = {"n_q": len(rows), "n_docs": rows[0]["n_docs"],
                      "avg_ctx": round(avg, 1)}
        print(f"  {name:<10}{len(rows):>3}题  {rows[0]['n_docs']:>2}条/题  平均 {avg:.0f}字")

    with open(os.path.join(RESULTS, "e55_meta.json"), "w", encoding="utf-8") as f:
        json.dump({"experiment": "E-55", "conds": meta}, f, ensure_ascii=False, indent=1)
    print(f"\n→ {TASK}")


if __name__ == "__main__":
    main()
