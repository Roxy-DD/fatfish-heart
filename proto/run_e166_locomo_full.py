"""
E-166 LoCoMo 全量评测（10 个对话，1986 条 QA）

E-160/E-164 只跑了 1 个对话（conv-26，197 条 QA）。
本实验跑完全部 10 个对话，得到可信的检索召回数字。

评测协议（与 E-160 一致，便于对比）：
  ① 每个对话独立建库
  ② 每轮对话写一条记忆（带 [日期] 说话人: 内容 格式）
  ③ 用 question 检索 top-K
  ④ evidence 指定的轮次被召回 → 命中
  ⑤ 报 Recall@K，按类别分

这是**检索召回**，不是答题 F1。与 Mem0 的 91.6 不可直接比较。
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e166")
os.makedirs(TASK, exist_ok=True)
RESULTS = os.path.join(_HERE, "results")
DATA = os.path.join(_HERE, "work", "e165", "locomo10.json")

K = 10
CAT = {"1": "单跳", "2": "时间", "3": "多跳", "4": "常识", "5": "对抗"}


def run_conversation(conv, idx):
    """跑单个对话，返回 {hit, total, all_hit, by_cat}"""
    from store_v2 import MemoryStore

    sid = conv.get("sample_id") or f"conv-{idx}"
    qa = conv.get("qa") or []
    c = conv.get("conversation") or {}

    # 数 session
    sessions = []
    j = 1
    while f"session_{j}" in c:
        turns = c[f"session_{j}"]
        if turns:
            sessions.append((j, c.get(f"session_{j}_date_time", ""), turns))
        j += 1

    # 独立库
    db = os.path.join(TASK, f"{sid}.db")
    for suf in ("", "-wal", "-shm"):
        p = db + suf
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass
    st = MemoryStore(db)

    # 写记忆
    mark_of = {}
    n_mem = 0
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
                n_mem += 1
            except Exception:
                pass

    # 评测
    hit = all_hit = total = 0
    by_cat = defaultdict(lambda: [0, 0])

    for q in qa:
        question = q.get("question") or ""
        ev = q.get("evidence") or []
        cat = str(q.get("category"))
        if not isinstance(ev, list):
            ev = [ev]
        ev = [str(e).strip() for e in ev if e]
        if not question or not ev:
            continue

        res = st.search(question, top=K)
        got = {mark_of.get(t[0].ep_id) for t in res}
        got.discard(None)

        h = any(e in got for e in ev)
        ah = all(e in got for e in ev)
        hit += 1 if h else 0
        all_hit += 1 if ah else 0
        total += 1
        by_cat[cat][1] += 1
        by_cat[cat][0] += 1 if h else 0

    st.con.close()

    return {
        "sample_id": sid, "n_mem": n_mem,
        "total": total, "hit": hit, "all_hit": all_hit,
        "by_cat": {k: list(v) for k, v in by_cat.items()},
    }


def main():
    if not os.path.exists(DATA):
        print("缺少 LoCoMo 全量数据")
        return

    data = json.load(open(DATA, encoding="utf-8"))

    print("=" * 94)
    print(f"E-166 LoCoMo 全量评测（{len(data)} 个对话，K={K}）")
    print("=" * 94)

    t0 = time.perf_counter()
    rows = []
    print(f"\n{'#':<4}{'对话':<12}{'记忆':<8}{'QA':<8}{'命中':<8}{'Recall'}")
    print("-" * 94)

    for i, conv in enumerate(data):
        r = run_conversation(conv, i)
        rows.append(r)
        rate = r["hit"] / max(1, r["total"])
        print(f"{i:<4}{r['sample_id']:<12}{r['n_mem']:<8}"
              f"{r['total']:<8}{r['hit']:<8}{rate:.4f}")

    dt = time.perf_counter() - t0

    # ---------- 汇总 ----------
    print("\n" + "=" * 94)
    print("汇总")
    print("=" * 94)

    total_n = sum(r["total"] for r in rows)
    total_hit = sum(r["hit"] for r in rows)
    total_all = sum(r["all_hit"] for r in rows)
    total_mem = sum(r["n_mem"] for r in rows)

    print(f"\n  对话数: {len(rows)}")
    print(f"  记忆总数: {total_mem}")
    print(f"  QA 总数: {total_n}")
    print(f"  耗时: {dt:.1f}s")

    print(f"\n  ★ 总体 Recall@{K}: {total_hit}/{total_n} = "
          f"{total_hit/total_n:.4f}")
    print(f"    全证据召回: {total_all}/{total_n} = "
          f"{total_all/total_n:.4f}")

    # 分类型
    agg = defaultdict(lambda: [0, 0])
    for r in rows:
        for cat, (h, n) in r["by_cat"].items():
            agg[cat][0] += h
            agg[cat][1] += n

    print(f"\n  {'类别':<10}{'召回/总':<16}{'Recall'}")
    print("  " + "-" * 50)
    for cat in sorted(agg):
        h, n = agg[cat]
        print(f"  {CAT.get(cat, cat):<10}{f'{h}/{n}':<16}{h/max(1,n):.4f}")

    # 各对话的方差
    rates = [r["hit"] / max(1, r["total"]) for r in rows]
    import statistics
    print(f"\n  各对话 Recall：均值 {statistics.mean(rates):.4f}  "
          f"标准差 {statistics.pstdev(rates):.4f}")
    print(f"    最小 {min(rates):.4f}  最大 {max(rates):.4f}")

    # ---------- 与之前对比 ----------
    print("\n" + "=" * 94)
    print("与历史对比")
    print("=" * 94)
    print(f"\n  E-160（1 个对话，停用词修复前）: 0.4924")
    print(f"  E-160b（1 个对话，停用词修复后）: 0.5584")
    print(f"  E-166（{len(rows)} 个对话，全量）: {total_hit/total_n:.4f}  ← 本轮")

    print(f"\n  说明：这是**检索召回**，不是答题 F1。")
    print(f"  Mem0 在 LoCoMo 报的 91.6 是 F1（答题），两者不可直接比较。")

    out = {"experiment": "E-166",
           "n_conversations": len(rows),
           "n_memories": total_mem,
           "n_qa": total_n,
           "k": K,
           "recall": round(total_hit / total_n, 4),
           "all_evidence_recall": round(total_all / total_n, 4),
           "by_category": {c: {"hit": v[0], "total": v[1]}
                           for c, v in agg.items()},
           "per_conversation": rows,
           "elapsed_s": round(dt, 1)}
    with open(os.path.join(RESULTS, "e166_locomo_full.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e166_locomo_full.json')}")


if __name__ == "__main__":
    main()
