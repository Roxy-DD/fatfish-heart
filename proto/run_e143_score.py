"""
E-143 评分：模型判链 vs 标准答案

同时验证模型指出的关键现象：
  "余弦排序与语义同链几乎不相关，甚至反向"
  → 如果成立，说明链式展开**必须做语义判别**，规则阈值做不到
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
TASK = os.path.join(_HERE, "work", "e143")
RESULTS = os.path.join(_HERE, "results")


def main():
    packs = json.load(open(os.path.join(TASK, "packs.json"), encoding="utf-8"))
    model = {r["cid"]: r for r in
             json.load(open(os.path.join(TASK, "model.json"), encoding="utf-8"))}

    print("=" * 96)
    print("E-143 链式展开：模型判链准确率")
    print("=" * 96)
    print(f"\n{'cid':<6}{'命中':<8}{'gold 排名':<12}{'模型选的':<40}{'说明'}")
    print("-" * 96)

    hit = 0
    details = []
    for p in packs:
        cid = p["cid"]
        gold = p["gold_chain_target"]
        m = model.get(cid, {})
        chain = m.get("chain") or []

        ok = gold in chain
        hit += 1 if ok else 0

        picked = chain[0][:38] if chain else "(空)"
        rank = p["gold_rank_in_candidates"]
        print(f"{cid:<6}{'✓' if ok else '✗':<8}"
              f"{('#' + str(rank)) if rank else '-':<12}"
              f"{picked:<40}{p['desc'][:20]}")

        details.append({
            "cid": cid, "desc": p["desc"],
            "gold": gold, "gold_rank": rank,
            "picked": chain, "ok": ok,
            "reason": m.get("reason", ""),
        })

    n = len(packs)
    print("-" * 96)
    print(f"\n  模型判链准确率: {hit}/{n} = {hit/n:.4f}")

    # ---------- 验证模型的关键观察 ----------
    print("\n" + "=" * 96)
    print("验证模型的关键观察：余弦排序能否代替语义判别？")
    print("=" * 96)

    print(f"\n{'cid':<6}{'gold 排名':<12}{'Top-1 是否 gold':<18}{'Top-3 是否含 gold'}")
    print("-" * 96)

    top1_hit = 0
    top3_hit = 0
    for p in packs:
        rank = p["gold_rank_in_candidates"]
        t1 = (rank == 1)
        t3 = (0 < rank <= 3)
        top1_hit += 1 if t1 else 0
        top3_hit += 1 if t3 else 0
        print(f"{p['cid']:<6}{('#' + str(rank)) if rank else '-':<12}"
              f"{'✅' if t1 else '❌':<18}{'✅' if t3 else '❌'}")

    print("-" * 96)
    print(f"\n  如果只用余弦 Top-1:      {top1_hit}/{n} = {top1_hit/n:.4f}")
    print(f"  如果只用余弦 Top-3:      {top3_hit}/{n} = {top3_hit/n:.4f}")
    print(f"  模型语义判别:            {hit}/{n} = {hit/n:.4f}")

    gain_vs_top1 = hit - top1_hit
    gain_vs_top3 = hit - top3_hit
    print(f"\n  模型 vs Top-1: {gain_vs_top1:+d} 条")
    print(f"  模型 vs Top-3: {gain_vs_top3:+d} 条")

    # ---------- 结论 ----------
    print("\n" + "=" * 96)
    print("结论")
    print("=" * 96)

    if hit == n:
        print("  → ✅ 模型判链 7/7，链式展开可以用「系统给候选 + 模型判链」实现")
    else:
        print(f"  → 模型漏判 {n - hit} 条")

    if top1_hit < n:
        print(f"  → ❌ 余弦 Top-1 只能对 {top1_hit}/{n}，不能代替语义判别")
        print(f"     这与模型自己的观察一致：「余弦排序与同链几乎不相关，甚至反向」")
    else:
        print(f"  → 余弦 Top-1 也能全对，但样本量小，不能据此下结论")

    print(f"\n  修复前（规则阈值 DECAY=0.75）：0/7（目标余弦仅 0.42~0.52，全被筛掉）")
    print(f"  修复后（系统给候选 + 模型判链）：{hit}/7")

    out = {"experiment": "E-143b", "n": n,
           "model_hit": hit, "model_acc": round(hit / n, 4),
           "cosine_top1_hit": top1_hit,
           "cosine_top3_hit": top3_hit,
           "cosine_top1_acc": round(top1_hit / n, 4),
           "details": details}
    with open(os.path.join(RESULTS, "e143_chain_score.json"),
              "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e143_chain_score.json')}")


if __name__ == "__main__":
    main()
