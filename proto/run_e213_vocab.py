"""
E-213 量化词汇鸿沟：问题实词有多少出现在证据里

如果问题实词几乎不出现，就是词汇鸿沟 → LLM 改写可救
如果问题实词大量出现但检索不到 → 不是词汇鸿沟，是别的
"""
from __future__ import annotations
import json
import os
import re
import sys
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(_HERE, "work", "e165", "locomo10.json")

STOP = set("""
a an the and or but if then than that this these those
is are was were be been being am do does did doing done
have has had having will would shall should can could may might must
i you he she it we they me him her us them my your his its our their
of in on at to from by with without for about as into over under
what which who whom whose when where why how
not no yes so such very just also too only more most much many
there here all any some each every both few other another
would likely pursue what which why how
""".split())


def content_words(s):
    s = str(s or "").lower()
    return [w for w in re.findall(r"[a-z]+|\d+", s)
            if len(w) >= 3 and w not in STOP]


def main():
    data = json.load(open(DATA, encoding="utf-8"))

    # 收集多跳题的 问题实词 vs 证据实词
    rows = []
    for conv in data:
        c = conv.get("conversation") or {}
        mark_text = {}
        j = 1
        while f"session_{j}" in c:
            for ti, turn in enumerate(c[f"session_{j}"] or [], start=1):
                spk = turn.get("speaker") or ""
                txt = turn.get("text") or turn.get("content") or ""
                txt = re.sub(r"\s+", " ", str(txt)).strip()
                if len(txt) >= 5:
                    mark_text[f"D{j}:{ti}"] = f"{spk}: {txt}"
            j += 1

        for q in conv.get("qa") or []:
            if str(q.get("category")) != "3":
                continue
            question = q.get("question") or ""
            ev = q.get("evidence") or []
            if not isinstance(ev, list):
                ev = [ev]
            ev = [str(e).strip() for e in ev if e]
            if not question or not ev:
                continue

            q_words = set(content_words(question))
            ev_text = " ".join(mark_text.get(e, "") for e in ev)
            ev_words = set(content_words(ev_text))

            overlap = q_words & ev_words
            rate = len(overlap) / len(q_words) if q_words else 0
            rows.append({
                "q": question, "q_words": sorted(q_words),
                "overlap": sorted(overlap), "rate": rate,
            })

    print("=" * 94)
    print("E-213 词汇鸿沟量化")
    print("=" * 94)

    n = len(rows)
    print(f"\n多跳题 {n} 题")

    rates = [r["rate"] for r in rows]
    import statistics
    print(f"\n问题实词出现在证据中的比例：")
    print(f"  均值: {statistics.mean(rates):.3f}")
    print(f"  中位: {statistics.median(rates):.3f}")

    # 分布
    buckets = Counter()
    for r in rates:
        if r == 0:
            buckets["0%（完全鸿沟）"] += 1
        elif r < 0.25:
            buckets["0-25%"] += 1
        elif r < 0.5:
            buckets["25-50%"] += 1
        elif r < 0.75:
            buckets["50-75%"] += 1
        else:
            buckets["75-100%"] += 1

    print(f"\n分布:")
    for b in ["0%（完全鸿沟）", "0-25%", "25-50%", "50-75%", "75-100%"]:
        c = buckets.get(b, 0)
        print(f"  {b:<16}{c:<6}{c/n:.1%}")

    # 完全鸿沟的样本
    zero = [r for r in rows if r["rate"] == 0]
    print(f"\n完全鸿沟（问题实词 0% 出现在证据）: {len(zero)} 题")
    for r in zero[:5]:
        print(f"  Q: {r['q'][:60]}")
        print(f"    问题词: {r['q_words'][:8]}")

    out = {"experiment": "E-213", "n": n,
           "mean_rate": round(statistics.mean(rates), 3),
           "median_rate": round(statistics.median(rates), 3),
           "buckets": dict(buckets),
           "zero_mismatch": len(zero)}
    with open(os.path.join(_HERE, "results", "e213_vocab_gap.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 results/e213_vocab_gap.json")


if __name__ == "__main__":
    main()
