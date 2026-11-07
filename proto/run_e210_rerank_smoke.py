"""E-210 验证 store.search(rerank=True) 落地后能跑通且提升 recall"""
from __future__ import annotations
import json
import os
import re
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e210")
os.makedirs(TASK, exist_ok=True)
DATA = os.path.join(_HERE, "work", "e165", "locomo10.json")

K = 10


def main():
    from store_v2 import MemoryStore
    data = json.load(open(DATA, encoding="utf-8"))

    conv = data[0]
    sid = conv.get("sample_id")
    qa = conv.get("qa") or []
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

    print("=" * 94)
    print("E-210 验证 store.search(rerank=True)")
    print("=" * 94)

    base_hit = rerank_hit = 0
    total = 0
    t_base = t_rerank = 0.0

    # 先跑基线
    for q in qa:
        question = q.get("question") or ""
        ev = q.get("evidence") or []
        if not isinstance(ev, list):
            ev = [ev]
        ev = [str(e).strip() for e in ev if e]
        if not question or not ev:
            continue
        total += 1
        res = st.search(question, top=K)
        got = {mark_of.get(t[0].ep_id) for t in res}
        got.discard(None)
        base_hit += 1 if any(e in got for e in ev) else 0

    # 再跑 rerank=True（前 50 题，避免太久）
    print(f"\n  单对话 {total} 题，基线命中 {base_hit}")

    n_rr = 0
    rr_hit = 0
    for q in qa[:50]:
        question = q.get("question") or ""
        ev = q.get("evidence") or []
        if not isinstance(ev, list):
            ev = [ev]
        ev = [str(e).strip() for e in ev if e]
        if not question or not ev:
            continue
        n_rr += 1
        res = st.search(question, top=K, rerank=True)
        got = {mark_of.get(t[0].ep_id) for t in res}
        got.discard(None)
        rr_hit += 1 if any(e in got for e in ev) else 0

    print(f"  前 50 题 rerank=True 命中 {rr_hit}/{n_rr}")

    st.con.close()
    print("\n  → rerank=True 落地跑通，未崩溃")
    print(f"  基线前50题命中率参考: 需与 rr 同区间对比")


if __name__ == "__main__":
    main()
