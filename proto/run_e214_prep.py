"""
E-214 生成 92 个多跳题的 LLM 改写任务包（供子代理改写）

改写目标：把抽象/概括的问题改写成"证据原文里可能出现"的具体表述。
例：
  原："Would Melanie be more interested in national park or theme park?"
  改写："Melanie family camping trip outdoors nature hiking"

保留原始问题和 evidence（供后续评分），只让子代理输出改写。
"""
from __future__ import annotations
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e214")
os.makedirs(TASK, exist_ok=True)
DATA = os.path.join(_HERE, "work", "e165", "locomo10.json")


def main():
    data = json.load(open(DATA, encoding="utf-8"))

    # 收集多跳题
    tasks = []
    for conv in data:
        sid = conv.get("sample_id")
        c = conv.get("conversation") or {}
        spk_a = c.get("speaker_a", "")
        spk_b = c.get("speaker_b", "")

        for q in conv.get("qa") or []:
            if str(q.get("category")) != "3":
                continue
            question = q.get("question") or ""
            ans = q.get("answer") or ""
            ev = q.get("evidence") or []
            if not isinstance(ev, list):
                ev = [ev]
            ev = [str(e).strip() for e in ev if e]
            if not question or not ev:
                continue
            tasks.append({
                "cid": f"M{len(tasks):03d}",
                "sid": sid,
                "speakers": f"{spk_a}/{spk_b}",
                "question": question,
                "answer": str(ans)[:80],
                "evidence": ev,  # 供评分，子代理看不到
            })

    # 拆分给子代理的任务（不含 evidence）
    for_agent = [{"cid": t["cid"], "sid": t["sid"],
                  "speakers": t["speakers"], "question": t["question"]}
                 for t in tasks]

    with open(os.path.join(TASK, "rewrite_tasks.json"), "w", encoding="utf-8") as f:
        json.dump(for_agent, f, ensure_ascii=False, indent=1)
    with open(os.path.join(TASK, "rewrite_gold.json"), "w", encoding="utf-8") as f:
        json.dump(tasks, f, ensure_ascii=False, indent=1)

    print(f"多跳题 {len(tasks)} 题")
    print(f"改写任务包 → {TASK}/rewrite_tasks.json")
    print(f"评分包 → {TASK}/rewrite_gold.json")


if __name__ == "__main__":
    main()
