"""
E-73 分析：模型发现的新缺陷类型，能否被规则实现

模型给出 audit.json 后：
  1. 看它把哪些判成"非 ok"
  2. 看现有 3 类规则能覆盖多少
  3. 剩下的就是"我还没发现的新类型"
  4. 把它的检测思路落成规则，测召回
"""
from __future__ import annotations

import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e73")
RESULTS = os.path.join(_HERE, "results")


def main():
    ap = os.path.join(TASK, "audit.json")
    if not os.path.exists(ap):
        print("缺少 audit.json")
        return
    audit = json.load(open(ap, encoding="utf-8"))
    corpus = {r["rid"]: r["memory"] for r in
              json.load(open(os.path.join(TASK, "corpus.json"), encoding="utf-8"))}

    # 现有 3 类规则（用 E-72 的 fast_check）
    sys.path.insert(0, _HERE)
    from run_e72_gate import fast_check

    per = audit.get("per_item", [])
    news = audit.get("new_types", [])

    print("=" * 92)
    print("E-73 模型发现的缺陷类型 vs 现有规则覆盖")
    print("=" * 92)
    print(f"\n模型判定：{len(per)} 条")
    from collections import Counter
    c = Counter(p.get("verdict") for p in per)
    print("  分布:", dict(c))
    print(f"\n模型归纳的新类型：{len(news)} 个")
    for n in news:
        print(f"\n  【{n.get('name')}】")
        print(f"    标准: {n.get('criterion')}")
        print(f"    例子: {n.get('examples')}")
        print(f"    检测: {n.get('detect')}")

    # ---------- 逐条对比：模型判"有问题" vs 现有规则 ----------
    print("\n" + "=" * 92)
    print("逐条对比：模型说有问题 vs 现有规则能否抓到")
    print("=" * 92)
    print("-" * 92)
    print(f"{'rid':<6}{'模型判定':<20}{'现有规则':<26}{'覆盖?':<8}{'内容'}")
    print("-" * 92)

    covered = missed = 0
    missed_items = []
    for p in per:
        rid = p.get("rid")
        v = p.get("verdict", "?")
        txt = corpus.get(rid, "")
        chk = fast_check(txt)
        cur = "/".join(chk["issues"]) if chk["issues"] else "clean"
        if v == "ok":
            ok = True if not chk["issues"] else False
            mark = "—"
        else:
            # 模型说有问题
            if v in ("bare_pronoun", "unanchored_time", "emotion_only"):
                ok = v in chk["issues"]
                mark = "已知类" if ok else "规则漏了"
                if ok: covered += 1
                else: missed += 1; missed_items.append((rid, v, txt))
            else:
                # 新类型：现有规则几乎不可能抓到
                ok = bool(chk["issues"])
                mark = "新类型·规则抓到" if ok else "新类型·规则漏"
                if ok: covered += 1
                else: missed += 1; missed_items.append((rid, v, txt))
        print(f"{rid:<6}{v:<20}{cur[:24]:<26}{mark:<8}{txt[:26]}")
    print("-" * 92)

    print(f"\n现有规则能覆盖: {covered}")
    print(f"现有规则漏掉: {missed}")
    if corpus:
        print(f"覆盖率: {covered/(covered+missed)*100:.1f}%" if (covered+missed) else "n/a")

    if missed_items:
        print("\n" + "=" * 92)
        print("我漏掉的（这些就是模型比我多发现的）")
        print("=" * 92)
        for rid, v, txt in missed_items:
            print(f"  [{rid}] 模型判={v}")
            print(f"    {txt}")

    out = {"experiment": "E-73",
           "model_verdicts": dict(c),
           "new_types": news,
           "rule_covered": covered,
           "rule_missed": missed,
           "missed_items": [{"rid": r, "verdict": v, "text": t}
                            for r, v, t in missed_items]}
    with open(os.path.join(RESULTS, "e73_discovery.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e73_discovery.json')}")


if __name__ == "__main__":
    main()
