"""
E-70 第 3 步：对比"客观命中"与"模型自检"，看模型是否能发现问题

关键：如果模型自检判为 no 的，恰恰是客观没命中的，那说明模型真的能自查。
      如果模型判为 no 的比客观更多，说明它更严格（可能发现了"命中但答不出"）。
"""
from __future__ import annotations

import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
TASK = os.path.join(_HERE, "work", "e70_task")
RESULTS = os.path.join(_HERE, "results")


def main():
    obj = json.load(open(os.path.join(TASK, "objective.json"), encoding="utf-8"))
    meta = {m["jid"]: m for m in
            json.load(open(os.path.join(TASK, "selfcheck_meta.json"), encoding="utf-8"))}
    jp = os.path.join(TASK, "judge.json")
    if not os.path.exists(jp):
        print("缺少 judge.json")
        return
    judge = json.load(open(jp, encoding="utf-8"))

    # 按 jid 汇总模型判断：有任一候选判 yes，则模型认为"能答"
    by_jid = {}
    for r in judge:
        jid = r["cid"].split("-")[0]
        v = str(r.get("verdict", "")).strip().lower() == "yes"
        by_jid[jid] = by_jid.get(jid, False) or v

    print("=" * 88)
    print("E-70 第 3 步：客观命中 vs 模型自检")
    print("=" * 88)

    common = [j for j in obj if j in by_jid]
    print(f"\n可比对问题 {len(common)} 个\n")
    print("-" * 88)
    print(f"{'jid':<8}{'类型':<14}{'客观命中':<10}{'模型判定':<10}{'一致':<8}{'问题'}")
    print("-" * 88)

    agree = 0
    mismatch = []
    for j in sorted(common):
        o = bool(obj[j]); m = bool(by_jid[j])
        same = (o == m)
        agree += 1 if same else 0
        if not same:
            mismatch.append((j, o, m))
        t = meta.get(j, {}).get("type", "?")
        q = meta.get(j, {}).get("q", "")[:30]
        flag = "" if same else " ⚠"
        print(f"{j:<8}{t:<14}{str(o):<10}{str(m):<10}{str(same):<8}{q}{flag}")
    print("-" * 88)
    print(f"\n一致率 = {agree}/{len(common)} = {agree/len(common):.4f}")

    if mismatch:
        print("\n不一致（这些最能说明问题）：")
        for j, o, m in mismatch:
            t = meta.get(j, {}).get("type", "?")
            q = meta.get(j, {}).get("q", "")
            if o and not m:
                print(f"  [{j}] ({t}) {q}")
                print(f"    → 客观：目标记忆进了 top-3；模型：答不出")
                print(f"    → 含义：检索命中了，但内容不足以回答（真问题！）")
            else:
                print(f"  [{j}] ({t}) {q}")
                print(f"    → 客观：目标没进 top-3；模型：认为能答")
                print(f"    → 含义：可能是别的候选也能答，或模型误判")

    # 模型自己提出的修法
    fp = os.path.join(TASK, "fixes.json")
    print("\n" + "=" * 88)
    print("模型自己提出的改进方案")
    print("=" * 88)
    if os.path.exists(fp):
        fixes = json.load(open(fp, encoding="utf-8"))
        if not fixes:
            print("  （空：模型认为全部都能记住）")
        for f in fixes:
            print(f"\n  [{f.get('jid')}] 类型={f.get('type')}")
            print(f"    问题: {f.get('problem')}")
            print(f"    修法: {f.get('fix')}")
    else:
        print("  （fixes.json 不存在）")
        fixes = []

    out = {"experiment": "E-70", "n_questions": len(common),
           "agree": agree, "agree_rate": round(agree / len(common), 4),
           "mismatch": [{"jid": j, "objective": o, "model": m,
                         "type": meta.get(j, {}).get("type"),
                         "q": meta.get(j, {}).get("q")}
                        for j, o, m in mismatch],
           "model_fixes": fixes}
    with open(os.path.join(RESULTS, "e70_selfloop.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e70_selfloop.json')}")


if __name__ == "__main__":
    main()
