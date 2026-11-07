"""
E-61 分析：代价到底是"当下情绪"还是"失去情绪"

三个假设：
  H1 代价 ≈ 当下情绪        → 我现在"双通道"设计成立
  H2 代价 ≈ 失去时的情绪    → 用户说法成立（代价就是情绪，藏在"失去"里）
  H3 代价与两者都弱相关     → 代价是独立维度

另测一个关键点：
  用户说"习以为常的东西，人们感觉不到它其实是情绪主导的"。
  若成立：硬事实类（密码/截止日）的 A（当下情绪）应该低，但 C（失去情绪）不低。
"""
from __future__ import annotations

import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
TASK = os.path.join(_HERE, "work", "e61_task")
RESULTS = os.path.join(_HERE, "results")

GROUPS = {
    "硬事实代价": [0, 1, 2, 3, 4, 5, 6],
    "亲情身份": [7, 8, 9, 10, 11],
    "创伤": [12, 13, 14, 15],
    "美好": [16, 17, 18, 19],
    "工作决策": [20, 21, 22],
    "日常无关": [23, 24, 25, 26, 27, 28, 29],
}


def pearson(a, b):
    n = len(a)
    ma = sum(a) / n; mb = sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((x - mb) ** 2 for x in b)
    if va == 0 or vb == 0:
        return 0.0
    cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    return cov / (va ** 0.5 * vb ** 0.5)


def spearman(a, b):
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r
    return pearson(rank(a), rank(b))


def main():
    try:
        A = {r["idx"]: r["score"] for r in
             json.load(open(os.path.join(TASK, "A.json"), encoding="utf-8"))}
        B = {r["idx"]: r["score"] for r in
             json.load(open(os.path.join(TASK, "B.json"), encoding="utf-8"))}
        C = {r["idx"]: r["score"] for r in
             json.load(open(os.path.join(TASK, "C.json"), encoding="utf-8"))}
        items = {r["idx"]: r["item"] for r in
                 json.load(open(os.path.join(TASK, "items.json"), encoding="utf-8"))}
    except FileNotFoundError as e:
        print("缺少评分文件:", e)
        return

    idxs = sorted(set(A) & set(B) & set(C))
    a = [A[i] for i in idxs]
    b = [B[i] for i in idxs]
    c = [C[i] for i in idxs]

    print("=" * 90)
    print("E-61 代价 vs 当下情绪 vs 失去情绪")
    print("=" * 90)
    print(f"\n有效条目 {len(idxs)} 条\n")

    print("-" * 90)
    print("相关性（这是判决）")
    print("-" * 90)
    print(f"{'关系':<28}{'Pearson':<12}{'Spearman':<12}")
    print("-" * 90)
    pairs = [
        ("代价 B × 当下情绪 A", b, a),
        ("代价 B × 失去情绪 C", b, c),
        ("当下情绪 A × 失去情绪 C", a, c),
    ]
    res = {}
    for name, x, y in pairs:
        p, s = pearson(x, y), spearman(x, y)
        res[name] = {"pearson": round(p, 4), "spearman": round(s, 4)}
        print(f"{name:<28}{p:<+12.3f}{s:<+12.3f}")
    print("-" * 90)

    p_ba = res["代价 B × 当下情绪 A"]["pearson"]
    p_bc = res["代价 B × 失去情绪 C"]["pearson"]
    print("\n判决：")
    print(f"  corr(代价, 当下情绪) = {p_ba:+.3f}")
    print(f"  corr(代价, 失去情绪) = {p_bc:+.3f}")
    if p_bc > p_ba + 0.15:
        verdict = "H2 成立：代价更接近「失去时的情绪」，用户的说法对"
    elif p_ba > p_bc + 0.15:
        verdict = "H1 成立：代价更接近「当下情绪」"
    else:
        verdict = "H3：代价与两者都接近，可能是独立维度"
    print(f"  → {verdict}")

    # ---------- 分组均值 ----------
    print("\n" + "-" * 90)
    print("分组均值（看「习以为常的东西」是否 A 低 C 高）")
    print("-" * 90)
    print(f"{'组':<12}{'n':<5}{'A当下情绪':<12}{'B代价':<10}{'C失去情绪':<12}"
          f"{'C−A':<10}")
    print("-" * 90)
    gm = {}
    for gname, gidx in GROUPS.items():
        gi = [i for i in gidx if i in A]
        if not gi:
            continue
        ma = sum(A[i] for i in gi) / len(gi)
        mb = sum(B[i] for i in gi) / len(gi)
        mc = sum(C[i] for i in gi) / len(gi)
        gm[gname] = {"n": len(gi), "A": round(ma, 2), "B": round(mb, 2),
                     "C": round(mc, 2), "C_minus_A": round(mc - ma, 2)}
        print(f"{gname:<12}{len(gi):<5}{ma:<12.2f}{mb:<10.2f}{mc:<12.2f}"
              f"{mc-ma:<+10.2f}")
    print("-" * 90)

    # 关键：硬事实类
    hf = gm.get("硬事实代价")
    if hf:
        print(f"\n关键检验（用户的核心说法）：")
        print(f"  硬事实代价组：当下情绪 A={hf['A']:.2f}，"
              f"失去情绪 C={hf['C']:.2f}，差 {hf['C_minus_A']:+.2f}")
        if hf["C"] > hf["A"] + 0.5:
            print(f"  ✔ 习以为常的东西：当下没感觉，但失去时会难受")
            print(f"    → 说明「代价」里确实藏着情绪，只是被习惯掩盖了（用户对）")
        else:
            print(f"  ✘ 没有明显的「习惯化掩盖」现象")

    # 逐条明细
    print("\n" + "-" * 90)
    print("逐条明细（按代价 B 降序）")
    print("-" * 90)
    print(f"{'idx':<5}{'A':<4}{'B':<4}{'C':<4}{'条目'}")
    print("-" * 90)
    for i in sorted(idxs, key=lambda x: -B[x]):
        print(f"{i:<5}{A[i]:<4}{B[i]:<4}{C[i]:<4}{items.get(i,'')[:48]}")
    print("-" * 90)

    out = {"experiment": "E-61", "n": len(idxs),
           "correlations": res, "verdict": verdict,
           "group_mean": gm,
           "raw": {"A": a, "B": b, "C": c, "idx": idxs}}
    with open(os.path.join(RESULTS, "e61_cost_emotion.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e61_cost_emotion.json')}")


if __name__ == "__main__":
    main()
