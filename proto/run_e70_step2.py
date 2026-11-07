"""
E-70 第 2 步：用当前检索系统去回答模型自己出的问题，然后让模型自己判分

流程：
  1. 读模型自己写的 goals.json
  2. 对每个问题，用当前检索取 top-3
  3. 把「问题 + 检索结果」交给模型，让它判断"检索给回来的东西里，
     有没有能回答这个问题的"——这是自检（E-69 已证 F1 0.974）
  4. 汇总：哪些类型的问题答不上（这是"记忆系统限制能力"的具体位置）
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
RESULTS = os.path.join(_HERE, "results")
TASK = os.path.join(_HERE, "work", "e70_task")
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")


def main():
    from store_v1 import MemoryStore

    corpus = json.load(open(os.path.join(TASK, "corpus.json"), encoding="utf-8"))
    goals_p = os.path.join(TASK, "goals.json")
    if not os.path.exists(goals_p):
        print("缺少 goals.json（模型还没写目标）")
        return
    goals = json.load(open(goals_p, encoding="utf-8"))

    db = os.path.join(TASK, "e70.db")
    for suf in ("", "-wal", "-shm"):
        p = db + suf
        if os.path.exists(p):
            os.remove(p)
    st = MemoryStore(db)
    mid2eid = {}
    for r in corpus:
        ep = st.add(r["memory"])
        mid2eid[r["mid"]] = ep.ep_id

    # 构造自检包：每个问题 + 检索结果
    pack = []
    meta = []
    n_q = 0
    for g in goals:
        for q in g["questions"]:
            res = st.search(q["q"], top=3)
            cands = [{"cid": f"J{n_q:03d}-{k}",
                      "memory": ep.raw_text,
                      "is_target": ep.ep_id == mid2eid[g["mid"]]}
                     for k, (ep, *_ ) in enumerate(res)]
            pack.append({"jid": f"J{n_q:03d}", "question": q["q"],
                         "candidates": cands})
            meta.append({"jid": f"J{n_q:03d}", "mid": g["mid"],
                         "type": q["type"], "q": q["q"]})
            n_q += 1

    with open(os.path.join(TASK, "selfcheck_pack.json"), "w", encoding="utf-8") as f:
        json.dump(pack, f, ensure_ascii=False, indent=1)
    with open(os.path.join(TASK, "selfcheck_meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)

    # 同时记录"客观可答性"：目标记忆是否进了 top-3
    obj = {}
    for i, p in enumerate(pack):
        jid = p["jid"]
        obj[jid] = any(c["is_target"] for c in p["candidates"])
    with open(os.path.join(TASK, "objective.json"), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)

    print("=" * 88)
    print("E-70 第 2 步：生成自检包")
    print("=" * 88)
    print(f"\n模型自己出了 {n_q} 个问题")
    by_type = {}
    for m in meta:
        by_type[m["type"]] = by_type.get(m["type"], 0) + 1
    print("  类型分布:", by_type)
    hit = sum(1 for v in obj.values() if v)
    print(f"  目标记忆进 top-3 的比例: {hit}/{n_q} = {hit/n_q:.3f}")

    print("\n未命中的问题（这些是当前系统的真实盲区）：")
    for m in meta:
        if not obj[m["jid"]]:
            print(f"  [{m['jid']}] ({m['type']}) {m['q']}")
    print(f"\n→ {TASK}")


if __name__ == "__main__":
    main()
