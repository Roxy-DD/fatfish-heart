"""
E-186b 评分：精排能否救回 54 题

判定：
  这 54 题都是"基线漏掉、但 evidence 在 top-30 候选里"的题。
  精排的目标：从 30 候选里找出 evidence 对应的那条。

  如果精排能命中 evidence → 说明"证据确实在池里，只是排序分不开"（E-185 结论成立）
  如果精排也找不到 → 说明 LLM 判断也分不清（那问题更复杂）
"""
from __future__ import annotations

import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e186b")
RESULTS = os.path.join(_HERE, "results")
DATA = os.path.join(_HERE, "work", "e165", "locomo10.json")


def main():
    tp = os.path.join(TASK, "recoverable.json")
    sp = os.path.join(TASK, "picks.json")
    if not os.path.exists(sp):
        print("缺少 picks.json")
        return

    tasks = json.load(open(tp, encoding="utf-8"))
    picks = {r["cid"]: r for r in json.load(open(sp, encoding="utf-8"))}

    # 重建 mark 映射（需要原始对话，不能用 tasks 里的文本）
    # 简化：用 tasks 里的 ep_id 反查 mark —— 但 tasks 没存 mark
    # 重新建库
    from store_v2 import MemoryStore
    data = json.load(open(DATA, encoding="utf-8"))

    # 收集所有 candidate ep_id -> 文本，用于从 mark 反推
    mark_of = {}
    for conv in data[:2]:
        c = conv.get("conversation") or {}
        j = 1
        while f"session_{j}" in c:
            for ti, turn in enumerate(c[f"session_{j}"] or [], start=1):
                spk = turn.get("speaker") or ""
                txt = turn.get("text") or turn.get("content") or ""
                txt = re.sub(r"\s+", " ", str(txt)).strip()
                if len(txt) < 5:
                    continue
                full = f"[{c.get(f'session_{j}_date_time','')}] {spk}: {txt}"
                mark_of[full[:800]] = f"D{j}:{ti}"
            j += 1

    # 建立 ep_id -> mark，通过重新建库
    db = os.path.join(TASK, "map.db")
    for suf in ("", "-wal", "-shm"):
        p = db + suf
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass
    st = MemoryStore(db)
    ep_to_mark = {}
    for conv in data[:2]:
        c = conv.get("conversation") or {}
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
                    ep_to_mark[ep.ep_id] = f"D{j}:{ti}"
                except Exception:
                    pass
            j += 1
    st.con.close()

    hit = 0
    n = 0
    details = []

    for t in tasks:
        cid = t["cid"]
        ev = set(t["evidence"])
        pk = picks.get(cid, {}).get("picks") or []

        # 精排选中的 mark
        picked_marks = {ep_to_mark.get(eid) for eid in pk}
        picked_marks.discard(None)

        ok = bool(ev & picked_marks)
        n += 1
        hit += 1 if ok else 0

        details.append({
            "cid": cid, "question": t["question"][:50],
            "ok": ok, "n_picks": len(pk),
        })

    # ---------- 汇总 ----------
    print("=" * 94)
    print("E-186b 精排救回率")
    print("=" * 94)

    rate = hit / max(1, n)
    print(f"\n  可救回题: {n}")
    print(f"  精排命中: {hit}/{n} = {rate:.4f}")

    # 对比
    print(f"\n  整体基线（前 2 对话）: 114/302 = 0.3775")
    print(f"  若精排全救回 54 题:   (114+54)/302 = {(114+54)/302:.4f}")
    print(f"  实际精排救回:          (114+{hit})/302 = {(114+hit)/302:.4f}")

    print(f"\n  精排在这 54 题上的准确率: {rate:.4f}")

    print("\n" + "=" * 94)
    print("结论")
    print("=" * 94)

    if rate > 0.6:
        print(f"\n  → ✅ 精排有效（{rate:.0%}），LLM 能从 30 条里找出证据")
        print(f"  → 证明 E-185 的判断：问题在排序精度，不在语义匹配")
        print(f"  → 精排作为第二级（rerank）是正确方向")
    elif rate > 0.3:
        print(f"\n  → 精排部分有效（{rate:.0%}），能救回一部分")
    else:
        print(f"\n  → ❌ 精排效果差（{rate:.0%}），LLM 也分不清这 30 条")
        print(f"  → 说明这些题确实是 hard case，需要更强的手段")

    out = {"experiment": "E-186b", "n": n, "hit": hit,
           "rate": round(rate, 4),
           "overall_baseline": 0.3775,
           "overall_with_rerank": round((114 + hit) / 302, 4),
           "details": details}
    with open(os.path.join(RESULTS, "e186b_rerank.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e186b_rerank.json')}")


if __name__ == "__main__":
    main()
