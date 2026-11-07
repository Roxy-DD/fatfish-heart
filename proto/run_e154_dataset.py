"""
E-154 公开数据集适配可行性探测

目标：确认能否拿到 LongMemEval 数据（不改数据集，只写适配层）

LongMemEval 结构（已确认）：
  question_id       题目 id
  question_type     single-session-user / single-session-assistant /
                    single-session-preference / temporal-reasoning /
                    knowledge-update / multi-session
                    （question_id 以 _abs 结尾 = abstention）
  question          问题
  answer            标准答案
  question_date     提问日期
  haystack_sessions 历史会话（list of sessions，每 session 是 turns）
  haystack_dates    各 session 时间戳
  haystack_session_ids
  answer_session_ids 证据 session（用于检索评测）

对我们的意义：
  · knowledge-update 类正好测我们的矛盾处理（replace 能力）
  · answer_session_ids 可以做"检索召回"评测（不依赖 LLM 作答）
  · 这是**唯一能对外报数字**的路径

本实验只做可行性探测，不下载全量。
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
TASK = os.path.join(_HERE, "work", "e154")
os.makedirs(TASK, exist_ok=True)
RESULTS = os.path.join(_HERE, "results")

# 候选数据源
SOURCES = [
    ("HF datasets-server API (longmemeval_s)",
     "https://datasets-server.huggingface.co/rows"
     "?dataset=xiaowu0162%2Flongmemeval&config=default&split=train&offset=0&length=3"),
    ("HF MemoryAsModality/LongMemEval",
     "https://datasets-server.huggingface.co/rows"
     "?dataset=MemoryAsModality%2FLongMemEval&config=default&split=train&offset=0&length=3"),
    ("GitHub raw longmemeval_oracle",
     "https://raw.githubusercontent.com/xiaowu0162/LongMemEval/main/"
     "data/longmemeval_oracle.json"),
]


def try_fetch(name, url, timeout=20):
    print(f"\n[{name}]")
    print(f"  URL: {url[:88]}")
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (research)"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            size = len(raw)
            print(f"  HTTP {r.status}  {size} bytes")
            if size > 500:
                # 尝试解析
                try:
                    d = json.loads(raw.decode("utf-8"))
                    if isinstance(d, dict) and "rows" in d:
                        print(f"  ✅ 可解析，rows={len(d['rows'])}")
                        # 看第一条的字段
                        if d["rows"]:
                            r0 = d["rows"][0].get("row", {})
                            print(f"  字段: {list(r0.keys())[:12]}")
                            return {"ok": True, "kind": "hf_rows",
                                    "sample": r0, "n": len(d["rows"])}
                    elif isinstance(d, list):
                        print(f"  ✅ 可解析，list len={len(d)}")
                        if d:
                            print(f"  字段: {list(d[0].keys())[:12]}")
                            return {"ok": True, "kind": "json_list",
                                    "sample": d[0], "n": len(d)}
                    else:
                        print(f"  类型: {type(d)}")
                        return {"ok": True, "kind": "other", "n": 0}
                except Exception as e:
                    print(f"  ⚠ 解析失败: {e}")
                    return {"ok": False, "error": f"parse: {e}"}
            else:
                print(f"  响应太小: {raw[:200]}")
                return {"ok": False, "error": "too small"}
    except Exception as e:
        print(f"  ❌ {type(e).__name__}: {str(e)[:100]}")
        return {"ok": False, "error": str(e)[:120]}


def main():
    print("=" * 94)
    print("E-154 LongMemEval 数据集获取可行性")
    print("=" * 94)

    results = {}
    for name, url in SOURCES:
        r = try_fetch(name, url)
        results[name] = {"ok": r.get("ok"), "error": r.get("error")}
        if r.get("ok") and r.get("sample"):
            # 保存样本供后续分析
            out = os.path.join(TASK, "sample.json")
            with open(out, "w", encoding="utf-8") as f:
                json.dump(r["sample"], f, ensure_ascii=False, indent=1)
            print(f"  样本已保存 → {out}")
            results[name]["sample_saved"] = True

            # 分析样本结构
            s = r["sample"]
            print(f"\n  样本字段分析:")
            for k in s:
                v = s[k]
                t = type(v).__name__
                if isinstance(v, str):
                    print(f"    {k:<24}{t:<10}{v[:50]}")
                elif isinstance(v, list):
                    print(f"    {k:<24}{t:<10}len={len(v)}")
                else:
                    print(f"    {k:<24}{t:<10}{v}")
            break   # 第一个成功的就够

    # ---------- 结论 ----------
    print("\n" + "=" * 94)
    print("结论")
    print("=" * 94)

    any_ok = any(v.get("ok") for v in results.values())
    if any_ok:
        print("  ✅ 至少一个数据源可达，可以做适配")
        print(f"  → 适配层需要做的事：")
        print(f"    1. 把 haystack_sessions 逐条喂给我们的复盘机制（抽事实写入）")
        print(f"    2. 用 question 检索，取 top-N")
        print(f"    3. 与 answer 比对（或与 answer_session_ids 比检索召回）")
        print(f"\n  关键优势：answer_session_ids 让我们能**不依赖 LLM 作答**")
        print(f"  就先做检索召回评测 —— 这是纯客观的。")
    else:
        print("  ❌ 所有数据源都不可达")
        print("  → 需要换方案：")
        print("    · 手动构造类似 LongMemEval 的本地测试集")
        print("    · 或用已有的 E-128/E-131 合成数据做规模压测")

    out = {"experiment": "E-154", "sources": results,
           "available": any_ok}
    with open(os.path.join(RESULTS, "e154_dataset_probe.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e154_dataset_probe.json')}")


if __name__ == "__main__":
    main()
