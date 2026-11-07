"""
E-81 identity 专项：先问模型判据，再落规则

E-80 结论：identity 准确率只 0.7083，是最后短板。
E-79 里模型和我的分歧例子：
  「我的工牌编号 A-2261」  → 模型判 identity=false（"我的工牌"是泛称）
  「我表姐家的孩子」        → 模型判 identity=true

我的规则抓"有专名/编号"，模型抓的似乎是"能否唯一指认"。
但这只是我的推测 —— E-80 的教训是别猜，要问。

E-81：
  第 1 步：构造一组专测 identity 的句子（设计成落在边界上）
  第 2 步：让模型独立判断，并**要求它说出判据**
  第 3 步：从它的判据里提炼规则，测准确率
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(_HERE, "results")
TASK = os.path.join(_HERE, "work", "e81")
os.makedirs(TASK, exist_ok=True)

# 刻意设计成 identity 的边界案例
CASES = [
    # 明确唯一（预期 true）
    ("张工说这周五交", "具名人物"),
    ("我的身份证号是 310101199001011234", "唯一编号"),
    ("小李是我的大学室友", "具名人物"),
    ("我老婆叫陈静", "具名亲属"),
    ("工牌编号 A-2261", "唯一编号"),
    ("李建国经理负责这个项目", "具名+职务"),
    # 明确泛称（预期 false）
    ("他说不行", "纯代词"),
    ("那次出差回来之后总觉得累", "指代+无对象"),
    ("那个方案后来改了", "指代+类名词"),
    ("有人跟我说过这事", "不定指"),
    ("大家都觉得这个方案不错", "集合指代"),
    # 边界案例（最考验判据）
    ("我的工牌编号是 A-2261", "有编号，但主体是'我的工牌'"),
    ("我表姐家的孩子今年上三年级", "亲属关系链，无姓名"),
    ("我女儿今年六岁", "亲属，无姓名"),
    ("我们组的后端负责人不同意", "职务指认，无人名"),
    ("前台的小姑娘让我登记一下", "描述性指认，无人名"),
    ("上周那个客户又打电话来了", "描述性+时间，无人名"),
    ("我妈说家里一切都好", "亲属"),
    ("我电脑的登录密码是 8848", "物品归属，非人"),
    ("城南路18号是我女儿幼儿园", "地点指认"),
    ("小区门口的修车师傅姓王", "姓氏+位置描述"),
    ("我老板姓张", "姓氏+关系"),
    ("房东说下个月涨房租", "关系指认，无姓名"),
    ("快递员把包裹放门口了", "职业指认"),
]


def main():
    rows = [{"rid": f"I{i:02d}", "memory": t, "tag": tag}
            for i, (t, tag) in enumerate(CASES)]
    with open(os.path.join(TASK, "corpus.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)

    print("=" * 92)
    print("E-81 identity 专项：边界案例")
    print("=" * 92)
    print(f"\n{len(CASES)} 条（刻意设计在 identity 边界上）\n")
    print("-" * 92)
    print(f"{'rid':<7}{'类型':<36}{'记忆'}")
    print("-" * 92)
    for r in rows:
        print(f"{r['rid']:<7}{r['tag']:<36}{r['memory']}")
    print("-" * 92)
    print(f"\n→ {TASK}")


if __name__ == "__main__":
    main()
