"""
E-73 让模型自己找"我还没发现的缺陷类型"

目的：
  我目前只定义了 3 类完整性缺陷（裸代词 / 无锚时间 / 纯情绪）。
  这三类是我从模型的话里翻译出来的。
  模型还能发现第四、第五类吗？

做法：
  给模型一批**全新语料**（不是之前那 10 条），
  让它自由审查，找出"写得不完整"的记忆，并**自己归纳新的缺陷类型**。
  然后我把它的发现落成规则，测能不能覆盖它自己指出的问题。
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(_HERE, "results")
TASK = os.path.join(_HERE, "work", "e73")
os.makedirs(TASK, exist_ok=True)

# 全新语料：故意混入多种"可能有缺陷"的写法（我自己没预设分类）
CORPUS = [
    # 看似完整
    "我的车牌号是 沪A·5X29K",
    "12月3日下午两点，我和张工在 7 楼会议室对齐了接口协议",
    "医生说要连续吃两周的药，不能断",
    "我女儿的幼儿园在城南路 18 号",
    "这个方案我否决了，因为成本太高",
    # 可能有缺陷的（我没预设类型）
    "明天记得带那个东西",                          # 空指代 + 相对时间
    "他说不行",                                     # 无主语 + 无对象
    "这个方案后来改了",                             # 裸代词 + 无改后内容
    "上次那个价格谈下来了",                         # 相对时间 + 裸代词
    "我已经跟他对接过了",                           # 无对象
    "还是按老规矩办",                               # 无指代
    "那个文件在共享盘里",                           # 裸代词 + 无路径
    "记得提醒我",                                   # 无内容
    "大概下周三吧",                                 # 模糊时间
    "价格是市场价",                                 # 无可验证值
    "他同意了，但是有条件",                         # 无对象 + 无条��
    "这批货有点问题",                               # 无批次 + 无问题
    "我问过他了，他说等通知",                        # 无对象
    "那个东西我放起来了",                           # 裸代词
    "到那边之后再说",                               # 无地点
    # 长但信息模糊
    "今天开完会之后感觉整体方向还行，但是细节还需要再讨论一下，"
    "特别是关于接下来的排期和资源分配，可能需要再找几个人对一下",
]


def main():
    rows = [{"rid": f"R{i:02d}", "memory": t} for i, t in enumerate(CORPUS)]
    with open(os.path.join(TASK, "corpus.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)

    print("=" * 90)
    print("E-73 让模型自己找缺陷类型")
    print("=" * 90)
    print(f"\n全新语料 {len(CORPUS)} 条（我未预设分类）\n")
    print("-" * 90)
    for r in rows:
        print(f"  [{r['rid']}] {r['memory']}")
    print("-" * 90)
    print(f"\n→ {TASK}")


if __name__ == "__main__":
    main()
