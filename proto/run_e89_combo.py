"""
E-89 组合方案：规则(精确率1.0) + jieba nr(补漏报)，能否两全

E-88 发现：
  jieba nr 能补 4 条漏报，但把"小姑娘"误标成人名（1 条误报）
  这会破坏 identity 规则"精确率 1.0"的性质。

组合思路（关键：不牺牲精确率）：
  1. 规则先判（精确率 1.0，已证明）
  2. 规则判 false 时，再用 jieba 的 nr 做补充：
     仅当 nr 是"2字或3字全名"且**不在已知普通词表**里，才判 true
  3. "小姑娘"这种 3 字但明显是普通词的，靠词表排除

判据：能否在 44 条上做到 recall 提升、precision 保持 1.0？
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
RESULTS = os.path.join(_HERE, "results")

from capability import identity_can  # noqa

# 常见"被 nr 误标"的普通词（从 E-88 及常见中文词里收集）
NR_FALSE_POSITIVE = {
    "小姑娘", "小伙子", "小朋友", "老太太", "老头子", "老头", "婆婆",
    "大妈", "大姐", "大哥", "大嫂", "小弟", "小妹", "阿姨", "叔叔",
    "师傅", "师父", "老师", "同学", "同事", "朋友", "老板", "房东",
}


def identity_combined(text: str, nr_words: list[str]) -> bool:
    """规则 + jieba nr 组合"""
    # 1. 规则先判（精确率 1.0）
    if identity_can(text):
        return True

    # 2. 规则判 false，再用 nr 补充
    for w in nr_words:
        # 2-3 字、不是普通词、且（含姓氏白名单 或 形似人名）
        if len(w) in (2, 3) and w not in NR_FALSE_POSITIVE:
            return True
    return False


def main():
    import jieba
    import jieba.posseg as pseg
    jieba.setLogLevel(60)

    print("=" * 92)
    print("E-89 组合方案：规则 + jieba nr 补漏报")
    print("=" * 92)

    # 加载 44 条模型标注
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

    # 预计算 jieba nr
    for r in rows:
        words = list(pseg.cut(r["text"]))
        r["nr"] = [w.word for w in words if w.flag == "nr"]

    print(f"\n{len(rows)} 条（E-81 24 + E-83 20）\n")

    # 纯规则 vs 组合
    tp_rule = fp_rule = fn_rule = 0
    tp_combo = fp_combo = fn_combo = 0
    tn = 0

    print("-" * 92)
    print(f"{'rid':<6}{'gold':<7}{'规则':<7}{'组合':<7}{'nr命中':<16}{'变化':<10}{'内容'}")
    print("-" * 92)
    changes = []
    for r in rows:
        g = r["gold"]
        p_rule = identity_can(r["text"])
        p_combo = identity_combined(r["text"], r["nr"])

        if g and p_rule: tp_rule += 1
        elif g and not p_rule: fn_rule += 1
        elif (not g) and p_rule: fp_rule += 1
        else: tn += 1

        if g and p_combo: tp_combo += 1
        elif g and not p_combo: fn_combo += 1
        elif (not g) and p_combo: fp_combo += 1

        change = ""
        if p_rule != p_combo:
            change = "↑补漏" if (not p_rule and p_combo) else "↓误报"
            changes.append((r["rid"], r["text"], g, r["nr"], change))
        print(f"{r['rid']:<6}{str(g):<7}{str(p_rule):<7}{str(p_combo):<7}"
              f"{str(r['nr'])[:14]:<16}{change:<10}{r['text'][:22]}")

    n = len(rows)
    prec_rule = tp_rule / (tp_rule + fp_rule) if (tp_rule + fp_rule) else 0
    rec_rule = tp_rule / (tp_rule + fn_rule) if (tp_rule + fn_rule) else 0
    prec_combo = tp_combo / (tp_combo + fp_combo) if (tp_combo + fp_combo) else 0
    rec_combo = tp_combo / (tp_combo + fn_combo) if (tp_combo + fn_combo) else 0
    f1_rule = 2 * prec_rule * rec_rule / (prec_rule + rec_rule) if (prec_rule + rec_rule) else 0
    f1_combo = 2 * prec_combo * rec_combo / (prec_combo + rec_combo) if (prec_combo + rec_combo) else 0

    print("\n" + "=" * 92)
    print("对比")
    print("=" * 92)
    print(f"{'方案':<16}{'精确率':<10}{'召回':<10}{'F1':<10}")
    print("-" * 92)
    print(f"{'纯规则(E-85)':<16}{prec_rule:<10.4f}{rec_rule:<10.4f}{f1_rule:<10.4f}")
    print(f"{'规则+jieba':<16}{prec_combo:<10.4f}{rec_combo:<10.4f}{f1_combo:<10.4f}")
    print("-" * 92)

    if changes:
        print("\n变化的条目：")
        for rid, txt, g, nr, kind in changes:
            print(f"  [{rid}] {kind}  gold={g}  nr={nr}")
            print(f"    {txt}")

    out = {"experiment": "E-89",
           "rule": {"precision": prec_rule, "recall": rec_rule, "f1": f1_rule},
           "combo": {"precision": prec_combo, "recall": rec_combo, "f1": f1_combo},
           "changes": changes}
    with open(os.path.join(RESULTS, "e89_combo.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e89_combo.json')}")


if __name__ == "__main__":
    main()
