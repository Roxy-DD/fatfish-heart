"""
E-200 cross-encoder 精排 vs LLM 精排 对比评分

用 cross-encoder（onnx int8）对 244 个可救回题打分，
取每题候选里分数最高的 1-3 条，判定是否命中 evidence。

直接对比：
  LLM 精排：193/244 = 79.1%
  cross-encoder 精排：本实验
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e196")
RESULTS = os.path.join(_HERE, "results")


def strip_meta(s):
    s = s or ""
    s = re.sub(r"\[[^\]]*\]", " ", s)
    s = re.sub(r"^\s*(?:Caroline|Melanie|Jon|Gina|John|Maria|Joanna|Nate|Tim|"
               r"Audrey|Andrew|James|Deborah|Jolene|Evan|Sam|Calvin|Dave)\s*:\s*",
               " ", s, flags=re.I)
    return re.sub(r"\s+", " ", s).strip()


def main():
    from rerank_v1 import CrossEncoderReranker

    gold = json.load(open(os.path.join(TASK, "rerank_gold.json"),
                          encoding="utf-8"))

    print("=" * 94)
    print("E-200 cross-encoder 精排 vs LLM 精排")
    print("=" * 94)

    rk = CrossEncoderReranker()
    print("  加载模型...")
    t0 = time.perf_counter()

    hit = 0
    n = 0
    by_cat = {}
    details = []
    top1_hit = 0

    for g in gold:
        cid = g["cid"]
        cat = g.get("category", "?")
        question = g["question"]
        cands = g["candidates"]
        texts = [c["text"] for c in cands]

        # cross-encoder 打分
        scores = rk.score(question, texts)
        # 取 top-3 作为精排结果
        order = sorted(range(len(scores)), key=lambda i: -scores[i])[:3]
        picked_texts = [texts[i] for i in order]

        n += 1
        ok = False
        for pt in picked_texts:
            pt_core = strip_meta(pt)
            for et in g["evidence_texts"]:
                et_core = strip_meta(et)
                if len(pt_core) > 25 and len(et_core) > 25:
                    if pt_core in et_core or et_core in pt_core:
                        ok = True
                        break
                    if pt_core[:40] == et_core[:40]:
                        ok = True
                        break
            if ok:
                break

        hit += 1 if ok else 0
        by_cat[cat] = by_cat.get(cat, [0, 0])
        by_cat[cat][1] += 1
        by_cat[cat][0] += 1 if ok else 0

        # top-1 命中（cross-encoder 分数最高的一条）
        top1_text = texts[order[0]] if order else ""
        top1_core = strip_meta(top1_text)
        top1_ok = any(
            (top1_core in strip_meta(et) or strip_meta(et) in top1_core)
            for et in g["evidence_texts"] if len(top1_core) > 25
            and len(strip_meta(et)) > 25)
        top1_hit += 1 if top1_ok else 0

        if not ok and len(details) < 8:
            details.append({
                "cid": cid, "cat": cat, "q": question[:45],
                "ev": strip_meta(g["evidence_texts"][0])[:45]
                if g["evidence_texts"] else "",
                "top1": top1_core[:45],
            })

    dt = time.perf_counter() - t0
    rate = hit / max(1, n)

    print(f"\n  cross-encoder 精排命中: {hit}/{n} = {rate:.4f}")
    print(f"  LLM 精排命中（对比）:   193/244 = 0.7910")
    print(f"  top-1 命中: {top1_hit}/{n} = {top1_hit/max(1,n):.4f}")
    print(f"  耗时: {dt:.1f}s（含首次加载），平均 {dt/max(1,n)*1000:.0f}ms/题")

    print(f"\n  分题型命中:")
    print(f"  {'类别':<10}{'命中/总':<14}{'命中率'}")
    print("-" * 40)
    for c in sorted(by_cat):
        h, t = by_cat[c]
        print(f"  {c:<10}{f'{h}/{t}':<14}{h/t:.4f}")

    print(f"\n  未命中案例:")
    for d in details:
        print(f"    [{d['cid']}] cat={d['cat']} {d['q']}")
        print(f"      ev:    {d['ev']}")
        print(f"      top1:  {d['top1']}")

    out = {"experiment": "E-200", "n": n, "hit": hit,
           "rate": round(rate, 4), "top1_hit": top1_hit,
           "top1_rate": round(top1_hit / max(1, n), 4),
           "llm_rate": 0.7910,
           "by_cat": by_cat,
           "elapsed_s": round(dt, 1),
           "ms_per_q": round(dt / max(1, n) * 1000, 0)}
    with open(os.path.join(RESULTS, "e200_cross_encoder.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e200_cross_encoder.json')}")


if __name__ == "__main__":
    main()
