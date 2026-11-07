"""
E-101 用 E-91 的 identity 组合重测 E-79 全新集，确认 identity 是否真闭环

E-100 用旧 infer_scope 的 identity 测 E-79 全新集得 0.643，
但那 5 条错误里，E-91 已经解决了部分。本实验确认。

E-91 的 identity 最终版 = 规则(identity_can) + jieba nr 补充
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
RESULTS = os.path.join(_HERE, "results")

from capability import identity_can, extract_nr  # noqa

NR_FALSE_POSITIVE = {
    "小姑娘", "小伙子", "小朋友", "老太太", "老头子", "老头", "婆婆",
    "大妈", "大姐", "大哥", "大嫂", "小弟", "小妹", "阿姨", "叔叔",
    "师傅", "师父", "老师", "同学", "同事", "朋友", "老板", "房东",
}


def identity_final(text):
    if identity_can(text):
        return True
    for w in extract_nr(text):
        if len(w) in (2, 3) and w not in NR_FALSE_POSITIVE:
            return True
    return False


def main():
    import jieba
    jieba.setLogLevel(60)

    print("=" * 92)
    print("E-101 用 E-91 identity 组合重测 E-79 全新集")
    print("=" * 92)

    d = os.path.join(_HERE, "work", "e79")
    corpus = json.load(open(os.path.join(d, "corpus.json"), encoding="utf-8"))
    scope = {r["rid"]: r for r in
             json.load(open(os.path.join(d, "scope.json"), encoding="utf-8"))}

    tp = fp = tn = fn = 0
    print("\n" + "-" * 92)
    print(f"{'rid':<6}{'模型':<8}{'旧规则':<8}{'E91组合':<10}{'变化':<10}{'内容'}")
    print("-" * 92)
    for r in corpus:
        rid = r["rid"]
        if rid not in scope:
            continue
        g = bool(scope[rid].get("scope", {}).get("identity"))
        old = identity_can(r["memory"])
        new = identity_final(r["memory"])
        if g and new: tp += 1
        elif g and not new: fn += 1
        elif (not g) and new: fp += 1
        else: tn += 1
        change = ""
        if old != new:
            change = "↑jieba补" if new else "↓"
        if old != g or new != g:
            print(f"{rid:<6}{str(g):<8}{str(old):<8}{str(new):<10}{change:<10}"
                  f"{r['memory'][:34]}")

    n = len(corpus)
    acc = (tp + tn) / n
    prec = tp / (tp + fp) if (tp + fp) else 0
    rec = tp / (tp + fn) if (tp + fn) else 0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0

    print("-" * 92)
    print(f"\n  TP={tp} FP={fp} TN={tn} FN={fn}")
    print(f"  旧规则：acc={sum(1 for r in corpus if r['rid'] in scope and identity_can(r['memory']) == bool(scope[r['rid']]['scope']['identity']))/n:.4f}")
    print(f"  E91组合：acc={acc:.4f} prec={prec:.4f} rec={rec:.4f} F1={f1:.4f}")

    out = {"experiment": "E-101", "n": n, "tp": tp, "fp": fp, "tn": tn,
           "fn": fn, "accuracy": round(acc, 4), "precision": round(prec, 4),
           "recall": round(rec, 4), "f1": round(f1, 4)}
    with open(os.path.join(RESULTS, "e101_identity_e79.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e101_identity_e79.json')}")


if __name__ == "__main__":
    main()
