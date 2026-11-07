"""
E-70 自举记忆：模型自己定义目标、自己验证、自己迭代

用户的判断（2026-10-26）：
  "模型可以自己在记忆的时候实验一遍，然后去修改，直到他觉得这是真的能记住了。
   也就是他直接实验出来是真的能记住的，和他的预期是一样的。
   所以模型的记忆系统应该是让模型自己能够操纵和迭代，
   而不是由人给它规定好应该如何迭代、如何找。"

E-69 已证：模型自我判断 F1=0.974，零误判，比我的规则强得多。

E-70 做的是把判断权交给模型：
  1. 给定一批记忆，模型**自己**为每条写"我想以后能回答什么问题"
     （这是它自己定义"记住"的含义）
  2. 系统用当前检索去回答这些问题
  3. 模型**自己**判断：检索给回来的东西，能不能回答我的问题
  4. 不能 → 模型**自己**提出改法（关键词？场景？同义？）
  5. 改完再验，直到它自己说"这确实能记住了"

关键：全程不由我规定阈值、不由我规定权重。
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
RESULTS = os.path.join(_HERE, "results")
TASK = os.path.join(_HERE, "work", "e70_task")
os.makedirs(TASK, exist_ok=True)

# 语料：故意包含"字面查不到但语义相关"的难例
CORPUS = [
    "我电脑的登录密码是 8848",
    "这个千万别搞错，搞错了要出大事",
    "小时候被邻居家的狗追着咬过，小腿上留了疤",
    "小时候在老家院子里看夕阳，天是橘红色的，特别轻松",
    "我被裁了，那天晚上一个人在车里坐了很久",
    "孩子第一次叫妈妈的时候，我眼泪一下就出来了",
    "客户明确说这条需求不能改，否则要重做",
    "下个月 15 号是项目的截止日期，逾期不候",
    "钱包丢了，里面有身份证和所有的卡",
    "和喜欢的人第一次约会，走过那条街",
]


def main():
    rows = [{"mid": f"M{i:02d}", "memory": t} for i, t in enumerate(CORPUS)]
    with open(os.path.join(TASK, "corpus.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)

    print("=" * 92)
    print("E-70 自举记忆：让模型自己定义「记住」并自己验证")
    print("=" * 92)
    print(f"\n{len(CORPUS)} 条记忆\n")
    print("-" * 92)
    for r in rows:
        print(f"  [{r['mid']}] {r['memory']}")
    print("-" * 92)
    print(f"\n→ {TASK}")
    print("\n下一步：让模型自己为每条记忆写'我想以后能回答什么'。")


if __name__ == "__main__":
    main()
