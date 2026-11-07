"""
E-54 干扰假说：不是容量不够，是"相似记忆互相干扰"

文献依据（已查证）：
  arXiv 2506.08184 "Unable to Forget: Proactive Interference Reveals
  Working Memory Limits in LLMs Beyond Context Length"
  核心结论：
    - LLM 检索准确率随干扰累积**对数线性衰减到零**（log-linear to zero）
    - 错误来自"检索到已经被覆盖的旧值"
    - 用 prompt 工程缓解"收效有限"
    - 定位：**存在超出上下文长度的工作记忆瓶颈**

  arXiv 2601.02740 "Language Hierarchization Provides the Optimal Solution
  to Human Working Memory Limits"
  核心结论：层级化把"单位数"压缩到工作记忆容量以内，优于线性处理。

本实验验证"干扰假说"，而不是"容量假说"：
  如果干扰是主因，那么——同样条数，**语义相近的干扰项**比**多样的干扰项**
  造成的准确率损失应该显著更大。

四个条件（全部完整原文，不压缩，隔离干扰变量）：
  A_1        只有目标 1 条（天花板对照）
  B_SIM5     目标 + 4 条同模板干扰（高干扰）
  C_DIV5     目标 + 4 条异模板干扰（低干扰）
  D_SIM10    目标 + 9 条同模板干扰（更强干扰）

判据：
  若 acc(B_SIM5) 显著低于 acc(C_DIV5) → 干扰是主因，选择策略应转向"多样性"
  若两者接近且都低于 A_1      → 是容量问题，干扰次要
"""
from __future__ import annotations

import json
import os
import random

_HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(_HERE, "results")
TASK = os.path.join(_HERE, "work", "e54_task")
os.makedirs(TASK, exist_ok=True)

SURNAME = ["岑", "宓", "郗", "逄", "禚", "阚", "胥", "钭", "乜", "缑"]
GIVEN = ["令仪", "斯年", "望舒", "之桓", "沐宸", "砚清", "辞白", "叙礼", "澹宁", "屿舟"]
PROJ = ["KX7", "QM3", "VF9", "RN2", "WB5", "ZL8", "TP4", "HY6", "GD1", "CS0"]
DEPT = ["乙组", "丙组", "戊组", "庚组", "壬组"]

# 四个"模板"（句式），同模板=高语义相似，异模板=低相似
TEMPLATES = [
    # t0: 人物负责项目
    lambda p, pr, dp: f"{p} 负责 {pr} 这个项目，交付时间是下个季度末",
    # t1: 项目属于组
    lambda p, pr, dp: f"{pr} 隶属于 {dp}，目前正在做接口联调，负责人是 {p}",
    # t2: 组的负责人
    lambda p, pr, dp: f"{dp} 的负责人是 {p}，同时兼管预算审批和人员安排",
    # t3: 审批流程
    lambda p, pr, dp: f"{p} 提出把 {pr} 的评审周期从两周缩短到一周，{dp} 已同意",
]

N_ITEMS = 24


def build_pool(seed=21):
    """生成一批互不相同的记忆，记录其模板号"""
    rng = random.Random(seed)
    pool, used = [], set()
    # 先生成足够多的唯一实体组合
    combos = []
    for pr in PROJ:
        for n in range(10, 100):
            combos.append(f"{pr}{n}")
    rng.shuffle(combos)
    ci = 0
    for i in range(N_ITEMS):
        while True:
            p = rng.choice(SURNAME) + rng.choice(GIVEN)
            if p not in used:
                used.add(p)
                break
        proj = combos[ci]; ci += 1
        dept = rng.choice(DEPT)
        t = i % 4
        text = TEMPLATES[t](p, proj, dept)
        # 问句：只问 t0/t1/t2 可答的
        if t == 0:
            q, ans = f"谁负责 {proj}？", p
        elif t == 1:
            q, ans = f"{proj} 属于哪个组？", dept
        elif t == 2:
            q, ans = f"{dept} 的负责人是谁？", p
        else:
            q, ans = f"谁提出缩短 {proj} 的评审周期？", p
        pool.append({"id": i, "t": t, "text": text, "q": q, "a": ans,
                     "p": p, "proj": proj, "dept": dept})
    return pool


def make_cond(pool, kind, n_distract):
    """构造一个条件的所有题目"""
    rng = random.Random(hash(kind) % 10000)
    rows = []
    for it in pool:
        if n_distract == 0:
            docs = [(it["text"], "target")]
        else:
            if kind == "SIM":
                # 同模板干扰：优先取相同 t
                cands = [x for x in pool if x["id"] != it["id"] and x["t"] == it["t"]]
                if len(cands) < n_distract:
                    cands += [x for x in pool if x["id"] != it["id"] and x["t"] != it["t"]]
            else:
                # 异模板干扰：取不同 t
                cands = [x for x in pool if x["id"] != it["id"] and x["t"] != it["t"]]
                if len(cands) < n_distract:
                    cands += [x for x in pool if x["id"] != it["id"] and x["t"] == it["t"]]
            pick = rng.sample(cands, min(n_distract, len(cands)))
            docs = [(it["text"], "target")] + [(d["text"], "distract") for d in pick]
        rng.shuffle(docs)
        block = "\n".join(f"[{j}] {t}" for j, (t, _) in enumerate(docs, 1))
        rows.append({"id": it["id"], "context": block,
                     "question": it["q"], "answer": it["a"],
                     "n_docs": len(docs)})
    return rows


CONDS = [
    ("A_1", "NONE", 0),
    ("B_SIM5", "SIM", 4),
    ("C_DIV5", "DIV", 4),
    ("D_SIM10", "SIM", 9),
]


def main():
    pool = build_pool()
    print("=" * 92)
    print("E-54 干扰假说：相似干扰 vs 多样干扰")
    print("=" * 92)
    print(f"\n记忆池 {len(pool)} 条，4 个模板\n")

    print("-" * 92)
    print("样例（同一条目标在不同条件下的上下文）")
    print("-" * 92)
    for name, kind, nd in CONDS:
        rows = make_cond(pool, kind, nd)
        r = rows[0]
        print(f"\n【{name}】{r['n_docs']} 条")
        for line in r["context"].split("\n")[:6]:
            print(f"   {line}")
        if r["n_docs"] > 6:
            print(f"   ...（共 {r['n_docs']} 条）")
        print(f"   Q: {r['question']}   A: {r['answer']}")

    print("\n" + "-" * 92)
    print("生成任务包")
    print("-" * 92)
    meta = {}
    for name, kind, nd in CONDS:
        rows = make_cond(pool, kind, nd)
        out = [{"qid": f"{name}-{i:03d}", "context": r["context"],
                "question": r["question"]} for i, r in enumerate(rows)]
        with open(os.path.join(TASK, f"{name}.json"), "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        key = {f"{name}-{i:03d}": {"answer": r["answer"], "id": r["id"]}
               for i, r in enumerate(rows)}
        with open(os.path.join(TASK, f"{name}_key.json"), "w", encoding="utf-8") as f:
            json.dump(key, f, ensure_ascii=False, indent=1)
        avg_len = sum(len(r["context"]) for r in rows) / len(rows)
        meta[name] = {"n_q": len(rows), "n_docs": rows[0]["n_docs"],
                      "avg_ctx_len": round(avg_len, 1)}
        print(f"  {name:<10}{len(rows):>3} 题  {rows[0]['n_docs']:>2} 条/题  "
              f"平均 {avg_len:.0f} 字")

    with open(os.path.join(RESULTS, "e54_meta.json"), "w", encoding="utf-8") as f:
        json.dump({"experiment": "E-54", "conds": meta}, f, ensure_ascii=False, indent=1)
    print(f"\n任务包 → {TASK}")


if __name__ == "__main__":
    main()
