"""
E-179 关键边界判断：多跳题是不是"检索做不到的推理题"

E-178 发现：多跳题 61%（56/92）"一条 evidence 都找不到"。

看失败案例发现一个重要现象：
  Q: "Would Caroline pursue writing as a career option?"
  evidence D7:5: "I'm still looking into counseling and mental heal..."
  evidence D7:9: "Thanks so much, Mel! Seeing this pic just made me appreciate..."
  → 证据里根本没有 "writing" 这个词！

  Q: "Would Melanie be more interested in a national park or a state park?"
  evidence: "We always look forward to our family camping trip..."
  → 证据说 camping，问题问 park

假设：**多跳题（category 3）本质是"推理题"，不是"检索题"。**
      答案需要从证据推理得出，而非从证据直接读出。
      → 检索系统在这种题上天然有上限，不是实现缺陷。

验证方法：
  ① 量化：答案的关键词有多少出现在 evidence 原文里？
     如果答案词不在证据里 → 检索不可能"找到答案"，只能"找到素材"
  ② 对比：单跳题（category 1）的答案词在 evidence 里的比例
     如果单跳高、多跳低 → 证明多跳是推理题

这一步很重要：它能确定"哪些题是检索该负责的，哪些不是"，
避免把推理题的失败算成检索的失败。
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e179")
os.makedirs(TASK, exist_ok=True)
RESULTS = os.path.join(_HERE, "results")
DATA = os.path.join(_HERE, "work", "e165", "locomo10.json")

CAT = {"1": "单跳", "2": "时间", "3": "多跳", "4": "常识", "5": "对抗"}

STOP = set("""
a an the and or but if then than that this these those
is are was were be been being am do does did doing done
have has had having will would shall should can could may might must
i you he she it we they me him her us them my your his its our their
of in on at to from by with without for about as into over under
what which who whom whose when where why how
not no yes so such very just also too only more most much many
there here all any some each every both few other another
""".split())


def content_words(s):
    """抽实词（容忍 int 类型的答案）"""
    s = str(s or "").lower()
    words = [w for w in re.findall(r"[a-z]+|\d+", s)
             if len(w) >= 3 and w not in STOP]
    return set(words)


def main():
    data = json.load(open(DATA, encoding="utf-8"))

    print("=" * 94)
    print("E-179 边界判断：多跳题是检索题还是推理题")
    print("=" * 94)

    # 统计每类：答案词在 evidence 里的覆盖率
    by_cat = defaultdict(lambda: {"n": 0, "full": 0, "part": 0,
                                  "none": 0, "rates": []})

    samples = []

    for conv in data:
        sid = conv.get("sample_id")
        c = conv.get("conversation") or {}

        # 建 mark -> text 映射
        text_of = {}
        j = 1
        while f"session_{j}" in c:
            for ti, turn in enumerate(c[f"session_{j}"] or [], start=1):
                txt = turn.get("text") or turn.get("content") or ""
                txt = re.sub(r"\s+", " ", str(txt)).strip()
                text_of[f"D{j}:{ti}"] = txt
            j += 1

        for q in conv.get("qa") or []:
            cat = str(q.get("category"))
            ans = q.get("answer") or ""
            ev = q.get("evidence") or []
            if not isinstance(ev, list):
                ev = [ev]
            ev = [str(e).strip() for e in ev if e]
            if not ans or not ev:
                continue

            ans_words = content_words(ans)
            if not ans_words:
                continue

            # evidence 原文
            ev_text = " ".join(text_of.get(e, "") for e in ev)
            ev_words = content_words(ev_text)

            covered = ans_words & ev_words
            rate = len(covered) / len(ans_words)

            d = by_cat[cat]
            d["n"] += 1
            d["rates"].append(rate)
            if rate >= 0.8:
                d["full"] += 1
            elif rate > 0:
                d["part"] += 1
            else:
                d["none"] += 1

            if cat == "3" and rate == 0 and len(samples) < 5:
                samples.append({
                    "q": q.get("question"),
                    "ans": ans,
                    "ans_words": sorted(ans_words)[:6],
                    "ev_text": ev_text[:200],
                })

    # ---------- 汇总 ----------
    print(f"\n{'类别':<10}{'题数':<8}{'答案词全在证据':<16}"
          f"{'部分在':<10}{'完全不在':<10}{'平均覆盖'}")
    print("-" * 94)

    for c in sorted(by_cat):
        d = by_cat[c]
        n = d["n"]
        avg = sum(d["rates"]) / n if n else 0
        f_s = "%d (%d%%)" % (d["full"], round(100 * d["full"] / n))
        p_s = "%d (%d%%)" % (d["part"], round(100 * d["part"] / n))
        n_s = "%d (%d%%)" % (d["none"], round(100 * d["none"] / n))
        print("%-10s%-8d%-16s%-10s%-10s%.3f" % (
            CAT.get(c, c), n, f_s, p_s, n_s, avg))

    # ---------- 多跳失败样本 ----------
    print("\n" + "=" * 94)
    print("多跳题里「答案词完全不在证据中」的样本")
    print("=" * 94)
    for s in samples:
        print(f"\n  Q: {s['q'][:72]}")
        print(f"  A: {s['ans'][:60]}")
        print(f"  答案实词: {s['ans_words']}")
        print(f"  证据原文: {s['ev_text'][:150]}")

    # ---------- 结论 ----------
    print("\n" + "=" * 94)
    print("结论")
    print("=" * 94)

    d1 = by_cat.get("1", {"n": 0, "full": 0, "rates": []})
    d3 = by_cat.get("3", {"n": 0, "full": 0, "rates": []})

    r1 = sum(d1["rates"]) / d1["n"] if d1["n"] else 0
    r3 = sum(d3["rates"]) / d3["n"] if d3["n"] else 0

    print(f"\n  单跳题（cat 1）：答案词在证据里的平均覆盖率 {r1:.3f}")
    print(f"  多跳题（cat 3）：答案词在证据里的平均覆盖率 {r3:.3f}")
    print(f"  差距: {r1 - r3:+.3f}")

    if r3 < r1 - 0.15:
        print(f"\n  → ✅ 多跳题的答案确实更多是「推理得出」而非「原文可读」")
        print(f"  → 这类题对检索系统的上限天生偏低")
        print(f"  → **不应把多跳的失败全算作检索缺陷**")
        print(f"\n  正确做法：")
        print(f"    · 检索负责「找到素材」（对应召回指标）")
        print(f"    · 推理负责「从素材得出结论」（需要 LLM 作答环节）")
    else:
        print(f"\n  → 多跳与单跳的答案可读性接近")
        print(f"  → 多跳失败确实是检索问题")

    out = {"experiment": "E-179",
           "by_cat": {c: {"n": d["n"], "full": d["full"],
                          "part": d["part"], "none": d["none"],
                          "avg_coverage": round(
                              sum(d["rates"]) / d["n"], 4) if d["n"] else 0}
                      for c, d in by_cat.items()},
           "single_avg": round(r1, 4),
           "multi_avg": round(r3, 4),
           "samples": samples}
    with open(os.path.join(RESULTS, "e179_reasoning.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e179_reasoning.json')}")


if __name__ == "__main__":
    main()
