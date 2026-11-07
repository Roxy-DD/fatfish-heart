"""
E-164 定位 bug：余弦排名 #1 却不在 top-10

E-163 发现 6 条余弦排名前 3 却没被召回，
最极端一条余弦排名 #1（0.7102）仍不在 top-10。

原因假设：
  search() 是三路打分：
    语义  sim * 2.0
    关键词 命中加分
    结构化 person/time/location 加成 0.8
    情绪触发加成
  纯余弦排名第一 ≠ 融合后排名第一

  可能的情况：
    A) 某条记忆靠关键词/结构化拿到高分，把余弦高的挤下去
    B) 语义权重 2.0 不够高
    C) 目标记忆因为长度长，关键词命中少

本实验拆解单条案例的打分明细，看每一路的贡献。
"""
from __future__ import annotations

import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e164")
os.makedirs(TASK, exist_ok=True)
RESULTS = os.path.join(_HERE, "results")
DATA = os.path.join(_HERE, "work", "e159", "locomo_sample.json")

K = 10


def main():
    from store_v2 import MemoryStore
    from store_v1 import Embedder, split_query_terms, extract_elements
    import numpy as np

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

    mark_of, id_of_mark, text_of = {}, {}, {}
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
                id_of_mark[mark] = ep.ep_id
                text_of[mark] = full
            except Exception:
                pass

    emb = Embedder.get()
    st._load_vectors(include_invalid=False)
    norms = np.linalg.norm(st._vec, axis=1)
    norms[norms == 0] = 1.0

    # 找余弦排名前 3 却失败的题
    targets = []
    for q in qa:
        question = q.get("question") or ""
        ev = q.get("evidence") or []
        if not isinstance(ev, list) or len(ev) != 1:
            continue
        mark = str(ev[0]).strip()
        if mark not in id_of_mark:
            continue

        nv = emb.encode(question)[0]
        sims = (st._vec @ nv) / norms
        eidx = st._vec_ids.index(id_of_mark[mark])
        rank = int((sims > sims[eidx]).sum()) + 1
        if rank > 3:
            continue

        res = st.search(question, top=K)
        got = {mark_of.get(t[0].ep_id) for t in res}
        if mark in got:
            continue

        targets.append({
            "q": question, "mark": mark, "rank": rank,
            "sim": float(sims[eidx]),
            "results": [(mark_of.get(t[0].ep_id), round(t[1], 3) if len(t) > 1 else 0)
                        for t in res],
        })

    print("=" * 94)
    print(f"E-164 拆解 {len(targets)} 条'余弦高却未召回'的案例")
    print("=" * 94)

    for t in targets[:4]:
        q = t["q"]
        mark = t["mark"]
        print(f"\n{'=' * 94}")
        print(f"问题: {q}")
        print(f"证据: {mark}  余弦排名 #{t['rank']}  余弦 {t['sim']:.4f}")
        print(f"\n  证据原文:")
        print(f"    {text_of.get(mark, '')[:180]}")

        print(f"\n  实际返回 top-{K}:")
        for m, sc in t["results"]:
            flag = ""
            print(f"    [{m}] score={sc:.3f}  {text_of.get(m, '')[:70]}{flag}")

        # 拆解查询词与结构化
        terms = split_query_terms(q)
        el = extract_elements(q)
        print(f"\n  查询拆解:")
        print(f"    关键词: {terms[:8]}")
        print(f"    person={el['person']} time={el['time']} location={el['location']}")

        # 证据条目的三路得分
        ev_text = text_of.get(mark, "")
        ev_low = ev_text.lower()
        kw_hit = sum(1 for w in terms if w.lower() in ev_low)
        print(f"\n  证据条目的三路得分:")
        print(f"    语义: {t['sim']:.4f} × 2.0 = {t['sim'] * 2.0:.3f}")
        print(f"    关键词命中: {kw_hit}/{len(terms)}")

        # 看返回的第一名为什么高
        if t["results"]:
            top_mark = t["results"][0][0]
            top_text = text_of.get(top_mark, "")
            top_low = top_text.lower()
            top_kw = sum(1 for w in terms if w.lower() in top_low)
            print(f"\n  返回第 1 名 [{top_mark}] 得分来源:")
            print(f"    关键词命中: {top_kw}/{len(terms)}  "
                  f"（证据条目只有 {kw_hit}）")
            print(f"    长度: {len(top_text)} vs 证据 {len(ev_text)}")

    st.con.close()

    print("\n" + "=" * 94)
    print("结论")
    print("=" * 94)
    print("\n如果证据条目的关键词命中数明显少于返回的第一名，")
    print("说明问题在【关键词通道权重过高】或【语义权重不足】。")
    print("如果两者相近但语义差很多，说明【语义权重 2.0 太低】。")

    out = {"experiment": "E-164", "n": len(targets),
           "cases": [{k: v for k, v in t.items() if k != "results"}
                     for t in targets]}
    with open(os.path.join(RESULTS, "e164_bug.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e164_bug.json')}")


if __name__ == "__main__":
    main()
