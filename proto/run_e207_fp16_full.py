"""E-207 fp16 GPU cross-encoder 精排全量（含完整 cid 明细，供融合分析）"""
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
    gold = json.load(open(os.path.join(TASK, "rerank_gold.json"), encoding="utf-8"))

    print("=" * 94)
    print("E-207 fp16 GPU cross-encoder 精排全量")
    print("=" * 94)

    rk = CrossEncoderReranker()
    _ = rk.score("warmup", ["hello"])
    print("  provider:", rk._sess.get_providers())

    t0 = time.perf_counter()
    hit = 0
    n = 0
    by_cat = {}
    hit_cids = []
    miss_cids = []

    for g in gold:
        cid = g["cid"]
        cat = g.get("category", "?")
        question = g["question"]
        texts = [c["text"] for c in g["candidates"]]

        scores = rk.score(question, texts)
        order = sorted(range(len(scores)), key=lambda i: -scores[i])[:3]
        picked = [texts[i] for i in order]

        n += 1
        ok = False
        for pt in picked:
            pc = strip_meta(pt)
            for et in g["evidence_texts"]:
                ec = strip_meta(et)
                if len(pc) > 25 and len(ec) > 25:
                    if pc in ec or ec in pc or pc[:40] == ec[:40]:
                        ok = True
                        break
            if ok:
                break

        hit += 1 if ok else 0
        by_cat[cat] = by_cat.get(cat, [0, 0])
        by_cat[cat][1] += 1
        by_cat[cat][0] += 1 if ok else 0
        (hit_cids if ok else miss_cids).append(cid)

    dt = time.perf_counter() - t0
    rate = hit / max(1, n)

    print(f"\n  fp16 GPU 命中: {hit}/{n} = {rate:.4f}")
    print(f"  对比 int8 CPU: 190/244 = 0.7787")
    print(f"  对比 LLM 精排: 193/244 = 0.7910")
    print(f"  耗时: {dt:.1f}s（平均 {dt/max(1,n)*1000:.0f}ms/题）")

    print(f"\n  分题型:")
    for c in sorted(by_cat):
        h, t = by_cat[c]
        print(f"    类别 {c}: {h}/{t} = {h/t:.4f}")

    out = {"experiment": "E-207", "n": n, "hit": hit,
           "rate": round(rate, 4),
           "llm_rate": 0.7910, "int8_rate": 0.7787,
           "elapsed_s": round(dt, 1),
           "ms_per_q": round(dt / max(1, n) * 1000, 0),
           "by_cat": by_cat,
           "hit_cids": hit_cids, "miss_cids": miss_cids}
    with open(os.path.join(RESULTS, "e207_fp16_gpu.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e207_fp16_gpu.json')}")


if __name__ == "__main__":
    main()
