"""
E-139（修正版）duplicate 作为第五种关系

上一版失败原因：测试集的"基准记忆"是我在独立测试库造的，
真实库里根本没有 → gold 目标 1/20 在库。

修正做法：
  ① 从真实库中取实际记忆作为基准
  ② duplicate 组：用已有记忆的同义改写作为"新记忆"
  ③ 其他组：用真实库中能构成 replace/merge/conflict/none 的组合
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
RESULTS = os.path.join(_HERE, "results")
DB = os.path.join(_HERE, "cosmos_mem", "dsh_mem.db")


def main():
    from store_v2 import MemoryStore
    from store_v1 import Embedder
    import conflict

    st = MemoryStore(DB)
    emb = Embedder.get()

    rows = st.con.execute(
        "SELECT ep_id, raw_text FROM episodes WHERE invalid_at IS NULL").fetchall()
    print("=" * 90)
    print("E-139b duplicate 是否可作为第五种关系")
    print("=" * 90)
    print(f"\n库中有效记忆 {len(rows)} 条")

    # 从真实库中挑 8 条内容较完整的作为 duplicate 基准
    # 用同义改写生成"新记忆"
    cand_rows = [r for r in rows if 30 <= len(r[1]) <= 200][:40]

    duplicate_cases = []
    for eid, txt in cand_rows[:8]:
        # 同义改写：加/删虚词，换等价说法（保持语义）
        variant = _paraphrase(txt)
        if variant and variant != txt:
            duplicate_cases.append((variant, txt, "duplicate"))

    print(f"\n生成 {len(duplicate_cases)} 组同义改写:")
    for new, old, _ in duplicate_cases:
        print(f"\n  原: {old[:64]}")
        print(f"  改: {new[:64]}")

    # 构造其他关系组：用真实库中相似的成对记忆
    others = []
    st._load_vectors(include_invalid=False)
    import numpy as np
    norms = np.linalg.norm(st._vec, axis=1)
    norms[norms == 0] = 1.0
    V = st._vec / norms[:, None]
    S = V @ V.T
    text_of = {e: t for e, t in rows}
    ids = [e for e, _ in rows]

    # 找余弦 0.5~0.9 之间的成对（不像重复，但相关）
    seen = set()
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            if 0.50 <= S[i, j] <= 0.88:
                key = (ids[i], ids[j])
                if key in seen:
                    continue
                seen.add(key)
                others.append({
                    "a_id": ids[i], "b_id": ids[j],
                    "a": text_of[ids[i]], "b": text_of[ids[j]],
                    "cos": round(float(S[i, j]), 4),
                })
                if len(others) >= 12:
                    break
        if len(others) >= 12:
            break

    print(f"\n\n库里余弦 0.5-0.88 的相关记忆对: {len(others)} 组")
    for o in others[:6]:
        print(f"\n  cos={o['cos']}")
        print(f"    A: {o['a'][:60]}")
        print(f"    B: {o['b'][:60]}")

    # ---------- 写任务包 ----------
    out_dir = os.path.join(_HERE, "work", "e139b")
    os.makedirs(out_dir, exist_ok=True)

    packs = []
    for i, (new, old, expect) in enumerate(duplicate_cases):
        pack = conflict.prepare_candidates(st, emb, new, top_k=20)
        in_pack = any(c.text == old for c in pack.candidates)
        packs.append({
            "cid": f"P{i:02d}",
            "kind": "duplicate",
            "new_memory": new,
            "gold_relation": expect,
            "gold_target": old,
            "gold_in_pack": in_pack,
            "candidates": [
                {"text": c.text, "cos": c.cos,
                 "value_types": c.value_types,
                 "value_tokens": c.value_tokens}
                for c in pack.candidates
            ],
        })

    print(f"\n" + "=" * 90)
    print("任务包")
    print("=" * 90)
    n_in = sum(1 for p in packs if p["gold_in_pack"])
    print(f"  duplicate 组：gold 目标在候选包中 {n_in}/{len(packs)}")

    with open(os.path.join(out_dir, "packs.json"), "w", encoding="utf-8") as f:
        json.dump(packs, f, ensure_ascii=False, indent=1)
    with open(os.path.join(out_dir, "pairs.json"), "w", encoding="utf-8") as f:
        json.dump(others, f, ensure_ascii=False, indent=1)

    st.con.close()
    print(f"  → {out_dir}/packs.json ({len(packs)} 组)")
    print(f"  → {out_dir}/pairs.json ({len(others)} 组)")

    out = {"experiment": "E-139b",
           "n_valid": len(rows),
           "duplicate_cases": len(duplicate_cases),
           "gold_in_pack": n_in,
           "other_pairs": len(others)}
    with open(os.path.join(RESULTS, "e139b_dup.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)


def _paraphrase(text):
    """机械同义改写（保持语义不变，只改措辞）"""
    t = (text or "").strip()
    subs = [
        ("用户要求", "用户希望"),
        ("用户希望", "用户要求"),
        ("所有的", "所有"),
        ("必须", "一定要"),
        ("不接受", "不能接受"),
        ("应该", "应当"),
        ("能够", "可以"),
        ("包括", "包含"),
        ("具体", "具体地"),
        ("每次", "每一"),
        ("之前", "以前"),
        ("通过", "经由"),
    ]
    out = t
    applied = 0
    for a, b in subs:
        if a in out:
            out = out.replace(a, b, 1)
            applied += 1
            if applied >= 2:
                break
    # 末尾加/去掉"。"形成差异
    if out == t:
        out = t.replace("。", "") + "，这是明确的。"
    return out


if __name__ == "__main__":
    main()
