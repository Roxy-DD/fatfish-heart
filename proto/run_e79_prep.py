"""
E-79 交叉验证：规则推断 vs 模型标注（全新语料）

E-78 的 13/13 是在**我自己设计的语料**上测的，有循环论证嫌疑。
E-77 里模型和我的标注只一致 83.3%（我错 3 条）。

E-79 用全新语料（刻意不同于前几批的句式），
让模型独立标注能力范围，再和规则推断对比。

若一致率高 → 规则可用
若不高 → 该让模型标注替代规则
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
RESULTS = os.path.join(_HERE, "results")
TASK = os.path.join(_HERE, "work", "e79")
os.makedirs(TASK, exist_ok=True)

# 全新语料：刻意换句式、换领域，避免我"照着规则写句子"
CORPUS = [
    # 工作类
    "上周四的评审会上，产品经理把登录页的改版方案否了",
    "我答应这周五之前把接口文档发给他",
    # 生活类
    "小区门口的修车师傅姓王，收费便宜",
    "我每天要吃一片降压药，早上饭后",
    # 事件类
    "上个月搬家的那天，把一箱旧书落在旧房子里了",
    "去年冬天我妈摔了一跤，现在走路还有点不利索",
    # 数字/编号类
    "我的工牌编号是 A-2261",
    "快递柜的取件码是 8437",
    # 关系类
    "小李是我的大学室友，现在在深圳做后端",
    "我表姐家的孩子今年上三年级",
    # 模糊类
    "前几天在电梯里碰到个老同事，聊了几句就散了",
    "那次出差回来之后就总觉得累",
    # 精确类
    "2025年3月8日下午3点，在B座12层签的合同",
    # 承诺类
    "他说月底之前一定把尾款结清",
]


def main():
    from store_v1 import MemoryStore
    from run_e78_scope_route import infer_scope, DIMS

    rows = [{"rid": f"T{i:02d}", "memory": t} for i, t in enumerate(CORPUS)]
    # 规则推断
    for r in rows:
        r["rule_scope"] = infer_scope(r["memory"])

    with open(os.path.join(TASK, "corpus.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)

    print("=" * 96)
    print("E-79 全新语料：规则推断的能力范围")
    print("=" * 96)
    print(f"\n{len(CORPUS)} 条\n")
    print("-" * 96)
    print(f"{'rid':<6}{'exist':<7}{'ident':<7}{'t_exact':<9}{'t_range':<9}"
          f"{'value':<7}{'loc':<6}{'记忆'}")
    print("-" * 96)
    for r in rows:
        s = r["rule_scope"]
        print(f"{r['rid']:<6}"
              f"{('✓' if s['existence'] else '·'):<7}"
              f"{('✓' if s['identity'] else '·'):<7}"
              f"{('✓' if s['time_exact'] else '·'):<9}"
              f"{('✓' if s['time_range'] else '·'):<9}"
              f"{('✓' if s['value'] else '·'):<7}"
              f"{('✓' if s['location'] else '·'):<6}{r['memory'][:34]}")
    print("-" * 96)
    print(f"\n→ {TASK}/corpus.json（含 rule_scope，供模型独立复核）")


if __name__ == "__main__":
    main()
