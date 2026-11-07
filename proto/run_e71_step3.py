"""E-71 第 3 步：闭环验证 —— 改造前后模型自判的"能答率"变化"""
from __future__ import annotations
import json, os

_HERE = os.path.dirname(os.path.abspath(__file__))
TASK = os.path.join(_HERE, "work", "e70_task")
RESULTS = os.path.join(_HERE, "results")


def load_verdicts(path):
    if not os.path.exists(path):
        return None
    by = {}
    for r in json.load(open(path, encoding="utf-8")):
        jid = r["cid"].split("-")[0]
        v = str(r.get("verdict", "")).strip().lower() == "yes"
        by[jid] = by.get(jid, False) or v
    return by


def main():
    meta = {m["jid"]: m for m in
            json.load(open(os.path.join(TASK, "selfcheck_meta.json"), encoding="utf-8"))}
    v1 = load_verdicts(os.path.join(TASK, "judge.json"))
    v2 = load_verdicts(os.path.join(TASK, "judge_v2.json"))
    if v1 is None or v2 is None:
        print("缺少 judge.json 或 judge_v2.json")
        return

    # v1 的 jid 是 J###，v2 是 K###；按顺序一一对应
    j_old = sorted(v1); j_new = sorted(v2)
    if len(j_old) != len(j_new):
        print(f"数量不一致 {len(j_old)} vs {len(j_new)}")
        return

    # 用 meta（J 序号）取问题
    mlist = [meta[j] for j in j_old]

    print("=" * 92)
    print("E-71 闭环验证：改造前 vs 改造后（模型自判）")
    print("=" * 92)

    old_ok = sum(1 for j in j_old if v1[j])
    new_ok = sum(1 for j in j_new if v2[j])
    n = len(j_old)

    print(f"\n改造前模型认为能答: {old_ok}/{n} = {old_ok/n:.4f}")
    print(f"改造后模型认为能答: {new_ok}/{n} = {new_ok/n:.4f}")
    print(f"变化: {(new_ok-old_ok)/n:+.4f}")

    # 逐条看
    print("\n" + "-" * 92)
    print(f"{'类型':<14}{'问题':<44}{'改造前':<10}{'改造后':<10}{'变化'}")
    print("-" * 92)
    fixed, broke, still = [], [], []
    for i, m in enumerate(mlist):
        a = v1[j_old[i]]; b = v2[j_new[i]]
        flag = ""
        if not a and b:
            flag = " ↑修复"; fixed.append(m)
        elif a and not b:
            flag = " ↓退化"; broke.append(m)
        elif not a and not b:
            flag = " 仍失败"; still.append(m)
        print(f"{m['type']:<14}{m['q'][:42]:<44}{str(a):<10}{str(b):<10}{flag}")
    print("-" * 92)

    print(f"\n修复 {len(fixed)} 条，退化 {len(broke)} 条，仍失败 {len(still)} 条")
    if fixed:
        print("\n修复的：")
        for m in fixed:
            print(f"  [{m['jid']}] ({m['type']}) {m['q']}")
    if still:
        print("\n仍失败的：")
        for m in still:
            print(f"  [{m['jid']}] ({m['type']}) {m['q']}")
    if broke:
        print("\n退化的（需检查是不是别名/字段引入的噪声）：")
        for m in broke:
            print(f"  [{m['jid']}] ({m['type']}) {m['q']}")

    # 闭环判决
    print("\n" + "=" * 92)
    print("闭环判决")
    print("=" * 92)
    delta = (new_ok - old_ok) / n
    if delta >= 0.10 and len(broke) <= 1:
        verdict = "闭环成立：模型发现问题→自己提改法→执行→模型自己确认改善了"
    elif delta > 0:
        verdict = "部分成立：有改善但幅度小"
    else:
        verdict = "不成立：改造没有让模型认为更好了"
    print(f"  {verdict}")

    out = {"experiment": "E-71-loop", "n": n,
           "old_ok": old_ok, "new_ok": new_ok,
           "old_rate": round(old_ok/n, 4), "new_rate": round(new_ok/n, 4),
           "delta": round(delta, 4),
           "fixed": [m["jid"] for m in fixed],
           "broke": [m["jid"] for m in broke],
           "still": [m["jid"] for m in still],
           "verdict": verdict}
    with open(os.path.join(RESULTS, "e71_loop.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e71_loop.json')}")


if __name__ == "__main__":
    main()
