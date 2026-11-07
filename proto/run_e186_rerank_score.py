"""
E-186 评分：LLM 精排 vs 向量基线

判定：
  基线：store.search 的 top-10 里有 evidence
  精排：LLM 打分后取前 10，里面有 evidence

关键：用 gold_evidence 与候选文本匹配，判断 evidence 是否在选中集合里。
      因为 evidence 是 "D1:3" 这种 mark 格式，候选是文本，
      需要用 E-186 prep 时建立的映射（这里重新建）。

对齐方式：
  重新建库，得到 ep_id -> mark 映射，然后：
    · 基线命中 = 原 top-10 的 ep_id 对应的 mark 里有 gold
    · 精排命中 = LLM 打分 top-10 的 ep_id 对应的 mark 里有 gold
"""
from __future__ import annotations

import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e186")
RESULTS = os.path.join(_HERE, "results")
DATA = os.path.join(_HERE, "work", "e165", "locomo10.json")

K = 10


def main():
    tp = os.path.join(TASK, "rerank_tasks.json")
    sp = os.path.join(TASK, "rerank_scores.json")

    if not os.path.exists(sp):
        print("缺少 rerank_scores.json")
        return

    tasks = json.load(open(tp, encoding="utf-8"))
    scores = {r["cid"]: r for r in json.load(open(sp, encoding="utf-8"))}

    print("=" * 94)
    print("E-186 LLM 精排 vs 向量基线")
    print("=" * 94)
    print(f"\n任务 {len(tasks)} 题，打分 {len(scores)} 题\n")

    # 重建 ep_id -> mark 映射
    from store_v2 import MemoryStore
    data = json.load(open(DATA, encoding="utf-8"))
    conv = data[0]
    c = conv.get("conversation") or {}
    db = os.path.join(TASK, "map.db")
    for suf in ("", "-wal", "-shm"):
        p = db + suf
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass
    st = MemoryStore(db)
    mark_of = {}
    j = 1
    while f"session_{j}" in c:
        for ti, turn in enumerate(c[f"session_{j}"] or [], start=1):
            spk = turn.get("speaker") or ""
            txt = turn.get("text") or turn.get("content") or ""
            txt = re.sub(r"\s+", " ", str(txt)).strip()
            if len(txt) < 5:
                continue
            full = f"[{c.get(f'session_{j}_date_time','')}] {spk}: {txt}"
            try:
                ep = st.add(full[:800], resolve_conflict=False)
                mark_of[ep.ep_id] = f"D{j}:{ti}"
            except Exception:
                pass
        j += 1
    st.con.close()

    base_hit = rerank_hit = 0
    n = 0
    improved = degraded = 0
    details = []

    for t in tasks:
        cid = t["cid"]
        ev = set(t["gold_evidence"])
        sc = scores.get(cid)
        if not sc:
            continue

        n += 1

        # 基线：原 top-10
        base_marks = {mark_of.get(c2["ep_id"])
                      for c2 in t["candidates"][:K]}
        base_marks.discard(None)
        bh = bool(ev & base_marks)
        base_hit += 1 if bh else 0

        # 精排：按 LLM 分数取前 10
        scored = sc.get("scores") or []
        # 按 score 降序，同分保持原序
        scored = sorted(scored, key=lambda x: -float(x.get("score") or 0))
        rerank_marks = {mark_of.get(x["ep_id"]) for x in scored[:K]}
        rerank_marks.discard(None)
        rh = bool(ev & rerank_marks)
        rerank_hit += 1 if rh else 0

        if rh and not bh:
            improved += 1
        elif bh and not rh:
            degraded += 1

        details.append({"cid": cid, "base": bh, "rerank": rh})

    # ---------- 汇总 ----------
    print("=" * 94)
    print("结果")
    print("=" * 94)

    rb = base_hit / max(1, n)
    rr = rerank_hit / max(1, n)

    print(f"\n  题数: {n}")
    print(f"  基线（向量 top-10）:   {base_hit}/{n} = {rb:.4f}")
    print(f"  LLM 精排（top-30 重排）: {rerank_hit}/{n} = {rr:.4f}")
    print(f"  差值: {rr - rb:+.4f}")
    print(f"\n  救回: {improved} 题")
    print(f"  变差: {degraded} 题")

    print("\n" + "=" * 94)
    print("结论")
    print("=" * 94)

    if rr > rb + 0.03:
        print(f"\n  → ✅ LLM 精排有效（+{rr-rb:.4f}）")
        print(f"  → 验证了 E-185 的判断：问题在排序精度，不在语义匹配")
        print(f"  → 下一步：优化精排成本（批量、小模型、或 cross-encoder）")
    elif abs(rr - rb) <= 0.03:
        print(f"\n  → 精排效果不显著（{rr-rb:+.4f}）")
        print(f"  → 可能原因：LLM 判断与向量排序高度一致（信息冗余）")
    else:
        print(f"\n  → ❌ 精排反而更差（{rr-rb:+.4f}）")
        print(f"  → LLM 判断不可靠，或打分标准有问题")

    out = {"experiment": "E-186", "n": n,
           "baseline": round(rb, 4),
           "rerank": round(rr, 4),
           "delta": round(rr - rb, 4),
           "improved": improved, "degraded": degraded,
           "details": details}
    with open(os.path.join(RESULTS, "e186_rerank.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e186_rerank.json')}")


if __name__ == "__main__":
    main()
