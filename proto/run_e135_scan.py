"""
E-135 批量补判：扫描历史记忆，找出未判定且需要判定的

现状（实测）：
  有效记忆 136 条
  已判定 4 条
  未判定 132 条
  待判定包 19 条（其中有判定失败的）

问题：历史记忆之间可能已有矛盾，但因写入时未做判定而漏掉。
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e135")
os.makedirs(TASK, exist_ok=True)
RESULTS = os.path.join(_HERE, "results")

DB = os.path.join(_HERE, "cosmos_mem", "dsh_mem.db")


def main():
    from store_v2 import MemoryStore
    from store_v1 import Embedder
    import conflict

    st = MemoryStore(DB)
    emb = Embedder.get()

    print("=" * 88)
    print("E-135 扫描未判定的记忆")
    print("=" * 88)

    # 所有有效记忆
    rows = st.con.execute(
        "SELECT ep_id, raw_text FROM episodes WHERE invalid_at IS NULL "
        "ORDER BY created_at DESC").fetchall()

    # 已判定的 ep_id
    judged = set()
    for (k,) in st.con.execute(
            "SELECT key FROM meta WHERE key LIKE 'conflict:%'").fetchall():
        judged.add(k.replace("conflict:", ""))

    need = []
    skipped_low = 0
    skipped_judged = 0

    print(f"\n扫描 {len(rows)} 条有效记忆...")
    for i, (eid, txt) in enumerate(rows):
        if eid in judged:
            skipped_judged += 1
            continue

        pack = conflict.prepare_candidates(st, emb, txt, top_k=10)
        if not conflict.needs_model(pack):
            skipped_low += 1
            continue

        need.append({
            "ep_id": eid,
            "text": txt,
            "top_cos": pack.candidates[0].cos if pack.candidates else 0,
            "top_text": pack.candidates[0].text if pack.candidates else "",
            "n_cands": len(pack.candidates),
        })

        if (i + 1) % 20 == 0:
            print(f"  已扫 {i+1}/{len(rows)}，需要判定 {len(need)} 条")

    print(f"\n{'=' * 88}")
    print("结果")
    print(f"{'=' * 88}")
    print(f"  已判定过: {skipped_judged}")
    print(f"  相似度低、判为无关: {skipped_low}")
    print(f"  **需要模型判定: {len(need)}**")
    print(f"  占比: {len(need)/len(rows):.1%}")

    # 按相似度排序，最可能矛盾的排前面
    need.sort(key=lambda x: -x["top_cos"])

    print(f"\n  需要判定的（按相似度降序，前 20 条）:")
    print(f"  {'':<3}{'cos':<9}{'记忆'}")
    print(f"  {'-' * 78}")
    for i, item in enumerate(need[:20]):
        print(f"  {i+1:<3}{item['top_cos']:<9.4f}{item['text'][:40]}")
        print(f"     └→ 最相近: {item['top_text'][:44]}")

    # 写任务包
    packs = []
    for i, item in enumerate(need):
        pack = conflict.prepare_candidates(st, emb, item["text"], top_k=20)
        packs.append({
            "cid": f"U{i:03d}",
            "ep_id": item["ep_id"],
            "new_memory": item["text"],
            "new_value_types": pack.new_value_types,
            "new_value_tokens": pack.new_value_tokens,
            "candidates": [
                {"text": c.text, "cos": c.cos,
                 "value_types": c.value_types, "value_tokens": c.value_tokens}
                for c in pack.candidates
            ],
        })

    with open(os.path.join(TASK, "packs.json"), "w", encoding="utf-8") as f:
        json.dump(packs, f, ensure_ascii=False, indent=1)

    st.con.close()

    print(f"\n  已写出 {len(packs)} 个任务包 → {TASK}/packs.json")

    out = {"experiment": "E-135",
           "total_valid": len(rows),
           "already_judged": skipped_judged,
           "low_similarity": skipped_low,
           "need_judge": len(need),
           "ratio": round(len(need) / len(rows), 4),
           "items": need}
    with open(os.path.join(RESULTS, "e135_scan.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"  写入 {os.path.join(RESULTS, 'e135_scan.json')}")


if __name__ == "__main__":
    main()
