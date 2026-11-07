"""
E-77 从"完整/不完整"改成"能力范围"（capability scope）

用户的关键纠正（2026-10-26）：
  "爷爷去世了，记忆本身是有的，只是具体是哪一年不记得了而已。
   或者说，我只记得他是在某一个范围内，所以时间回溯是有一个范围的，可以被模糊掉。
   但是这种事件性的记忆，它没有具体的时间锚点，而是关于'有没有发生过'的事儿。"

→ 记忆的"完整性"不是记忆自身的属性，而是**相对于问题的属性**。
   所以我之前"给每条记忆打完整/不完整标签"的做法是错的。

E-77 新模型：
  每条记忆不标"完整度"，而是标**能力范围**：
    can_answer    : 它能可靠回答哪些类型的问题
    cannot_answer : 它明确回答不了哪些类型（这是边界，不是缺陷）
    fuzzy_zones   : 模糊区（有时间范围但不精确）

三类问题维度（从语料里归纳）：
  existence  有没有发生过 / 是什么          ← 事件性记忆该能答
  identity   是谁 / 哪个对象                ← 需要专名
  time_exact 具体哪一天/哪一年              ← 常答不出，属正常
  time_range 大概什么时候                   ← 范围可模糊
  value      具体数值
  location   具体地点

验证：
  让模型给每条记忆标注能力范围
  然后用"该记忆该答的问题"和"不该答的问题"分别测，看标注是否与实际一致
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(_HERE, "results")
TASK = os.path.join(_HERE, "work", "e77")
os.makedirs(TASK, exist_ok=True)

# 语料：刻意的"事件性记忆"，时间不精确但事件成立
CORPUS = [
    ("爷爷去世了，那阵子我一直在医院和家之间来回跑",
     ["existence", "time_range"], ["time_exact", "value"]),
    ("小时候被邻居家的狗追着咬过，小腿上留了疤",
     ["existence", "identity"], ["time_exact", "location"]),
    ("孩子第一次叫妈妈的时候，我眼泪一下就出来了",
     ["existence", "identity"], ["time_exact"]),
    ("钱包丢了，里面有身份证和所有的卡",
     ["existence"], ["time_exact", "value"]),
    ("那次项目上线前一晚，大家通宵改最后一批 bug",
     ["existence", "identity"], ["time_exact", "value"]),
    ("我电脑的登录密码是 8848",
     ["value", "identity"], ["time_exact", "location"]),
    ("12月3日下午两点，我和张工在7楼会议室对齐了接口协议",
     ["time_exact", "identity", "location", "value"], []),
    ("城南路18号是我女儿幼儿园的地址",
     ["location", "value"], ["time_exact"]),
    ("这个方案我否决了，因为成本太高",
     ["existence"], ["value", "identity", "time_exact"]),
    ("他同意了，但是有条件",
     [], ["existence", "identity", "time_exact"]),
]


def main():
    rows = [{"rid": f"S{i:02d}", "memory": t,
             "expect_can": can, "expect_cannot": cant}
            for i, (t, can, cant) in enumerate(CORPUS)]
    with open(os.path.join(TASK, "corpus.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)

    print("=" * 96)
    print("E-77 能力范围标注（替代完整度标签）")
    print("=" * 96)
    print(f"\n{len(CORPUS)} 条事件性记忆\n")
    print("-" * 96)
    print(f"{'rid':<7}{'能答':<40}{'不能答':<26}{'记忆'}")
    print("-" * 96)
    for r in rows:
        print(f"{r['rid']:<7}{'/'.join(r['expect_can'])[:38]:<40}"
              f"{'/'.join(r['expect_cannot'])[:24]:<26}{r['memory'][:30]}")
    print("-" * 96)

    print("\n维度说明：")
    for k, v in {
        "existence": "有没有发生过 / 是什么事",
        "identity": "是谁 / 哪个对象",
        "time_exact": "具体哪一天/哪一年",
        "time_range": "大概什么时候（可模糊）",
        "value": "具体数值",
        "location": "具体地点",
    }.items():
        print(f"  {k:<12}{v}")
    print(f"\n→ {TASK}")


if __name__ == "__main__":
    main()
