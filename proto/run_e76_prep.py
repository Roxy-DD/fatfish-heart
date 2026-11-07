"""
E-76 闭环：规则闸 vs 模型自判的"分歧"能否驱动规则演进

设计：
  规则闸（7 类，F1 0.94）和模型自判（F1 0.974）各自给同一批记忆打标。
  两者的**分歧**是最有价值的信息：
    A类分歧：规则说"有问题"，模型说"能答"   → 规则误报（该放松）
    B类分歧：规则说"没问题"，模型说"答不出" → 规则漏报（该加强）

本实验：
  1. 用 21 条 + 之前 10 条，共 31 条
  2. 让模型独立判断"这条以后能答吗"
  3. 与规则闸对比，统计分歧
  4. 分析 B 类分歧（漏报）的共同特征 → 归纳成第 8 类规则
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e76")
os.makedirs(TASK, exist_ok=True)
RESULTS = os.path.join(_HERE, "results")
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")


def main():
    # 合并两批语料
    c1 = json.load(open(os.path.join(_HERE, "work", "e73", "corpus.json"),
                        encoding="utf-8"))
    c2 = json.load(open(os.path.join(_HERE, "work", "e70_task", "corpus.json"),
                        encoding="utf-8"))
    rows = []
    for r in c1:
        rows.append({"rid": r["rid"], "memory": r["memory"]})
    for r in c2:
        rows.append({"rid": f"P{r['mid']}", "memory": r["memory"]})

    # 规则闸判定
    sys.path.insert(0, _HERE)
    from run_e75_rules_v2 import full_check
    for r in rows:
        r["rule_issues"] = full_check(r["memory"])
        r["rule_says_bad"] = len(r["rule_issues"]) > 0

    with open(os.path.join(TASK, "corpus.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)

    print("=" * 92)
    print("E-76 规则闸 vs 模型自判：分歧分析")
    print("=" * 92)
    print(f"\n共 {len(rows)} 条（21 + 10）\n")
    print("-" * 92)
    print(f"{'rid':<7}{'规则判定':<28}{'内容'}")
    print("-" * 92)
    n_bad = 0
    for r in rows:
        if r["rule_says_bad"]:
            n_bad += 1
        print(f"{r['rid']:<7}{('/'.join(r['rule_issues']) or 'clean')[:26]:<28}"
              f"{r['memory'][:40]}")
    print("-" * 92)
    print(f"规则判有问题的: {n_bad}/{len(rows)}")
    print(f"\n→ 已生成 {TASK}/corpus.json（含规则判定，供模型独立复核）")


if __name__ == "__main__":
    main()
