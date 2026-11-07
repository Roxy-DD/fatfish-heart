"""
E-218 构造"改写检索素材 + LLM 推理作答"任务包

对 92 个多跳题：
  ① 用 LLM 改写检索，取 top-10 素材
  ② 把素材 + 问题打包，让 LLM 作答
  ③ 与标准答案比对

本脚本只做 ①（检索素材），生成任务包供子代理作答。
"""
from __future__ import annotations
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e218")
os.makedirs(TASK, exist_ok=True)
DATA = os.path.join(_HERE, "work", "e165", "locomo10.json")
REWRITE = os.path.join(_HERE, "work", "e214")

K = 10


def main():
    from store_v2 import MemoryStore

    gold = json.load(open(os.path.join(REWRITE, "rewrite_gold.json"), encoding="utf-8"))
    rewrites = {r["cid"]: r.get("rewrites", []) for r in
                json.load(open(os.path.join(REWRITE, "rewrites.json"), encoding="utf-8"))}
    data = json.load(open(DATA, encoding="utf-8"))
    conv_by_sid = {conv.get("sample_id"): conv for conv in data}

    tasks = []
    for g in gold:
        cid = g["cid"]
        sid = g["sid"]
        question = g["question"]
        answer = g["answer"]
        ev = g["evidence"]
        conv = conv_by_sid.get(sid)
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

        mark_of = {}
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
                    mark_of[ep.ep_id] = f"D{snum}:{ti}"
                except Exception:
                    pass

        # 改写检索取素材（并集 top-10）
        context = []
        seen = set()
        for rq in rewrites.get(cid, []):
            res = st.search(rq, top=K)
            for tup in res:
                m = mark_of.get(tup[0].ep_id)
                if m and m not in seen:
                    seen.add(m)
                    context.append(tup[0].raw_text[:300])

        tasks.append({
            "cid": cid, "sid": sid, "question": question,
            "answer": answer,  # 供评分，子代理看不到
            "context": context[:15],
        })
        st.con.close()

    # 拆分：给子代理的不含 answer
    for_agent = [{"cid": t["cid"], "question": t["question"],
                  "context": t["context"]} for t in tasks]
    with open(os.path.join(TASK, "qa_tasks.json"), "w", encoding="utf-8") as f:
        json.dump(for_agent, f, ensure_ascii=False, indent=1)
    with open(os.path.join(TASK, "qa_gold.json"), "w", encoding="utf-8") as f:
        json.dump(tasks, f, ensure_ascii=False, indent=1)

    print(f"任务包 {len(tasks)} 题")
    print(f"→ {TASK}/qa_tasks.json")


if __name__ == "__main__":
    main()
