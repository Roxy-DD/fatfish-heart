"""
E-186b 精排聚焦验证：只测"可救回"的题

E-186 失败原因：任务量太大（100×30=3000 次判断），子代理超时。

本实验收窄：
  ① 只取「基线漏掉 + evidence 在 top-30 候选里」的题（真正可救回的）
  ② 这类题数量少（E-185 显示约 12%），LLM 只需判断少量候选

关键：精确判断"evidence 是否在 top-30 候选里"，
      需要用 ep_id -> mark 映射（而不是文本匹配）。
"""
from __future__ import annotations

import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e186b")
os.makedirs(TASK, exist_ok=True)
RESULTS = os.path.join(_HERE, "results")
DATA = os.path.join(_HERE, "work", "e165", "locomo10.json")

K = 10
POOL = 30
N_CONV = 2   # 前 2 个对话，够找到足够多的可救回题


def main():
    from store_v2 import MemoryStore

    data = json.load(open(DATA, encoding="utf-8"))

    print("=" * 94)
    print("E-186b 聚焦精排：只测可救回的题")
    print("=" * 94)

    recoverable = []
    base_hit = 0
    total = 0

    for conv in data[:N_CONV]:
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
            question = q.get("question") or ""
            ev = q.get("evidence") or []
            if not isinstance(ev, list):
                ev = [ev]
            ev = [str(e).strip() for e in ev if e]
            if not question or not ev:
                continue

            total += 1
            res = st.search(question, top=POOL)
            cands = [(t[0].ep_id, mark_of.get(t[0].ep_id))
                     for t in res]

            top10 = {m for _, m in cands[:K] if m}
            allpool = {m for _, m in cands if m}

            hit = any(e in top10 for e in ev)
            if hit:
                base_hit += 1
            else:
                # 基线漏掉，但 evidence 在 pool 里 → 可救回
                in_pool = any(e in allpool for e in ev)
                if in_pool:
                    recoverable.append({
                        "cid": f"Q{len(recoverable):02d}",
                        "question": question,
                        "evidence": ev,
                        "candidates": [
                            {"ep_id": eid, "text": st.get(eid).raw_text[:400]}
                            for eid, _ in cands
                        ],
                    })

        st.con.close()

    # ---------- 汇总 ----------
    print(f"\n前 {N_CONV} 个对话，共 {total} 题")
    print(f"  基线命中: {base_hit}/{total} = {base_hit/total:.4f}")
    print(f"  基线漏掉但证据在 top-{POOL} 里（可救回）: {len(recoverable)} 题")
    print(f"  可救回率 = {len(recoverable)/(total-base_hit):.1%}（占失败题的比例）")

    if recoverable:
        with open(os.path.join(TASK, "recoverable.json"), "w",
                  encoding="utf-8") as f:
            json.dump(recoverable, f, ensure_ascii=False, indent=1)
        print(f"\n  任务包 → {TASK}/recoverable.json")
        print(f"  LLM 只需精排 {len(recoverable)} 题（而非 100 题）")

    out = {"experiment": "E-186b",
           "total": total, "base_hit": base_hit,
           "recoverable": len(recoverable),
           "recoverable_ratio": round(len(recoverable) / max(1, total - base_hit), 4)}
    with open(os.path.join(RESULTS, "e186b_prep.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e186b_prep.json')}")


if __name__ == "__main__":
    main()
