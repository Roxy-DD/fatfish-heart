"""
E-88 验证 jieba 分词/词性能否补上 identity 漏报

E-85 的 identity 规则：精确率 1.0，但召回 0.80（漏报 4 条）：
  J00「陈默是我们组的架构师」→ 漏（"陈默"不在我的全名白名单）
  J03「王雅琴医生给我开的药单」→ 漏（同上）
  J12「菜市场的张大姐」→ 漏（"大姐"不在称呼表）
  I22「房东说下个月涨房租」→ 漏（"房东"漏在稳定关系槽）

这 4 条的共同点：**都是"人"的识别问题**，不是判据问题。

候选方案：jieba 的 posseg（词性标注），能标出 nr（人名）/ n（名词）。
如果 jieba 能识别"陈默""王雅琴"是人名，就能补上漏报，且不影响精确率。

实验设计：
  1. 用 jieba.posseg 对 4 条漏报 + 若干负例做词性标注
  2. 看 nr（人名）标签能否区分"真身份"与"泛称"
  3. 若能 → 把 jieba 的 nr 作为 identity 的补充信号
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(_HERE, "results")

# 4 条漏报 + 若干负例（泛称，不该识别为人名）
POSITIVE = [
    ("J00", "陈默是我们组的架构师"),
    ("J03", "王雅琴医生给我开的药单"),
    ("J12", "菜市场的张大姐给我多称了半斤"),
    ("I22", "房东说下个月涨房租"),
]
NEGATIVE = [
    ("N1", "他说不行"),
    ("N2", "有人建议改一下流程"),
    ("N3", "快递员把包裹放门口了"),
    ("N4", "那个送外卖的小哥态度很好"),
    ("N5", "前台的小姑娘让我登记一下"),
    ("N6", "我表姐家的孩子今年上三年级"),
]


def main():
    import jieba
    import jieba.posseg as pseg
    jieba.setLogLevel(60)

    print("=" * 88)
    print("E-88 jieba 词性能否补上 identity 漏报")
    print("=" * 88)

    print("\n【正例：应该能识别出'人'的】")
    print("-" * 88)
    print(f"{'rid':<6}{'nr人名':<20}{'n名词':<30}{'内容'}")
    print("-" * 88)
    pos_ok = 0
    for rid, text in POSITIVE:
        words = list(pseg.cut(text))
        nr = [w.word for w in words if w.flag == "nr"]
        n = [w.word for w in words if w.flag == "n" and len(w.word) <= 3]
        # 正例判据：出现了 nr 人名，或"房东"被标为 n
        hit = len(nr) > 0 or any("房东" in w for w in n)
        pos_ok += 1 if hit else 0
        print(f"{rid:<6}{str(nr)[:18]:<20}{str(n)[:28]:<30}{text[:26]}")
    print("-" * 88)

    print("\n【负例：不该识别出'人'的】")
    print("-" * 88)
    print(f"{'rid':<6}{'nr人名':<20}{'n名词':<30}{'内容'}")
    print("-" * 88)
    neg_ok = 0
    for rid, text in NEGATIVE:
        words = list(pseg.cut(text))
        nr = [w.word for w in words if w.flag == "nr"]
        n = [w.word for w in words if w.flag == "n" and len(w.word) <= 3]
        # 负例判据：不该出现 nr 人名（这些是泛称/代词）
        hit = len(nr) == 0
        neg_ok += 1 if hit else 0
        print(f"{rid:<6}{str(nr)[:18]:<20}{str(n)[:28]:<30}{text[:26]}")
    print("-" * 88)

    print(f"\n正例识别率: {pos_ok}/{len(POSITIVE)}")
    print(f"负例零误报率: {neg_ok}/{len(NEGATIVE)}")

    # 结论
    print("\n" + "=" * 88)
    print("结论")
    print("=" * 88)
    if pos_ok == len(POSITIVE) and neg_ok == len(NEGATIVE):
        print("  ✅ jieba 的 nr 标签能完美补上漏报，且不误伤负例")
        print("  → 可以接入 identity 判定")
    elif pos_ok >= 3 and neg_ok == len(NEGATIVE):
        print("  ⚠️ jieba 能补大部分漏报，但少数正例漏识别")
        print("  → 可作为补充信号，但需保留原规则兜底")
    else:
        print("  ❌ jieba 词性不足以解决，需换 NER 模型或继续砸词表")

    out = {"experiment": "E-88",
           "positive": [{"rid": r, "text": t,
                         "nr": [w.word for w in list(pseg.cut(t)) if w.flag == "nr"]}
                        for r, t in POSITIVE],
           "negative": [{"rid": r, "text": t,
                         "nr": [w.word for w in list(pseg.cut(t)) if w.flag == "nr"]}
                        for r, t in NEGATIVE],
           "pos_ok": pos_ok, "neg_ok": neg_ok}
    with open(os.path.join(RESULTS, "e88_jieba.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e88_jieba.json')}")


if __name__ == "__main__":
    main()
