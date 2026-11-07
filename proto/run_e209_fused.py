"""
E-209 落地：融合精排封装 + 全量收益重算 + 红线回归

融合逻辑：LLM 精排的 picks ∪ cross-encoder 精排的 picks
但生产里"LLM 精排"是插件层的事，这里先做离线验证：
  用已保存的 LLM picks（e196 4个part）+ CE fp16 GPU picks（e207）算融合收益。

最终产出：一个可复现的"融合精排"报告，以及是否落地的建议。
"""
from __future__ import annotations
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
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
    # 合并 LLM picks（4个part）
    llm_picks = {}
    for i in range(4):
        p = os.path.join(TASK, f"rerank_picks_part{i}.json")
        if os.path.exists(p):
            for r in json.load(open(p, encoding="utf-8")):
                llm_picks[r["cid"]] = r

    gold = json.load(open(os.path.join(TASK, "rerank_gold.json"), encoding="utf-8"))

    # 全量收益（基线 1150，可救回 244）
    baseline = 1150
    total = 1982

    # 逐题算融合命中
    fused_hit = 0
    llm_hit = 0
    ce_hit = 0
    n = 0

    # 用 CE 结果（e207 有 hit_cids）
    e207 = json.load(open(os.path.join(RESULTS, "e207_fp16_gpu.json"), encoding="utf-8"))
    ce_hit_cids = set(e207["hit_cids"])

    for g in gold:
        cid = g["cid"]
        n += 1
        # LLM 命中判断
        pk = llm_picks.get(cid, {}).get("picks") or []
        ep_to_text = {c["ep_id"]: c["text"] for c in g["candidates"]}
        pick_texts = [ep_to_text.get(p, "") for p in pk if ep_to_text.get(p)]
        l_ok = False
        for pt in pick_texts:
            pc = strip_meta(pt)
            for et in g["evidence_texts"]:
                ec = strip_meta(et)
                if len(pc) > 25 and len(ec) > 25 and (pc in ec or ec in pc or pc[:40] == ec[:40]):
                    l_ok = True
                    break
            if l_ok:
                break

        c_ok = cid in ce_hit_cids
        llm_hit += 1 if l_ok else 0
        ce_hit += 1 if c_ok else 0
        fused_hit += 1 if (l_ok or c_ok) else 0

    print("=" * 94)
    print("E-209 融合精排落地验证")
    print("=" * 94)
    print(f"\n  可救回题 {n} 中:")
    print(f"    LLM 命中: {llm_hit} = {llm_hit/n:.4f}")
    print(f"    CE 命中:  {ce_hit} = {ce_hit/n:.4f}")
    print(f"    融合命中: {fused_hit} = {fused_hit/n:.4f}")

    # 全量收益
    fused_recall = (baseline + fused_hit) / total
    llm_recall = (baseline + llm_hit) / total
    print(f"\n  全量 recall@10:")
    print(f"    基线:         {baseline/total:.4f}")
    print(f"    +LLM 精排:    {llm_recall:.4f}")
    print(f"    +融合精排:    {fused_recall:.4f}")

    out = {"experiment": "E-209", "n": n,
           "llm_hit": llm_hit, "ce_hit": ce_hit, "fused_hit": fused_hit,
           "fused_rate": round(fused_hit/n, 4),
           "baseline": round(baseline/total, 4),
           "llm_recall": round(llm_recall, 4),
           "fused_recall": round(fused_recall, 4)}
    with open(os.path.join(RESULTS, "e209_fused_final.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e209_fused_final.json')}")


if __name__ == "__main__":
    main()
