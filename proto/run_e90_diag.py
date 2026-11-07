"""
E-90 补最后 2 条漏报：J12「张大姐」、I22「房东」

E-89 结果：规则+jieba 召回 0.80→0.90，精确率仍 1.0
  已补：J00 陈默、J03 王雅琴
  仍漏：J12 张大姐（jieba 只标出"张"，"大姐"没标 nr）
        I22 房东（jieba 没标 nr，但规则里 STABLE_SLOT 有"房东"？）

先查清 I22 为什么漏——这是关键。规则里 STABLE_SLOT 明明有"房东"。
"""
from __future__ import annotations

import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
RESULTS = os.path.join(_HERE, "results")

from capability import STABLE_SLOT, identity_can  # noqa


def main():
    print("=" * 88)
    print("E-90 诊断最后 2 条漏报")
    print("=" * 88)

    # 先查 I22 房东为什么漏
    t22 = "房东说下个月涨房租"
    print("\n【诊断 I22「房东说下个月涨房租」】")
    print(f"  identity_can = {identity_can(t22)}")
    print(f"  STABLE_SLOT 匹配 = {bool(STABLE_SLOT.search(t22))}")
    print(f"  STABLE_SLOT 正则 = {STABLE_SLOT.pattern}")
    # 手测
    m = STABLE_SLOT.search(t22)
    print(f"  匹配结果 = {m.group(0) if m else None}")

    # 问题：STABLE_SLOT 是 "我(?:的|家的|自己家的)?"，要求前面有"我"
    # 但"房东说..."前面没有"我"字！
    # 这就是漏的原因：正则要求"我的房东"，但实际是"房东"单独出现

    # 查 J12
    t12 = "菜市场的张大姐给我多称了半斤"
    print(f"\n【诊断 J12「菜市场的张大姐」】")
    print(f"  identity_can = {identity_can(t12)}")
    import jieba.posseg as pseg
    jieba.setLogLevel(60)
    words = list(pseg.cut(t12))
    print(f"  分词 = {[(w.word, w.flag) for w in words]}")

    print("\n" + "=" * 88)
    print("根因结论")
    print("=" * 88)
    print("""
  两个漏报的根因不同：

  I22「房东说...」
    → STABLE_SLOT 正则要求 "我(的/家的)房东"，但句子是"房东"单独开头
    → 根因：规则把"稳定关系槽"绑定在"我的X"上，没考虑"X"单独出现

  J12「张大姐」
    → jieba 把"张大姐"切成 "张"(nr) + "大姐"(n)
    → 根因：jieba 不认"姓+大姐"这种称呼组合

  修法：
    I22：STABLE_SLOT 增加"房东/老板/领导"等**可独立出现**的稳定角色
    J12：称呼表增加"大姐/大哥/阿姨/叔"等，且前面有姓氏
  """)


if __name__ == "__main__":
    main()
