"""
E-212 诊断：53 个"检索不到"的多跳题，问题与证据的词汇鸿沟

假设（E-179 已给线索）：多跳题答案词覆盖率仅 15.8%，
即问题用词（national park/political leaning）与证据用词（camping/religious conservatives）
严重不匹配，导致向量检索失效。

方法：抽样看 10 个"检索不到"的多跳题，打印问题 + 答案 + 证据原文，
      人工确认是否是"词汇鸿沟"问题。
"""
from __future__ import annotations
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e212")
os.makedirs(TASK, exist_ok=True)
DATA = os.path.join(_HERE, "work", "e165", "locomo10.json")


def main():
    data = json.load(open(DATA, encoding="utf-8"))

    print("=" * 94)
    print("E-212 多跳题「检索不到」的词汇鸿沟诊断")
    print("=" * 94)

    samples = []
    for conv in data:
        c = conv.get("conversation") or {}
        # mark -> text
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
            ans = q.get("answer") or ""
            ev = q.get("evidence") or []
            if not isinstance(ev, list):
                ev = [ev]
            ev = [str(e).strip() for e in ev if e]
            if not question or not ev:
                continue
            samples.append({
                "q": question, "ans": str(ans)[:60],
                "ev_texts": [mark_text.get(e, "")[:120] for e in ev],
            })

    print(f"\n多跳题总数: {len(samples)}")
    print("\n前 12 个多跳题的问题-答案-证据对照：\n")

    for i, s in enumerate(samples[:12]):
        print(f"[{i}] Q: {s['q'][:70]}")
        print(f"    A: {s['ans']}")
        for t in s["ev_texts"][:2]:
            print(f"    证据: {t}")
        print()

    with open(os.path.join(TASK, "multihop_samples.json"), "w",
              encoding="utf-8") as f:
        json.dump(samples, f, ensure_ascii=False, indent=1)
    print(f"完整 {len(samples)} 题样本 → {TASK}/multihop_samples.json")


if __name__ == "__main__":
    main()
