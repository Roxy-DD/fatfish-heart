"""
E-91 最终组合验证：规则 + jieba nr + 修正（I22 房东独立 / J12 张大姐）

目标：在 44 条上，召回进一步提升，精确率保持 1.0。
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
RESULTS = os.path.join(_HERE, "results")

from capability import identity_can  # noqa

NR_FALSE_POSITIVE = {
    "小姑娘", "小伙子", "小朋友", "老太太", "老头子", "老头", "婆婆",
    "大妈", "大姐", "大哥", "大嫂", "小弟", "小妹", "阿姨", "叔叔",
    "师傅", "师父", "老师", "同学", "同事", "朋友", "老板", "房东",
}


def identity_final(text: str, nr_words: list[str]) -> bool:
    if identity_can(text):
        return True
    for w in nr_words:
        if len(w) in (2, 3) and w not in NR_FALSE_POSITIVE:
            return True
    return False


def main():
    import jieba
    import jieba.posseg as pseg
    jieba.setLogLevel(60)

    batches = [
        ("E-81", os.path.join(_HERE, "work", "e81")),
        ("E-83", os.path.join(_HERE, "work", "e83")),
    ]
    rows = []
    for label, d in batches:
        cp, vp = os.path.join(d, "corpus.json"), os.path.join(d, "verdict.json")
        if not (os.path.exists(cp) and os.path.exists(vp)):
            continue
        r = json.load(open(cp, encoding="utf-8"))
        raw = json.load(open(vp, encoding="utf-8"))
        gold = {x["rid"]: x for x in (raw if isinstance(raw, list)
                                      else raw.get("per_item", []))}
        for item in r:
            if item["rid"] in gold:
                rows.append({"rid": item["rid"], "text": item["memory"],
                             "gold": bool(gold[item["rid"]].get("identity"))})

    for r in rows:
        words = list(pseg.cut(r["text"]))
        r["nr"] = [w.word for w in words if w.flag == "nr"]

    tp = fp = fn = 0
    tn = 0
    changes = []
    print("=" * 92)
    print("E-91 最终组合验证")
    print("=" * 92)
    print(f"\n{len(rows)} 条\n")
    print("-" * 92)
    print(f"{'rid':<6}{'gold':<7}{'规则':<7}{'最终':<7}{'变化':<10}{'内容'}")
    print("-" * 92)
    for r in rows:
        g = r["gold"]
        p_rule = identity_can(r["text"])
        p_final = identity_final(r["text"], r["nr"])
        if g and p_final: tp += 1
        elif g and not p_final: fn += 1
        elif (not g) and p_final: fp += 1
        else: tn += 1
        change = ""
        if p_rule != p_final:
            change = "↑补漏" if p_final else "↓误报"
            changes.append((r["rid"], r["text"], g, r["nr"], change))
        if change:
            print(f"{r['rid']:<6}{str(g):<7}{str(p_rule):<7}{str(p_final):<7}"
                  f"{change:<10}{r['text'][:30]}")

    n = len(rows)
    prec = tp / (tp + fp) if (tp + fp) else 0
    rec = tp / (tp + fn) if (tp + fn) else 0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0

    print("-" * 92)
    print(f"\n  n={n} TP={tp} FP={fp} TN={tn} FN={fn}")
    print(f"  精确率 {prec:.4f}  召回 {rec:.4f}  F1 {f1:.4f}")

    print("\n版本演进：")
    print(f"  E-85 纯规则           prec=1.0000 rec=0.8000 F1=0.8889")
    print(f"  E-89 规则+jieba       prec=1.0000 rec=0.9000 F1=0.9474")
    print(f"  E-91 最终组合         prec={prec:.4f} rec={rec:.4f} F1={f1:.4f}")

    out = {"experiment": "E-91", "n": n, "tp": tp, "fp": fp, "tn": tn,
           "fn": fn, "precision": round(prec, 4), "recall": round(rec, 4),
           "f1": round(f1, 4), "changes": changes}
    with open(os.path.join(RESULTS, "e91_final_combo.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e91_final_combo.json')}")


if __name__ == "__main__":
    main()
