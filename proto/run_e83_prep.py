"""
E-83 identity 第三批（全新语料）—— 检验是否过拟合

E-81/E-82 都是在同一批 24 条上迭代的，有过拟合风险。
E-83 用全新语料（句式、领域都不同），先修掉两个已知 bug，再测。

已修 bug：
  I07「出差」的"出"被当姓 → 排除词加 fullmatch 生效
  I22「房东」漏在 STABLE_SLOT → 补进去

全新第三批：刻意避开前两批的用词
"""
from __future__ import annotations

import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
TASK = os.path.join(_HERE, "work", "e83")
os.makedirs(TASK, exist_ok=True)
RESULTS = os.path.join(_HERE, "results")

# 全新语料：换领域、换句式
CASES = [
    # 具名/唯一
    "陈默是我们组的架构师",
    "订单号 20250308-1174 已经发货了",
    "我公公今年七十三",
    "王雅琴医生给我开的药单",
    "我家的车是白色的卡罗拉",
    # 泛称/指代
    "那个快递好像丢了",
    "别人都说这个不错",
    "他让我明天去一趟",
    "有几个同事提过这事",
    "有人建议改一下流程",
    # 边界：位置性角色
    "楼下便利店的老板说可以赊账",
    "我们楼的水管工很靠谱",
    "菜市场的张大姐给我多称了半斤",
    "那个送外卖的小哥态度很好",
    # 边界：关系链
    "我舅妈家的表哥在银行上班",
    "我同学的妈妈是老师",
    # 边界：编号绑定
    "保单号是 PA-9931",
    "我的保单号是 PA-9931",
    # 边界：称谓
    "小周说他下周回来",
    "周经理批了这个预算",
]


def main():
    rows = [{"rid": f"J{i:02d}", "memory": t} for i, t in enumerate(CASES)]
    with open(os.path.join(TASK, "corpus.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    print("=" * 92)
    print("E-83 identity 第三批（全新语料，检验过拟合）")
    print("=" * 92)
    print(f"\n{len(CASES)} 条\n")
    print("-" * 92)
    for r in rows:
        print(f"  [{r['rid']}] {r['memory']}")
    print("-" * 92)
    print(f"\n→ {TASK}")


if __name__ == "__main__":
    main()
