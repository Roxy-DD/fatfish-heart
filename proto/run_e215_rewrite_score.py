"""
E-215 评分：LLM 改写后检索 vs 原问题检索（多跳题）

对每个多跳题：
  ① 原问题检索 → top-10 有没有证据（基线）
  ② LLM 改写后的多个 query 检索 → 任一改写命中的证据算命中（改写召回）
  ③ 所有改写结果并集 → 证据命中率

对比：改写能否把"检索不到"的多跳题救回。
"""
from __future__ import annotations
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e214")
RESULTS = os.path.join(_HERE, "results")
DATA = os.path.join(_HERE, "work", "e165", "locomo10.json")

K = 10


def main():
    from store_v2 import MemoryStore

    gold = json.load(open(os.path.join(TASK, "rewrite_gold.json"), encoding="utf-8"))
    rp = os.path.join(TASK, "rewrites.json")
    if not os.path.exists(rp):
        print("缺少 rewrites.json")
        return
    rewrites = {r["cid"]: r.get("rewrites", []) for r in json.load(open(rp, encoding="utf-8"))}

    data = json.load(open(DATA, encoding="utf-8"))

    print("=" * 94)
    print("E-215 LLM 改写检索 vs 原问题检索")
    print("=" * 94)

    # 对每个对话建库，用 sid 映射
    conv_by_sid = {conv.get("sample_id"): conv for conv in data}

    base_hit = 0
    rewrite_hit = 0
    total = 0
    rescued = 0  # 改写救回的题

    for g in gold:
        cid = g["cid"]
        sid = g["sid"]
        question = g["question"]
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

        total += 1

        # 基线：原问题
        res0 = st.search(question, top=K)
        got0 = {mark_of.get(t[0].ep_id) for t in res0}
        got0.discard(None)
        b_hit = any(e in got0 for e in ev)
        base_hit += 1 if b_hit else 0

        # 改写检索：所有改写的并集
        all_got = set()
        for rq in rewrites.get(cid, []):
            res = st.search(rq, top=K)
            got = {mark_of.get(t[0].ep_id) for t in res}
            got.discard(None)
            all_got |= got

        r_hit = any(e in all_got for e in ev)
        rewrite_hit += 1 if r_hit else 0
        if r_hit and not b_hit:
            rescued += 1

        st.con.close()

    # ---------- 汇总 ----------
    print(f"\n  多跳题 {total} 题")
    print(f"\n  基线（原问题检索）:  {base_hit}/{total} = {base_hit/total:.4f}")
    print(f"  改写检索:            {rewrite_hit}/{total} = {rewrite_hit/total:.4f}")
    print(f"  改写救回（基线漏但改写命中）: {rescued} 题")

    print(f"\n  提升: {rewrite_hit/total - base_hit/total:+.4f}")

    out = {"experiment": "E-215", "n": total,
           "base_hit": base_hit, "rewrite_hit": rewrite_hit,
           "base_rate": round(base_hit/total, 4),
           "rewrite_rate": round(rewrite_hit/total, 4),
           "rescued": rescued}
    with open(os.path.join(RESULTS, "e215_rewrite.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e215_rewrite.json')}")


if __name__ == "__main__":
    main()
