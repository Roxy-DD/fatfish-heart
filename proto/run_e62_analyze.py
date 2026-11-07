"""
E-62 分析：创伤是"常驻回忆"还是"情景触发"

四个量（对 24 条，含 4 组）：
  P 平时存在感   T 触发强度   F 触发频率   C 失去代价

判决：
  H1 创伤 = 高 P               → 杞人忧天式（用户否定）
  H2 创伤 = 高 T + 低 P         → 用户说的"情景式"才对
  H3 影响 = T × F              → 取决于遇到多少次

另测：P 与 T 的相关性。
  若 P 与 T 弱相关 → 证明"平时想不想"和"被触发多强"是两个独立维度。
  这直接支持用户对创伤的描述。
"""
from __future__ import annotations

import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
TASK = os.path.join(_HERE, "work", "e62_task")
RESULTS = os.path.join(_HERE, "results")

GROUPS = {"创伤": list(range(0, 6)), "美好": list(range(6, 12)),
          "硬事实": list(range(12, 18)), "日常无关": list(range(18, 24))}


def pearson(a, b):
    n = len(a)
    if n < 2:
        return 0.0
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((x - mb) ** 2 for x in b)
    if va == 0 or vb == 0:
        return 0.0
    return sum((a[i] - ma) * (b[i] - mb) for i in range(n)) / (va ** .5 * vb ** .5)


def main():
    need = {}
    for f in ("P", "T", "F", "C"):
        p = os.path.join(TASK, f"{f}.json")
        if not os.path.exists(p):
            print(f"缺少 {f}.json"); return
        need[f] = {r["idx"]: r["score"]
                   for r in json.load(open(p, encoding="utf-8"))}
    items = {r["idx"]: r["item"] for r in
             json.load(open(os.path.join(TASK, "items.json"), encoding="utf-8"))}

    idxs = sorted(set(need["P"]) & set(need["T"]) & set(need["F"]) & set(need["C"]))
    P = [need["P"][i] for i in idxs]
    T = [need["T"][i] for i in idxs]
    F = [need["F"][i] for i in idxs]
    C = [need["C"][i] for i in idxs]

    print("=" * 88)
    print("E-62 创伤：常驻回忆 vs 情景触发")
    print("=" * 88)
    print(f"\n有效条目 {len(idxs)} 条\n")

    print("-" * 88)
    print("各维度相关性")
    print("-" * 88)
    print(f"{'关系':<24}{'Pearson'}")
    print("-" * 88)
    corr = {}
    for name, x, y in [("P 平时 × T 触发", P, T),
                       ("P 平时 × C 代价", P, C),
                       ("T 触发 × C 代价", T, C),
                       ("T 触发 × F 频率", T, F)]:
        r = pearson(x, y)
        corr[name] = round(r, 4)
        print(f"{name:<24}{r:+.4f}")
    print("-" * 88)

    print("\n" + "-" * 88)
    print("分组均值（核心表）")
    print("-" * 88)
    print(f"{'组':<10}{'n':<5}{'P平时':<10}{'T触发':<10}{'F频率':<10}"
          f"{'C代价':<10}{'T×F':<8}")
    print("-" * 88)
    gm = {}
    for gname, gidx in GROUPS.items():
        gi = [i for i in gidx if i in need["P"]]
        mp = sum(need["P"][i] for i in gi) / len(gi)
        mt = sum(need["T"][i] for i in gi) / len(gi)
        mf = sum(need["F"][i] for i in gi) / len(gi)
        mc = sum(need["C"][i] for i in gi) / len(gi)
        tf = sum(need["T"][i] * need["F"][i] for i in gi) / len(gi) / 10
        gm[gname] = {"n": len(gi), "P": round(mp, 2), "T": round(mt, 2),
                     "F": round(mf, 2), "C": round(mc, 2), "T_x_F": round(tf, 2)}
        print(f"{gname:<10}{len(gi):<5}{mp:<10.2f}{mt:<10.2f}{mf:<10.2f}"
              f"{mc:<10.2f}{tf:<8.2f}")
    print("-" * 88)

    # 判决
    print("\n判决：")
    tr = gm["创伤"]
    print(f"  创伤组: P平时={tr['P']:.2f}  T触发={tr['T']:.2f}  "
          f"C代价={tr['C']:.2f}")
    if tr["T"] > tr["P"] + 1.0:
        v1 = "H2 成立：创伤是「平时低存在感 + 高触发」，不是杞人忧天式常驻"
    elif tr["P"] >= tr["T"]:
        v1 = "H1 成立：创伤确实是常驻回忆"
    else:
        v1 = "介于两者之间"
    print(f"  → {v1}")

    print(f"\n  P 与 T 的相关性 = {corr['P 平时 × T 触发']:+.3f}")
    if abs(corr["P 平时 × T 触发"]) < 0.4:
        print(f"  → 「平时会不会想」与「被触发多强」是两个独立维度 ✓")
    else:
        print(f"  → 两者相关较强，可能是同一维度")

    print("\n  各组 T×F（触发强度 × 频率，即实际被影响的总量）：")
    for g, v in sorted(gm.items(), key=lambda kv: -kv[1]["T_x_F"]):
        print(f"    {g:<10}{v['T_x_F']:.2f}")

    # 逐条
    print("\n" + "-" * 88)
    print("逐条（创伤与硬事实对照）")
    print("-" * 88)
    print(f"{'idx':<5}{'P':<4}{'T':<4}{'F':<4}{'C':<4}{'条目'}")
    print("-" * 88)
    for i in list(range(0, 6)) + list(range(12, 18)):
        if i in need["P"]:
            print(f"{i:<5}{need['P'][i]:<4}{need['T'][i]:<4}"
                  f"{need['F'][i]:<4}{need['C'][i]:<4}{items.get(i,'')[:40]}")
    print("-" * 88)

    out = {"experiment": "E-62", "n": len(idxs), "correlations": corr,
           "group_mean": gm, "verdict": v1,
           "raw": {"idx": idxs, "P": P, "T": T, "F": F, "C": C}}
    with open(os.path.join(RESULTS, "e62_trauma.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e62_trauma.json')}")


if __name__ == "__main__":
    main()

