"""
E-71 第 2 步：改造后生成新的自检包，交给模型再判一次

对比 E-70：模型当时说 22/30 能答（73.3%）
E-71 改造后：看模型会不会说"现在能答了"

如果模型的"能答率"提升 → 闭环成立：
  模型发现问题 → 自己提改法 → 执行 → 模型自己验证改好了
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
RESULTS = os.path.join(_HERE, "results")
TASK = os.path.join(_HERE, "work", "e70_task")
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")


def main():
    from store_v1 import MemoryStore
    sys.path.insert(0, _HERE)
    from run_e71_fields import StoreV12

    corpus = json.load(open(os.path.join(TASK, "corpus.json"), encoding="utf-8"))
    goals = json.load(open(os.path.join(TASK, "goals.json"), encoding="utf-8"))

    db = os.path.join(TASK, "e71b.db")
    for suf in ("", "-wal", "-shm"):
        p = db + suf
        if os.path.exists(p):
            os.remove(p)

    base = MemoryStore(db)
    st = StoreV12(base)
    mid2eid = {}
    for r in corpus:
        ep = st.add(r["memory"])
        mid2eid[r["mid"]] = ep.ep_id

    # 构造自检包（带结构化字段的展示，便于模型判断）
    pack = []
    meta = []
    n = 0
    for g in goals:
        for q in g["questions"]:
            res = st.search(q["q"], top=3)
            cands = []
            for k, (ep, sc) in enumerate(res):
                f = st.fields.get(ep.ep_id, {})
                enrich = []
                for key in ("emotion", "trigger", "street", "date"):
                    v = f.get(key)
                    if v:
                        enrich.append(f"{key}={v}")
                cands.append({
                    "cid": f"K{n:03d}-{k}",
                    "memory": ep.raw_text,
                    "fields": enrich,
                    "is_target": ep.ep_id == mid2eid[g["mid"]],
                })
            pack.append({"jid": f"K{n:03d}", "question": q["q"],
                         "candidates": cands})
            meta.append({"jid": f"K{n:03d}", "mid": g["mid"],
                         "type": q["type"], "q": q["q"]})
            n += 1

    with open(os.path.join(TASK, "selfcheck_pack_v2.json"), "w", encoding="utf-8") as f:
        json.dump(pack, f, ensure_ascii=False, indent=1)
    with open(os.path.join(TASK, "selfcheck_meta_v2.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)

    print("=" * 88)
    print("E-71 第 2 步：改造后的自检包")
    print("=" * 88)
    print(f"\n{n} 个问题，每问 3 条候选（已附加结构化字段）\n")

    # 样例
    p0 = pack[24]     # J023 对应的问题（排工期）
    print("关键样例（之前失败的那条）：")
    print(f"  问题: {p0['question']}")
    for c in p0["candidates"]:
        mark = " ←目标" if c["is_target"] else ""
        print(f"    {c['memory'][:40]}  字段={c['fields']}{mark}")

    obj = {p["jid"]: any(c["is_target"] for c in p["candidates"]) for p in pack}
    print(f"\n客观命中: {sum(obj.values())}/{n}")
    with open(os.path.join(TASK, "objective_v2.json"), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
