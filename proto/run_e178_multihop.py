"""
E-178 多跳题为什么会失败（0.3913 最低）

E-166 全量结果：
  多跳 36/92 = 0.3913（最低）
  全证据召回 0.4859（一半的题拿不全证据）

本实验对多跳题做逐条诊断：
  ① evidence 有几条？分布在几个 session？
  ② 是否"一条 evidence 都找不到" vs "找到一部分"
  ③ 失败案例的 evidence 内容与问题有什么语义距离

判定目标：
  · 如果多数是"一条都找不到" → 语义匹配问题
  · 如果多数是"找到 1 条但缺其他" → 需要多路召回（不是排序问题）
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e178")
os.makedirs(TASK, exist_ok=True)
RESULTS = os.path.join(_HERE, "results")
DATA = os.path.join(_HERE, "work", "e165", "locomo10.json")

K = 10


def main():
    from store_v2 import MemoryStore

    data = json.load(open(DATA, encoding="utf-8"))

    print("=" * 94)
    print("E-178 多跳题失败诊断")
    print("=" * 94)

    # 只看多跳（category=3），跑全部对话
    total = 0
    found_all = found_part = found_none = 0
    ev_counts = Counter()
    session_spread = Counter()
    fails = []

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

        mark_of, text_of = {}, {}
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

        for q in qa:
            if str(q.get("category")) != "3":
                continue
            question = q.get("question") or ""
            ev = q.get("evidence") or []
            if not isinstance(ev, list):
                ev = [ev]
            ev = [str(e).strip() for e in ev if e]
            if not question or not ev:
                continue

            total += 1
            ev_counts[len(ev)] += 1

            # session 分布
            sess_set = {e.split(":")[0] for e in ev}
            session_spread[len(sess_set)] += 1

            res = st.search(question, top=K)
            got = {mark_of.get(t[0].ep_id) for t in res}
            got.discard(None)

            found = [e for e in ev if e in got]
            if len(found) == len(ev):
                found_all += 1
            elif found:
                found_part += 1
                if len(fails) < 6:
                    fails.append({
                        "q": question, "ev": ev, "found": found,
                        "sid": sid,
                        "ev_texts": [text_of.get(e, "")[:120] for e in ev],
                    })
            else:
                found_none += 1
                if len(fails) < 6:
                    fails.append({
                        "q": question, "ev": ev, "found": [],
                        "sid": sid,
                        "ev_texts": [text_of.get(e, "")[:120] for e in ev],
                    })

        st.con.close()

    # ---------- 汇总 ----------
    print(f"\n多跳题总数: {total}")
    print(f"\n{'结果':<20}{'条数':<10}{'占比'}")
    print("-" * 50)
    print(f"{'全部证据都找到':<20}{found_all:<10}{found_all/max(1,total):.1%}")
    print(f"{'找到部分证据':<20}{found_part:<10}{found_part/max(1,total):.1%}")
    print(f"{'一条都没找到':<20}{found_none:<10}{found_none/max(1,total):.1%}")

    print(f"\n  evidence 条数分布:")
    for n in sorted(ev_counts):
        print(f"    {n} 条: {ev_counts[n]} 题")

    print(f"\n  evidence 跨 session 数分布:")
    for n in sorted(session_spread):
        print(f"    跨 {n} 个 session: {session_spread[n]} 题")

    # ---------- 失败案例 ----------
    print(f"\n" + "=" * 94)
    print("失败案例样本")
    print("=" * 94)
    for f in fails[:4]:
        print(f"\n  [{f['sid']}] {f['q'][:66]}")
        print(f"    evidence: {f['ev']}  → 找到 {f['found']}")
        for i, t in enumerate(f["ev_texts"]):
            print(f"    【{f['ev'][i]}】{t[:100]}")

    # ---------- 结论 ----------
    print("\n" + "=" * 94)
    print("结论")
    print("=" * 94)

    print(f"\n  全找到: {found_all}  部分: {found_part}  全失败: {found_none}")

    if found_part > found_all and found_part > found_none:
        print(f"\n  → 多数是「找到部分证据」（{found_part}/{total}）")
        print(f"  → 问题在**多路召回不足**：单次检索只能命中一条相关记忆")
        print(f"  → 解法：迭代扩散（已有 chain.py）或扩大候选池")
    elif found_none > found_all:
        print(f"\n  → 多数是「一条都没找到」（{found_none}/{total}）")
        print(f"  → 问题在**语义匹配**：问题与证据的语义距离太远")
        print(f"  → 解法：需要改写查询或换更强的 embedding")
    else:
        print(f"\n  → 主要是全找到，失败较少")

    out = {"experiment": "E-178", "n_multi": total,
           "found_all": found_all, "found_part": found_part,
           "found_none": found_none,
           "ev_counts": dict(ev_counts),
           "session_spread": dict(session_spread),
           "samples": fails[:6]}
    with open(os.path.join(RESULTS, "e178_multihop.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e178_multihop.json')}")


if __name__ == "__main__":
    main()
