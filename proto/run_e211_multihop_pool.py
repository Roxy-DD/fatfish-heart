"""
E-211 量化：92 个多跳题的证据在 top-50 候选池里的分布

目的：区分两种情况
  A) 证据根本检索不到（top-50 都没有）→ 检索层问题，需换 embedding/改写
  B) 证据在池里但排不进前10 → 排序/推理问题，需"取素材 + LLM 推理"

方法：全量 10 对话，只测多跳题（category=3），
      对每题算"最好证据在 top-50 候选池的排名"。
"""
from __future__ import annotations
import json
import os
import re
import sys
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e211")
os.makedirs(TASK, exist_ok=True)
RESULTS = os.path.join(_HERE, "results")
DATA = os.path.join(_HERE, "work", "e165", "locomo10.json")

POOL = 50
CAT = "3"  # 多跳


def main():
    from store_v2 import MemoryStore

    data = json.load(open(DATA, encoding="utf-8"))

    print("=" * 94)
    print("E-211 多跳题证据在 top-50 池的分布")
    print("=" * 94)

    n_total = 0
    n_base_hit = 0        # 证据在 top-10（基线命中）
    n_pool_hit = 0        # 证据在 top-50 但不在 top-10（可救回）
    n_miss = 0            # top-50 都没有
    ev_ranks = []         # 每题最好证据的排名

    for conv in data:
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

        for q in qa:
            if str(q.get("category")) != CAT:
                continue
            question = q.get("question") or ""
            ev = q.get("evidence") or []
            if not isinstance(ev, list):
                ev = [ev]
            ev = [str(e).strip() for e in ev if e]
            if not question or not ev:
                continue

            n_total += 1
            res = st.search(question, top=POOL)
            got = []
            for tup in res:
                m = mark_of.get(tup[0].ep_id)
                if m:
                    got.append(m)

            # 最好证据的排名
            best_rank = None
            for e in ev:
                if e in got:
                    r = got.index(e) + 1
                    if best_rank is None or r < best_rank:
                        best_rank = r

            if best_rank is None:
                n_miss += 1
            elif best_rank <= 10:
                n_base_hit += 1
            else:
                n_pool_hit += 1
                ev_ranks.append(best_rank)

        st.con.close()

    # ---------- 汇总 ----------
    print(f"\n  多跳题总数: {n_total}")
    print(f"\n  {'状态':<30}{'数量':<10}{'占比'}")
    print("-" * 60)
    print(f"{'基线命中（top-10）':<30}{n_base_hit:<10}{n_base_hit/n_total:.1%}")
    print(f"{'可救回（11-50名）':<30}{n_pool_hit:<10}{n_pool_hit/n_total:.1%}")
    print(f"{'完全检索不到（>50名）':<30}{n_miss:<10}{n_miss/n_total:.1%}")

    if ev_ranks:
        import statistics
        print(f"\n  可救回题的证据排名分布:")
        buckets = Counter()
        for r in ev_ranks:
            if r <= 15:
                buckets["11-15"] += 1
            elif r <= 20:
                buckets["16-20"] += 1
            elif r <= 30:
                buckets["21-30"] += 1
            else:
                buckets["31-50"] += 1
        for b in ["11-15", "16-20", "21-30", "31-50"]:
            c = buckets.get(b, 0)
            print(f"    {b}: {c}")
        print(f"  中位排名: {statistics.median(ev_ranks)}")

    # ---------- 结论 ----------
    print("\n" + "=" * 94)
    print("结论")
    print("=" * 94)
    recoverable_ratio = (n_pool_hit + n_base_hit) / n_total
    print(f"\n  证据在 top-50 池内的题: {n_base_hit + n_pool_hit}（{recoverable_ratio:.1%}）")
    print(f"  完全检索不到: {n_miss}（{n_miss/n_total:.1%}）")

    if n_pool_hit > n_miss:
        print(f"\n  → 多跳题的主要问题是「证据在池里但排不进前10」+「需推理」")
        print(f"  → 攻法：取 top-50 素材 + LLM 推理作答")
    elif n_miss > n_pool_hit + n_base_hit:
        print(f"\n  → 多跳题主要问题是「检索不到」（{n_miss}题）")
        print(f"  → 攻法：换 embedding 或查询改写")
    else:
        print(f"\n  → 检索与推理问题各半")

    out = {"experiment": "E-211", "n_total": n_total,
           "base_hit": n_base_hit, "pool_hit": n_pool_hit, "miss": n_miss,
           "recoverable_ratio": round(recoverable_ratio, 4),
           "ev_ranks": ev_ranks}
    with open(os.path.join(RESULTS, "e211_multihop_pool.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e211_multihop_pool.json')}")


if __name__ == "__main__":
    main()
