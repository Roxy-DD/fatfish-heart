"""
E-109 真实链路验证：两阶段循环在 DSH 里是否真的跑起来

这个脚本只做一件事：读插件日志，如实报告两阶段是否触发、模型说了什么。
不做任何美化——没触发就说没触发。

检查项：
  阶段1（会话开始预判）：prewarm-ok
  阶段2（对话复盘记忆）：review-ok + auto-remember-model
  执行层（检索注入）：assemble
  错误层：任何 *error* / *fail*
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
    print("E-109 真实链路验证")
    print("=" * 88)

    if not os.path.exists(LOG):
        print("\n❌ 日志文件不存在")
        print(f"   路径: {LOG}")
        print("\n   可能原因：")
        print("   1. 插件未加载（检查 plugin_manager list_plugins）")
        print("   2. debugLog 关闭（config.debugLog=false）")
        print("   3. 还没有任何一轮对话触发 assemble")
        return

    recs = []
    for line in open(LOG, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            recs.append(json.loads(line))
        except Exception:
            pass

    print(f"\n日志共 {len(recs)} 条\n")

    if not recs:
        print("❌ 日志为空——插件从未触发")
        return

    from collections import Counter
    kinds = Counter(r.get("kind") for r in recs)

    print("-" * 88)
    print("事件统计")
    print("-" * 88)
    for k, v in kinds.most_common():
        print(f"  {k:<30}{v}")
    print("-" * 88)

    # ---- 阶段 1 ----
    print("\n" + "=" * 88)
    print("阶段 1：会话开始预判")
    print("=" * 88)
    pre = [r for r in recs if r.get("kind") == "prewarm-ok"]
    preSkip = [r for r in recs if r.get("kind") == "prewarm-skip"]
    preErr = [r for r in recs if r.get("kind") in
              ("prewarm-error", "prewarm-parse-fail", "prewarm-exception", "prewarm-catch")]

    if pre:
        print(f"\n✅ 触发 {len(pre)} 次")
        for r in pre[-3:]:
            print(f"\n  [{r.get('t','')[:19]}]")
            print(f"    推测: {r.get('prediction','')[:150]}")
            print(f"    可能话题: {r.get('topics')}")
            print(f"    参考记忆数: {r.get('memoryCount')}")
    else:
        print("\n⚠️ 未触发")
        if preSkip:
            print(f"   跳过 {len(preSkip)} 次，原因: {preSkip[-1].get('why')}")
        if preErr:
            print(f"   错误 {len(preErr)} 次: {preErr[-1].get('error') or preErr[-1].get('raw')}")

    # ---- 阶段 2 ----
    print("\n" + "=" * 88)
    print("阶段 2：对话复盘 + 模型记忆")
    print("=" * 88)
    rev = [r for r in recs if r.get("kind") == "review-ok"]
    wrote = [r for r in recs if r.get("kind") == "auto-remember-model"]
    revErr = [r for r in recs if r.get("kind") in
              ("review-error", "review-parse-fail", "auto-remember-error",
               "review-pipeline-error")]

    if rev:
        print(f"\n✅ 模型复盘 {len(rev)} 次")
        for r in rev[-3:]:
            print(f"\n  [{r.get('t','')[:19]}] 分析出 {r.get('count')} 条 "
                  f"(模型原始给出 {r.get('total')} 条)")
            print(f"    模型: {r.get('provider')}/{r.get('model')}")
            print(f"    原始输出: {str(r.get('raw',''))[:180]}")
    else:
        print("\n⚠️ 模型复盘未触发")

    if wrote:
        print(f"\n✅ 模型写入记忆 {len(wrote)} 条")
        for r in wrote[-8:]:
            print(f"  [{r.get('t','')[:19]}] {r.get('text','')}")
            print(f"     理由: {r.get('reason','')}  重要性: {r.get('importance')}")
    else:
        print("\n⚠️ 没有模型写入的记忆")

    if revErr:
        print(f"\n⚠️ 错误 {len(revErr)} 条:")
        for e in revErr[-5:]:
            print(f"  [{e.get('t','')[:19]}] {e.get('kind')}: "
                  f"{str(e.get('error') or e.get('raw'))[:150]}")

    # ---- 注入层 ----
    print("\n" + "=" * 88)
    print("注入层：assemble")
    print("=" * 88)
    asm = [r for r in recs if r.get("kind") == "assemble"]
    if asm:
        print(f"\n✅ 触发 {len(asm)} 次")
        for r in asm[-5:]:
            print(f"  [{r.get('t','')[:19]}] mode={r.get('mode')} "
                  f"hits={r.get('hits')} prewarm={r.get('hasPrewarm')}")
            print(f"      query={str(r.get('query',''))[:60]}")
    else:
        print("\n⚠️ 未触发")

    # ---- 总结 ----
    print("\n" + "=" * 88)
    print("总结")
    print("=" * 88)
    checks = [
        ("插件有活动", len(recs) > 0),
        ("阶段1 预判生效", len(pre) > 0),
        ("阶段2 模型复盘生效", len(rev) > 0),
        ("阶段2 实际写入记忆", len(wrote) > 0),
        ("注入层生效", len(asm) > 0),
    ]
    for name, ok in checks:
        print(f"  {name:<24}{'✅' if ok else '❌'}")

    out = {"experiment": "E-109", "total": len(recs),
           "kinds": dict(kinds),
           "prewarm_ok": len(pre), "prewarm_skip": len(preSkip),
           "review_ok": len(rev), "model_wrote": len(wrote),
           "assemble": len(asm), "errors": len(revErr) + len(preErr),
           "checks": {n: bool(v) for n, v in checks}}
    with open(os.path.join(RESULTS, "e109_real_chain.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e109_real_chain.json')}")


if __name__ == "__main__":
    main()
