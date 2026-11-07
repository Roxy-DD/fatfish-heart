"""
E-182 席位扩展对「全证据召回」的影响

E-166 全量结果：
  Recall@10（任一证据命中）  = 0.5802
  全证据召回（所有证据都命中）= 0.4859

差距 9.4pp。这部分的题：找到了一部分证据，但不全。

问题：是席位不够（证据排在第 11-30 名）还是真的找不到（排名 100+）？

方法：用真实 store.search，只改 top，看：
  top=10 / 20 / 30 / 50 时
  ① 任一命中率
  ② 全证据命中率
如果 top 放大后全证据召回显著上升 → 席位是瓶颈
如果上升很慢 → 证据确实找不到

这会直接回答"聚合能力"该往哪个方向优化。
"""
from __future__ import annotations

import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e182")
os.makedirs(TASK, exist_ok=True)
RESULTS = os.path.join(_HERE, "results")
DATA = os.path.join(_HERE, "work", "e165", "locomo10.json")

N_CONV = 3
TOPS = [10, 20, 30, 50]


def main():
    from store_v2 import MemoryStore

    data = json.load(open(DATA, encoding="utf-8"))

    print("=" * 94)
    print("E-182 席位扩展对「全证据召回」的影响")
    print("=" * 94)
    print(f"\n跑前 {N_CONV} 个对话，top ∈ {TOPS}\n")

    # 建库
    all_data = []
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
        all_data.append((sid, db, qa, mark_of))

    print(f"库已建好（3 个对话）\n")
    print(f"{'top':<8}{'任一命中':<18}{'全证据命中':<18}{'两者差距'}")
    print("-" * 94)

    rows = []
    for topn in TOPS:
        any_hit = all_hit = total = 0
        for sid, db, qa, mark_of in all_data:
            st = MemoryStore(db)
            for q in qa:
                question = q.get("question") or ""
                ev = q.get("evidence") or []
                if not isinstance(ev, list):
                    ev = [ev]
                ev = [str(e).strip() for e in ev if e]
                if not question or not ev:
                    continue
                res = st.search(question, top=topn)
                got = {mark_of.get(t[0].ep_id) for t in res}
                got.discard(None)
                total += 1
                any_hit += 1 if any(e in got for e in ev) else 0
                all_hit += 1 if all(e in got for e in ev) else 0
            st.con.close()

        ra = any_hit / max(1, total)
        rall = all_hit / max(1, total)
        rows.append({"top": topn, "any": round(ra, 4),
                     "all": round(rall, 4), "gap": round(ra - rall, 4)})
        print(f"{topn:<8}{f'{any_hit}/{total} = {ra:.4f}':<18}"
              f"{f'{all_hit}/{total} = {rall:.4f}':<18}{ra - rall:.4f}")

    # ---------- 结论 ----------
    print("\n" + "=" * 94)
    print("结论")
    print("=" * 94)

    r10 = next(r for r in rows if r["top"] == 10)
    r50 = next(r for r in rows if r["top"] == 50)

    print(f"\n  top=10: 任一 {r10['any']:.4f}  全证据 {r10['all']:.4f}")
    print(f"  top=50: 任一 {r50['any']:.4f}  全证据 {r50['all']:.4f}")
    print(f"\n  全证据召回的提升: {r50['all'] - r10['all']:+.4f}")
    print(f"  任一召回的提升:   {r50['any'] - r10['any']:+.4f}")

    if r50["all"] - r10["all"] > 0.10:
        print(f"\n  → ✅ 席位是瓶颈：放大到 50 后全证据召回提升 "
              f"{r50['all']-r10['all']:.2f}")
        print(f"  → 解法：多证据任务应该用更大 top，或做迭代召回")
    elif r50["all"] - r10["all"] > 0.03:
        print(f"\n  → 席位有中等影响（+{r50['all']-r10['all']:.3f}）")
    else:
        print(f"\n  → 席位不是主要瓶颈（仅 +{r50['all']-r10['all']:.3f}）")
        print(f"  → 多证据缺失是因为证据本身找不到，不是排不进去")

    out = {"experiment": "E-182", "n_conv": N_CONV, "rows": rows}
    with open(os.path.join(RESULTS, "e182_slots.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e182_slots.json')}")


if __name__ == "__main__":
    main()
