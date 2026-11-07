"""
E-53 端到端压缩验证（v2，修正单条截断的测量缺陷）

v1 的缺陷：单条截断时，答案串本身可能还留在上下文里
（"阚望舒 负责 T" —— 人名还在），模型回显就能蒙对 → 系统性高估。

v2 设计（更真实也更严格）：
  每题注入 5 条**同档位压缩**的记忆：1 条目标 + 4 条干扰。
  问的是目标那条的事实。
  压缩越狠 → 目标与干扰的区分度越低 → 模型越容易答错或答"无法确定"。

  这模拟真实检索：注入多条记忆，让模型在里面找。

防作弊：
  - 全部合成实体（10姓×10名=100种人名，随机猜中率 1%）
  - 干扰项来自同一批合成记忆，实体风格相同，无法靠"哪个像人名"区分
  - 允许且鼓励回答"无法确定"，正确计 0 分
"""
from __future__ import annotations

import json
import os
import random

_HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(_HERE, "results")
WORK = os.path.join(_HERE, "work", "e53")
os.makedirs(WORK, exist_ok=True)

SURNAME = ["岑", "宓", "郗", "逄", "禚", "阚", "胥", "钭", "乜", "缑"]
GIVEN = ["令仪", "斯年", "望舒", "之桓", "沐宸", "砚清", "辞白", "叙礼", "澹宁", "屿舟"]
PROJ = ["KX7", "QM3", "VF9", "RN2", "WB5", "ZL8", "TP4", "HY6", "GD1", "CS0"]
DEPT = ["乙组", "丙组", "戊组", "庚组", "壬组"]

LEVELS = ["FULL", "P60", "P30", "P10", "C3"]
N_ITEM = 30
N_DISTRACT = 4


def build_items(seed=7):
    rng = random.Random(seed)
    items = []
    used_names = set()
    for i in range(N_ITEM):
        # 保证人名唯一，避免同名干扰
        while True:
            p = rng.choice(SURNAME) + rng.choice(GIVEN)
            if p not in used_names:
                used_names.add(p)
                break
        proj = rng.choice(PROJ) + str(rng.randint(10, 99))
        dept = rng.choice(DEPT)
        kind = i % 4
        if kind == 0:
            text = f"{p} 负责 {proj}，交付时间是下个季度末"
            q, ans = f"谁负责 {proj}？", p
        elif kind == 1:
            text = f"{proj} 隶属于 {dept}，目前正在做接口联调"
            q, ans = f"{proj} 属于哪个组？", dept
        elif kind == 2:
            text = f"{dept} 的负责人是 {p}，同时兼管预算审批"
            q, ans = f"{dept} 的负责人是谁？", p
        else:
            text = f"{p} 提出把 {proj} 的评审周期从两周缩短到一周"
            q, ans = f"谁提出缩短 {proj} 的评审周期？", p
        items.append({"id": i, "text": text, "question": q, "answer": ans,
                      "proj": proj, "dept": dept, "person": p})
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


def make_packs(items):
    """每档：每题 = 目标 + 4 干扰，全部同档压缩；顺序打乱"""
    rng = random.Random(99)
    packs = {}
    for lv in LEVELS:
        rows = []
        for it in items:
            # 干扰项：从其它记忆里随机取
            others = [x for x in items if x["id"] != it["id"]]
            distract = rng.sample(others, N_DISTRACT)
            docs = [{"src": "target", "text": it["text"]}] + \
                   [{"src": "distract", "text": d["text"]} for d in distract]
            rng.shuffle(docs)
            block = []
            for j, d in enumerate(docs, 1):
                c = compress(d["text"], lv)
                block.append(f"[{j}] {c}")
            rows.append({
                "id": it["id"],
                "context": "\n".join(block),
                "question": it["question"],
                "answer": it["answer"],
                "ctx_len": sum(len(compress(d["text"], lv)) for d in docs),
                "full_len": sum(len(d["text"]) for d in docs),
            })
        packs[lv] = rows
    return packs


def main():
    items = build_items()
    packs = make_packs(items)
    print("=" * 92)
    print("E-53 v2 端到端压缩验证 —— 5条候选（1目标+4干扰）")
    print("=" * 92)
    print(f"\n{N_ITEM} 条记忆，{len(LEVELS)} 档，每题 {1+N_DISTRACT} 条候选\n")

    print("-" * 92)
    print("样例（第 0 题在各档位下的候选块）")
    print("-" * 92)
    for lv in LEVELS:
        r = packs[lv][0]
        print(f"\n【{lv}】{r['ctx_len']}/{r['full_len']} 字")
        for line in r["context"].split("\n"):
            print(f"   {line}")
        print(f"   Q: {r['question']}")
        print(f"   A: {r['answer']}")

    print("\n" + "-" * 92)
    print("各档平均上下文长度")
    print("-" * 92)
    stats = {}
    for lv in LEVELS:
        a = sum(r["ctx_len"] for r in packs[lv]) / len(packs[lv])
        f = sum(r["full_len"] for r in packs[lv]) / len(packs[lv])
        stats[lv] = {"avg_ctx": round(a, 1), "avg_full": round(f, 1),
                     "ratio": round(a / f, 3)}
        print(f"  {lv:<6}{a:>7.1f} 字 / {f:.1f}  （{a/f*100:.0f}%）")

    # 拆分：每档 3 批 × 10 题
    for lv in LEVELS:
        rows = packs[lv]
        for bi in range(3):
            batch = rows[bi * 10:(bi + 1) * 10]
            with open(os.path.join(WORK, f"v2_{lv}_{bi}.json"), "w", encoding="utf-8") as f:
                json.dump(batch, f, ensure_ascii=False, indent=1)

    meta = {"experiment": "E-53", "version": 2, "levels": LEVELS,
            "n_items": N_ITEM, "n_distract": N_DISTRACT,
            "stats": stats,
            "design": "每题5条候选(1目标+4干扰)，同档压缩，防作弊用合成实体"}
    with open(os.path.join(RESULTS, "e53_meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    print(f"\n生成 {len(LEVELS)*3} 个测试包 → {WORK}")


if __name__ == "__main__":
    main()
