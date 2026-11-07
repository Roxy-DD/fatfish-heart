"""
E-107 验证"模型回顾分析"自动记忆是否真的工作

区别：
  旧版（关键词）：日志里有 auto-remember，但那是字符串匹配
  新版（模型）：日志里应该出现 review-ok（模型返回了几条）+ auto-remember-model（写了哪几条）

本脚本检查日志，并从数据上判断：
  1. 模型回顾是否被调用（review-ok 出现）
  2. 模型返回了什么（raw 字段）
  3. 写入了哪些记忆
  4. 与关键词法对比（如果同一段话两种方法都跑了）
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(_HERE, "cosmos_mem", "plugin-debug.jsonl")
RESULTS = os.path.join(_HERE, "results")


def main():
    print("=" * 88)
    print("E-107 验证模型回顾分析自动记忆")
    print("=" * 88)

    if not os.path.exists(LOG):
        print("\n日志不存在——插件还没触发过")
        return

    lines = [l for l in open(LOG, encoding="utf-8") if l.strip()]
    recs = []
    for l in lines:
        try:
            recs.append(json.loads(l))
        except Exception:
            continue

    print(f"\n日志共 {len(recs)} 条\n")

    # 按类型统计
    from collections import Counter
    kinds = Counter(r.get("kind") for r in recs)
    print("事件类型：")
    for k, v in kinds.most_common():
        print(f"  {k:<28}{v}")

    # 关键：模型回顾
    print("\n" + "-" * 88)
    print("模型回顾分析（review-ok）")
    print("-" * 88)
    reviews = [r for r in recs if r.get("kind") == "review-ok"]
    if reviews:
        for r in reviews[-5:]:
            print(f"  [{r.get('t','')[:19]}] 模型分析出 {r.get('count')} 条记忆")
            print(f"    provider={r.get('provider')} model={r.get('model')}")
            print(f"    原始输出: {r.get('raw','')[:150]}")
    else:
        print("  ⚠️ 还没有 review-ok 记录")

    # 模型写入的记忆
    print("\n" + "-" * 88)
    print("模型决定记住的内容（auto-remember-model）")
    print("-" * 88)
    wrote = [r for r in recs if r.get("kind") == "auto-remember-model"]
    if wrote:
        for r in wrote[-10:]:
            print(f"  [{r.get('t','')[:19]}] {r.get('text','')[:60]}")
            print(f"    理由: {r.get('reason','')}  重要性: {r.get('importance')}")
    else:
        print("  ⚠️ 还没有模型写入记录")

    # 关键词法的记录（应该不再有）
    print("\n" + "-" * 88)
    print("关键词法残留（auto-remember 无 -model 后缀）")
    print("-" * 88)
    old = [r for r in recs if r.get("kind") == "auto-remember"]
    print(f"  {len(old)} 条（新版应该为 0）")

    # 错误
    errs = [r for r in recs if "error" in str(r.get("kind", "")) or r.get("kind") == "review-parse-fail"]
    if errs:
        print("\n" + "-" * 88)
        print("错误记录")
        print("-" * 88)
        for e in errs[-5:]:
            print(f"  [{e.get('t','')[:19]}] {e.get('kind')}: "
                  f"{str(e.get('error') or e.get('raw'))[:120]}")

    # 注入记录
    print("\n" + "-" * 88)
    print("自动注入（assemble）最近 3 条")
    print("-" * 88)
    asm = [r for r in recs if r.get("kind") == "assemble"]
    for r in asm[-3:]:
        print(f"  [{r.get('t','')[:19]}] mode={r.get('mode')} "
              f"query={str(r.get('query'))[:40]} hits={r.get('hits')}")

    out = {
        "experiment": "E-107",
        "total_records": len(recs),
        "kinds": dict(kinds),
        "review_count": len(reviews),
        "model_wrote": len(wrote),
        "keyword_residual": len(old),
        "errors": len(errs),
    }
    with open(os.path.join(RESULTS, "e107_model_review.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e107_model_review.json')}")


if __name__ == "__main__":
    main()
