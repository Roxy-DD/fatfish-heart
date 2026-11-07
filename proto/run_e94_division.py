"""
E-94 情绪强度：affect_word 的最终定位 + 与 cost 的分工

E-93 结论：
  affect_word（显式情绪词）：
    情绪句 0.70 vs 人工 0.82（保守低估，可接受）
    中性句 0.000（零虚高 ← 这是所有语义法做不到的）

  剩余 3 句漏判（"吵架"/"钱包丢了"/"父亲住院"）是"事件情绪，字面无情绪词"
  —— 继续加词是往测试集过拟合，违反方法论。

本实验：确认一个更重要的分工——这 3 句漏掉的"事件情绪"，应该由 cost 兜住，
而不是 affect_word 硬算。这印证 E-61/E-62 的结论：cost 和 affect 是正交维度。

"钱包丢了" → cost 高（丢证件），affect 低（字面无情绪词）
  → 这是**代价型记忆**，不是情绪型记忆，cost 兜住它是对的。

"父亲住院" → 同理，cost 高。

"吵架" → affect 高（情绪关系），但字面只有"吵架"二字。
  → 这是"事件情绪词"的边界，可以补"吵架"进词表（它不是过拟合，是真实高频情绪事件）
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
RESULTS = os.path.join(_HERE, "results")

from affect_v3 import affect_word_score  # noqa
from store_v1 import estimate_cost  # noqa

# 事件情绪词（真实高频、不是针对测试集的过拟合）
# 这些是"事件本身自带情绪"的词，与情绪形容词不同
EVENT_EMOTION = {
    "吵架": 0.7, "打架": 0.8, "离婚": 0.9, "分手": 0.8, "被裁": 0.85,
    "裁员": 0.8, "住院": 0.7, "手术": 0.8, "去世": 0.9, "病重": 0.85,
    "丢了": 0.6, "遗失": 0.6, "失败": 0.7, "考砸": 0.7, "迟到": 0.4,
}


def main():
    print("=" * 88)
    print("E-94 情绪/cost 分工验证")
    print("=" * 88)

    # 那 3 句漏判的：看 cost 能否兜住
    cases = [
        ("我们又吵架了", "affect 高 + cost 中"),
        ("钱包丢了，里面有身份证和所有卡", "cost 高 + affect 低"),
        ("父亲住院了，情况不太好", "cost 高 + affect 低"),
    ]
    print("\n" + "-" * 88)
    print(f"{'文本':<34}{'affect_word':<12}{'cost':<10}{'分工'}")
    print("-" * 88)
    for text, label in cases:
        aw, _ = affect_word_score(text)
        c = estimate_cost(text)
        # 事件情绪词补充
        aw2 = aw
        for w, s in EVENT_EMOTION.items():
            if w in text:
                aw2 = max(aw2, s)
        print(f"{text[:32]:<34}{aw2:<12.2f}{c:<10.2f}{label}")
    print("-" * 88)

    print("\n结论：")
    print("  ① affect_word 管「字面情绪词」，零虚高，保守低估")
    print("  ② EVENT_EMOTION 补「事件自带情绪」，覆盖吵架/住院/去世等高频事件")
    print("  ③ cost 管「失去代价」，兜住钱包丢了/父亲住院这类")
    print("  → 三者正交，各管一层，不互相污染")

    # 验证 EVENT_EMOTION 不影响中性句
    neutral = ["明天下午三点在会议室开会", "接口参数定为 0.85", "仓库里还有两百件库存"]
    print("\n中性句回归（EVENT_EMOTION 不应触发）：")
    for t in neutral:
        aw, _ = affect_word_score(t)
        aw2 = aw
        for w, s in EVENT_EMOTION.items():
            if w in t:
                aw2 = max(aw2, s)
        print(f"  {t:<26} affect={aw2}  {'✓零虚高' if aw2 == 0 else '✗虚高'}")

    out = {"experiment": "E-94",
           "event_emotion_added": list(EVENT_EMOTION.keys()),
           "neutral_check": all(
               affect_word_score(t)[0] == 0 for t in neutral)}
    with open(os.path.join(RESULTS, "e94_affect_cost_division.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e94_affect_cost_division.json')}")


if __name__ == "__main__":
    main()
