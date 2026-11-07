"""
E-191 批量精排 vs 逐条精排：一次看 50 条能否保持准确率

E-186b 逐条判断（30 条）命中率 90.7%，但成本高（1620 次判断）。
E-190 证明便宜信号做不了粗筛（余弦/关键词都分不开）。

本实验验证：把 50 条候选**打包成一次 LLM 调用**，让模型一次看完并排序，
  准确率能否与逐条判断相当？

如果批量判断准确率接近逐条 → 成本从 50 次调用降到 1 次调用，精排可落地。

规模：全量 10 对话里，抽样 100 个"可救回题"，让 LLM 批量排序。
"""
from __future__ import annotations

import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e191")
os.makedirs(TASK, exist_ok=True)
RESULTS = os.path.join(_HERE, "results")
DATA = os.path.join(_HERE, "work", "e165", "locomo10.json")

POOL = 30
N_SAMPLE = 100
N_CONV = 10


def main():
    from store_v2 import MemoryStore

    data = json.load(open(DATA, encoding="utf-8"))

    print("=" * 94)
    print("E-191 批量精排任务包（一次看全部候选）")
    print("=" * 94)

    # 收集可救回题（基线漏掉但证据在 top-30），最多 N_SAMPLE 个
    samples = []
    base_hit = 0
    total = 0

    for conv in data[:N_CONV]:
        if len(samples) >= N_SAMPLE:
            break
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
            if len(samples) >= N_SAMPLE:
                break
            question = q.get("question") or ""
            ev = q.get("evidence") or []
            if not isinstance(ev, list):
                ev = [ev]
            ev = [str(e).strip() for e in ev if e]
            if not question or not ev:
                continue

            total += 1
            res = st.search(question, top=POOL)
            cands = []
            for tup in res:
                ep = tup[0]
                cands.append({
                    "ep_id": ep.ep_id,
                    "mark": mark_of.get(ep.ep_id),
                    "text": ep.raw_text[:200],
                })

            got10 = {c2["mark"] for c2 in cands[:10] if c2["mark"]}
            if any(e in got10 for e in ev):
                base_hit += 1
                continue

            got30 = {c2["mark"] for c2 in cands if c2["mark"]}
            if not any(e in got30 for e in ev):
                continue

            # 可救回题
            samples.append({
                "cid": f"S{len(samples):03d}",
                "question": question,
                "evidence": ev,  # 标记给评分用，LLM 看不到
                "candidates": cands,
            })

        st.con.close()

    with open(os.path.join(TASK, "batch_tasks.json"), "w",
              encoding="utf-8") as f:
        json.dump(samples, f, ensure_ascii=False, indent=1)

    print(f"\n  抽样 {len(samples)} 个可救回题")
    print(f"  任务包 → {TASK}/batch_tasks.json")

    out = {"experiment": "E-191", "n_samples": len(samples),
           "pool": POOL, "base_hit": base_hit, "total_seen": total}
    with open(os.path.join(RESULTS, "e191_prep.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"写入 {os.path.join(RESULTS, 'e191_prep.json')}")


if __name__ == "__main__":
    main()
