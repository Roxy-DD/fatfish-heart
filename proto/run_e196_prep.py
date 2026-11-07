"""
E-196 全量可救回题任务包生成（带 evidence 原文，评分不重建库）

吸取 E-186b 教训：
  ① ep_id 每次建库随机，跨文件对不上 → 任务包内自含 evidence 原文文本
  ② mark D{session}:{turn} 跨对话覆盖 → 按对话隔离读取原文
  ③ 评分用"包含"判断，不重建库

生成内容：
  全量 10 对话，找出所有"基线漏掉但证据在 top-50"的可救回题，
  每题的 candidates 存 ep_id + text，evidence 存原文文本（供评分）。
"""
from __future__ import annotations

import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e196")
os.makedirs(TASK, exist_ok=True)
RESULTS = os.path.join(_HERE, "results")
DATA = os.path.join(_HERE, "work", "e165", "locomo10.json")

POOL = 50
K = 10


def main():
    from store_v2 import MemoryStore

    data = json.load(open(DATA, encoding="utf-8"))

    print("=" * 94)
    print("E-196 全量可救回题任务包生成")
    print("=" * 94)

    base_hit = 0
    total = 0
    recoverable = []
    by_cat_base = {}
    by_cat_rec = {}

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

        # 先建 mark -> 原文（本对话内）
        mark_text = {}
        for snum, dt, turns in sessions:
            for ti, turn in enumerate(turns, start=1):
                spk = turn.get("speaker") or ""
                txt = turn.get("text") or turn.get("content") or ""
                txt = re.sub(r"\s+", " ", str(txt)).strip()
                if len(txt) < 5:
                    continue
                full = f"[{dt}] {spk}: {txt}" if dt else f"{spk}: {txt}"
                mark_text[f"D{snum}:{ti}"] = full

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
            question = q.get("question") or ""
            ev = q.get("evidence") or []
            cat = str(q.get("category"))
            if not isinstance(ev, list):
                ev = [ev]
            ev = [str(e).strip() for e in ev if e]
            if not question or not ev:
                continue

            total += 1

            # 基线 top=10
            res10 = st.search(question, top=K)
            got10 = {mark_of.get(t[0].ep_id) for t in res10}
            got10.discard(None)
            if any(e in got10 for e in ev):
                base_hit += 1
                by_cat_base[cat] = by_cat_base.get(cat, 0) + 1
                continue

            # 可救回：top-50 池
            res50 = st.search(question, top=POOL)
            cands = []
            pool_marks = set()
            for tup in res50:
                m = mark_of.get(tup[0].ep_id)
                if m:
                    cands.append({
                        "ep_id": tup[0].ep_id,
                        "text": tup[0].raw_text[:250],
                    })
                    pool_marks.add(m)

            if not any(e in pool_marks for e in ev):
                continue

            # evidence 原文（本对话内）
            ev_texts = [mark_text.get(e, "") for e in ev]
            ev_texts = [t for t in ev_texts if t]

            recoverable.append({
                "cid": f"R{len(recoverable):03d}",
                "sid": sid,
                "question": question,
                "category": cat,
                "evidence_marks": ev,
                "evidence_texts": ev_texts,
                "candidates": cands,
            })
            by_cat_rec[cat] = by_cat_rec.get(cat, 0) + 1

        st.con.close()
        print(f"  [{sid}] 完成，累计可救回 {len(recoverable)}")

    # 写任务包（不带 evidence_texts 给子代理，但评分脚本需要，所以分开存）
    for_rater = []
    for r in recoverable:
        for_rater.append({
            "cid": r["cid"], "question": r["question"],
            "candidates": r["candidates"],
        })
    with open(os.path.join(TASK, "rerank_tasks.json"), "w",
              encoding="utf-8") as f:
        json.dump(for_rater, f, ensure_ascii=False, indent=1)

    # 评分用（含 evidence 原文 + 分题型 + mark）
    with open(os.path.join(TASK, "rerank_gold.json"), "w",
              encoding="utf-8") as f:
        json.dump(recoverable, f, ensure_ascii=False, indent=1)

    # ---------- 汇总 ----------
    print("\n" + "=" * 94)
    print("汇总")
    print("=" * 94)
    print(f"\n  总题数: {total}")
    print(f"  基线命中: {base_hit}/{total} = {base_hit/total:.4f}")
    print(f"  可救回题（top-{POOL}）: {len(recoverable)}")

    print(f"\n  分题型基线命中:")
    for c in sorted(by_cat_base):
        print(f"    类别 {c}: {by_cat_base[c]}")

    print(f"\n  分题型可救回:")
    for c in sorted(by_cat_rec):
        print(f"    类别 {c}: {by_cat_rec[c]}")

    out = {"experiment": "E-196", "total": total,
           "base_hit": base_hit, "baseline": round(base_hit / total, 4),
           "n_recoverable": len(recoverable),
           "by_cat_base": by_cat_base, "by_cat_rec": by_cat_rec}
    with open(os.path.join(RESULTS, "e196_prep.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n任务包: {TASK}/rerank_tasks.json")
    print(f"评分包: {TASK}/rerank_gold.json")
    print(f"写入 {os.path.join(RESULTS, 'e196_prep.json')}")


if __name__ == "__main__":
    main()
