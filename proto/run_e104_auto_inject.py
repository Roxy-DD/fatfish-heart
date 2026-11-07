"""
E-104 插件自动注入的真实验证（跨轮次）

背景：
  dsh-plugin 已装进 DSH，1 层工具已验证（内核接口全通）。
  第 2 层（自动注入）需要跨对话轮次才能触发：
    system-prompt/assemble 在每轮模型请求前发生。

本脚本：
  检查 plugin-debug.jsonl 是否记录了 assemble 调用，
  并解析注入内容，确认记忆真的进了上下文。
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
RESULT = os.path.join(_HERE, "results")
LOG = os.path.join(_HERE, "cosmos_mem", "plugin-debug.jsonl")


def main():
    print("=" * 80)
    print("E-104 插件自动注入验证")
    print("=" * 80)
    print(f"\n日志文件: {LOG}")

    if not os.path.exists(LOG):
        print("\n❌ 日志不存在——自动注入还没有被触发过")
        print("   原因：system-prompt/assemble 只在新的对话轮次开始时触发")
        print("   结论：需要你在 GUI 里发一条新消息，然后重跑本脚本")
        return

    lines = [l for l in open(LOG, encoding="utf-8") if l.strip()]
    print(f"   共 {len(lines)} 条记录\n")

    print("-" * 80)
    print(f"{'时间':<22}{'类型':<16}{'查询':<24}{'命中'}")
    print("-" * 80)
    ok = 0
    for line in lines[-20:]:
        try:
            r = json.loads(line)
        except Exception:
            continue
        kind = r.get("kind", "?")
        q = (r.get("query") or "")[:22]
        hits = r.get("hits", "-")
        if kind == "assemble" and hits:
            ok += 1
        print(f"{r.get('t','')[:19]:<22}{kind:<16}{q:<24}{hits}")
    print("-" * 80)

    # 找最后一条成功的注入，看内容
    last = None
    for line in reversed(lines):
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("kind") == "assemble" and r.get("body"):
            last = r
            break

    print("\n" + "=" * 80)
    if last:
        print("✅ 自动注入已生效")
        print("=" * 80)
        print(f"\n触发查询: {last.get('query')}")
        print(f"命中条数: {last.get('hits')}")
        print(f"\n注入进上下文的内容:")
        for l in (last.get("body") or "").split("\n"):
            print(f"  {l}")
    else:
        print("⚠️ 没有找到带内容的注入记录")
        print("=" * 80)
        print("\n可能原因：")
        print("  1. 还没发新消息（assemble 未触发）")
        print("  2. 查询没有匹配到任何记忆")
        print("  3. 注入逻辑有 bug（查 assemble-error 记录）")

    # 错误记录
    errs = []
    for line in lines:
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("kind", "").endswith("error"):
            errs.append(r)
    if errs:
        print(f"\n⚠️ 有 {len(errs)} 条错误记录：")
        for e in errs[-3:]:
            print(f"   {e.get('t','')[:19]} {e.get('error','')[:100]}")

    out = {"experiment": "E-104", "log_exists": True,
           "records": len(lines), "successful_injects": ok,
           "last_inject": last}
    with open(os.path.join(RESULT, "e104_auto_inject.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULT, 'e104_auto_inject.json')}")


if __name__ == "__main__":
    main()
