"""
E-100 诊断能力范围的过拟合在哪几个维度

E-79 发现：能力范围规则（infer_scope）在全新语料上 0.7381，
  远低于我设计语料上的 0.83+。

本实验：用 E-79 的 14 条全新语料 + E-77 的 10 条，
对照模型标注，逐维度看哪些维度规则退化最严重。
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
RESULTS = os.path.join(_HERE, "results")

from capability import infer_scope, DIMS


def load_model_scope(path, corpus_key="corpus.json", scope_key="scope.json"):
    """加载模型标注的能力范围，兼容 can/cannot 和 scope 两种格式"""
    if not (os.path.exists(os.path.join(path, corpus_key)) and
            os.path.exists(os.path.join(path, scope_key))):
        return None
    corpus = json.load(open(os.path.join(path, corpus_key), encoding="utf-8"))
    scope_raw = json.load(open(os.path.join(path, scope_key), encoding="utf-8"))
    gold = {}
    for r in scope_raw:
        if "scope" in r:
            gold[r["rid"]] = {d: bool(r["scope"].get(d)) for d in DIMS}
        elif "can" in r:
            can = set(r.get("can") or [])
            gold[r["rid"]] = {d: (d in can) for d in DIMS}
    return [(r["rid"], r["memory"], gold.get(r["rid"], {})) for r in corpus]


def main():
    print("=" * 92)
    print("E-100 诊断能力范围过拟合的维度")
    print("=" * 92)

    batches = [
        ("E-77(我设计)", os.path.join(_HERE, "work", "e77")),
        ("E-79(全新)", os.path.join(_HERE, "work", "e79")),
    ]

    all_items = []
    for label, d in batches:
        items = load_model_scope(d)
        if items:
            for rid, text, gold in items:
                all_items.append((label, rid, text, gold))

    # 逐维度统计
    print(f"\n{len(all_items)} 条（E-77 + E-79）\n")
    print("-" * 92)
    print(f"{'维度':<14}{'我设计集':<12}{'全新集':<12}{'差距':<10}{'判读'}")
    print("-" * 92)

    per_dim = {}
    for dim in DIMS:
        for label in ["E-77(我设计)", "E-79(全新)"]:
            subset = [it for it in all_items if it[0] == label and it[3]]
            agree = 0
            total = 0
            for _, _, text, gold in subset:
                pred = infer_scope(text)[dim]
                if pred == bool(gold.get(dim)):
                    agree += 1
                total += 1
            per_dim.setdefault(dim, {})[label] = agree / total if total else 0

    for dim in DIMS:
        a = per_dim[dim].get("E-77(我设计)", 0)
        b = per_dim[dim].get("E-79(全新)", 0)
        gap = a - b
        flag = "⚠过拟合" if gap > 0.15 else ("退化" if gap > 0.05 else "稳定")
        print(f"{dim:<14}{a:<12.3f}{b:<12.3f}{gap:<+10.3f}{flag}")
    print("-" * 92)

    # 找出退化最严重的维度
    worst = sorted(DIMS, key=lambda d: per_dim[d].get("E-79(全新)", 0))[0]
    print(f"\n全新集上最差的维度：{worst}（{per_dim[worst].get('E-79(全新)',0):.3f}）")

    # 看这个维度在全新集上错在哪
    print(f"\n{worst} 维度在全新集上的具体错误：")
    for label, rid, text, gold in all_items:
        if label == "E-79(全新)" and gold:
            pred = infer_scope(text)[worst]
            g = bool(gold.get(worst))
            if pred != g:
                print(f"  [{rid}] 模型={g} 规则={pred}  {text[:44]}")

    out = {"experiment": "E-100", "per_dim": per_dim}
    with open(os.path.join(RESULTS, "e100_overfit_diag.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e100_overfit_diag.json')}")


if __name__ == "__main__":
    main()
