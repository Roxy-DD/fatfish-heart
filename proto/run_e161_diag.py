"""
E-161 诊断：LoCoMo 单跳类别为什么只有 0.2812

E-160 结果：
  时间 0.7297 / 对抗 0.5106 / 常识 0.4571 / 多跳 0.4545 / 单跳 **0.2812**

单跳最低，反直觉（单跳应该最简单）。

假设：
  H1  evidence 指向的轮次内容与问题字面差异大（如问"研究什么"，答"收养机构"）
  H2  单跳问题的答案分散在多轮，单条 evidence 不够
  H3  记忆切分粒度问题（一轮太长，关键词被稀释）
  H4  检索排序问题（相关轮次被更长的、含关键词多的轮次挤掉）

诊断方法：
  对失败的单跳题，打印 evidence 轮次的原文 + 实际召回的 top-3
  看差距在哪
"""
from __future__ import annotations

import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e161")
os.makedirs(TASK, exist_ok=True)
RESULTS = os.path.join(_HERE, "results")
DATA = os.path.join(_HERE, "work", "e159", "locomo_sample.json")


def main():
    from store_v2 import MemoryStore

    d = json.load(open(DATA, encoding="utf-8"))
    qa = d.get("qa") or []
    conv = d.get("conversation") or {}

    sessions = []
    i = 1
    while f"session_{i}" in conv:
        turns = conv[f"session_{i}"]
        if turns:
            sessions.append((i, conv.get(f"session_{i}_date_time", ""), turns))
        i += 1

    db = os.path.join(TASK, "locomo.db")
    for suf in ("", "-wal", "-shm"):
        p = db + suf
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass
    st = MemoryStore(db)

    mark_of = {}
    text_of = {}
    for snum, dt, turns in sessions:
        for ti, turn in enumerate(turns, start=1):
            spk = turn.get("speaker") or ""
            txt = turn.get("text") or turn.get("content") or ""
            txt = re.sub(r"\s+", " ", str(txt)).strip()
            if len(txt) < 5:
                continue
            full = f"[{dt}] {spk}: {txt}" if dt else f"{spk}: {txt}"
            try:
                ep = st.add(full[:800], resolve_conflict=False)
                mark = f"D{snum}:{ti}"
                mark_of[ep.ep_id] = mark
                text_of[mark] = full
            except Exception:
                pass

    # 只看单跳（category=1）
    single = [q for q in qa if str(q.get("category")) == "1"]
    print("=" * 94)
    print(f"E-161 单跳失败诊断（共 {len(single)} 条）")
    print("=" * 94)

    fails = 0
    for q in single:
        question = q.get("question") or ""
        answer = q.get("answer") or ""
        ev = q.get("evidence") or []
        if not isinstance(ev, list):
            ev = [ev]
        ev = [str(e).strip() for e in ev if e]
        if not ev:
            continue

        res = st.search(question, top=10)
        got = [(mark_of.get(t[0].ep_id), t[0].raw_text) for t in res]
        hit = any(e in [g[0] for g in got] for e in ev)

        if hit:
            continue
        fails += 1
        if fails > 6:
            continue

        print(f"\n{'=' * 94}")
        print(f"问题: {question}")
        print(f"答案: {answer}")
        print(f"证据轮次: {ev}")

        for e in ev:
            txt = text_of.get(e, "(未找到)")
            print(f"\n  ★ 证据 {e} 原文:")
            print(f"    {txt[:260]}")

        print(f"\n  实际召回 top-3:")
        for m, t in got[:3]:
            print(f"    [{m}] {t[:120]}")

    st.con.close()

    print("\n" + "=" * 94)
    print(f"单跳失败 {fails} 条（共 {len(single)}）")
    print("=" * 94)
    print("\n观察上面案例，判断属于哪种情况：")
    print("  H1 语义差异大（问答用词完全不同）")
    print("  H2 答案分散多轮")
    print("  H3 轮次太长关键词被稀释")
    print("  H4 被含关键词多的无关轮次挤掉")


if __name__ == "__main__":
    main()
