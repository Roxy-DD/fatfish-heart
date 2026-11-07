"""
E-221 完整链路评测：任务包生成（检索 + 作答 + 判等）

目标：得到与业界对齐的 LoCoMo 准确率/F1。

设计决策：
  1. 分层抽样：全量 1982 题太多，每类抽 N 题，总量控制在 200 题左右
     （保证分题型可比，子代理可跑完）
  2. 检索链路 = 最优配置：向量检索 + cross-encoder 精排（rerank=True）
     多跳题（cat=3）额外用 LLM 改写检索（E-215 已验证 +15.2pp）
  3. 作答 = LLM 根据检索到的素材回答
  4. 判等 = LLM 判 pred 与 gold 是否语义等价

本脚本只做 ①②：抽样 + 检索素材，生成作答任务包。
"""
from __future__ import annotations
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e221")
os.makedirs(TASK, exist_ok=True)
DATA = os.path.join(_HERE, "work", "e165", "locomo10.json")
REWRITE = os.path.join(_HERE, "work", "e214")  # 多跳改写结果

K = 10
# 每类抽样数（按全量占比分配，总量 ~200）
PER_CAT = {"1": 40, "2": 40, "3": 40, "4": 50, "5": 40}


def main():
    from store_v2 import MemoryStore

    data = json.load(open(DATA, encoding="utf-8"))
    # 多跳改写结果（按 question 文本匹配）
    rewrites = {}
    rw_path = os.path.join(REWRITE, "rewrites.json")
    rw_gold_path = os.path.join(REWRITE, "rewrite_gold.json")
    if os.path.exists(rw_path) and os.path.exists(rw_gold_path):
        rw_gold = json.load(open(rw_gold_path, encoding="utf-8"))
        rw_list = json.load(open(rw_path, encoding="utf-8"))
        # gold[i].question -> rewrites[i].rewrites（两者同序）
        for g, r in zip(rw_gold, rw_list):
            rewrites[g["question"]] = r.get("rewrites", [])

    # 收集全量题目按类型分组
    by_cat = {}
    for conv in data:
        sid = conv.get("sample_id")
        c = conv.get("conversation") or {}
        for q in conv.get("qa") or []:
            cat = str(q.get("category"))
            by_cat.setdefault(cat, []).append({
                "sid": sid, "question": q.get("question") or "",
                "answer": q.get("answer") or "",
                "evidence": q.get("evidence") or [],
            })

    print("=" * 94)
    print("E-221 完整链路评测任务包")
    print("=" * 94)

    # 抽样（每类取前 PER_CAT 个，保持顺序稳定）
    import random
    random.seed(42)
    samples = []
    for cat, n in PER_CAT.items():
        pool = by_cat.get(cat, [])
        picked = pool[:n] if len(pool) >= n else pool
        for p in picked:
            samples.append({**p, "cat": cat})

    print(f"\n抽样 {len(samples)} 题（分类型）:")
    from collections import Counter
    for c, cnt in sorted(Counter(s["cat"] for s in samples).items()):
        print(f"  类别 {c}: {cnt}")

    # 建库 + 检索（每个对话建一次库，缓存）
    conv_cache = {}
    tasks = []
    for s in samples:
        sid = s["sid"]
        cat = s["cat"]
        question = s["question"]
        ev = s["evidence"] if isinstance(s["evidence"], list) else [s["evidence"]]

        # 建库（缓存）
        if sid not in conv_cache:
            conv = next((x for x in data if x.get("sample_id") == sid), None)
            if not conv:
                continue
            c = conv.get("conversation") or {}
            sessions = []
            j = 1
            while f"session_{j}" in c:
                if c[f"session_{j}"]:
                    sessions.append((j, c.get(f"session_{j}_date_time", ""),
                                     c[f"session_{j}"]))
                j += 1
            db = os.path.join(TASK, f"{sid}.db")
            for suf in ("", "-wal", "-shm"):
                p = db + suf
                if os.path.exists(p):
                    try:
                        os.remove(p)
                    except Exception:
                        pass
            st = MemoryStore(db)
            for snum, dt, turns in sessions:
                for ti, turn in enumerate(turns, start=1):
                    spk = turn.get("speaker") or ""
                    txt = turn.get("text") or turn.get("content") or ""
                    txt = re.sub(r"\s+", " ", str(txt)).strip()
                    if len(txt) < 5:
                        continue
                    full = f"[{dt}] {spk}: {txt}" if dt else f"{spk}: {txt}"
                    try:
                        st.add(full[:800], resolve_conflict=False)
                    except Exception:
                        pass
            conv_cache[sid] = st
        st = conv_cache[sid]

        # 检索：cross-encoder 精排（rerank=True）
        context = []
        seen = set()
        try:
            res = st.search(question, top=K, rerank=True)
            for tup in res:
                if tup[0].ep_id not in seen:
                    seen.add(tup[0].ep_id)
                    context.append(tup[0].raw_text[:300])
        except Exception:
            pass

        # 多跳题额外用改写检索扩充素材
        if cat == "3":
            for rq in rewrites.get(question, []):
                try:
                    res = st.search(rq, top=K)
                    for tup in res:
                        if tup[0].ep_id not in seen:
                            seen.add(tup[0].ep_id)
                            context.append(tup[0].raw_text[:300])
                except Exception:
                    pass

        tasks.append({
            "cid": f"Q{len(tasks):03d}",
            "cat": cat,
            "question": question,
            "answer": str(s["answer"])[:100],  # 供判等，子代理看不到
            "context": context[:15],
        })

    # 拆分成作答任务包（不含 answer）
    for_agent = [{"cid": t["cid"], "cat": t["cat"],
                  "question": t["question"], "context": t["context"]}
                 for t in tasks]
    with open(os.path.join(TASK, "answer_tasks.json"), "w", encoding="utf-8") as f:
        json.dump(for_agent, f, ensure_ascii=False, indent=1)
    with open(os.path.join(TASK, "gold.json"), "w", encoding="utf-8") as f:
        json.dump(tasks, f, ensure_ascii=False, indent=1)

    print(f"\n任务包 {len(tasks)} 题 → {TASK}/answer_tasks.json")
    print(f"评分包 → {TASK}/gold.json")


if __name__ == "__main__":
    main()
