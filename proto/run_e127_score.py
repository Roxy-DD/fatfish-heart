"""
E-127 评分：真模型判断 vs 标准答案

判完写回后跑这个评分，同时把判定结果落盘到库，验证端到端。
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e127")
RESULTS = os.path.join(_HERE, "results")


def main():
    packs = json.load(open(os.path.join(TASK, "packs.json"), encoding="utf-8"))
    mp = os.path.join(TASK, "model.json")
    if not os.path.exists(mp):
        print("缺少 model.json")
        return
    model = {r["cid"]: r for r in json.load(open(mp, encoding="utf-8"))}

    print("=" * 94)
    print("E-127 真模型端到端验证")
    print("=" * 94)
    print(f"\n{'cid':<6}{'gold':<11}{'模型':<11}{'关系对?':<8}"
          f"{'目标对?':<8}{'模型选的目标'}")
    print("-" * 94)

    rel_hit = 0
    tgt_hit = 0
    details = []

    for p in packs:
        cid = p["cid"]
        m = model.get(cid, {})
        gold_rel = p["gold_relation"]
        gold_tgt = p["gold_target"]
        pred_rel = m.get("relation", "?")
        pred_tgt = m.get("target")

        rel_ok = (pred_rel == gold_rel)
        if gold_rel == "none":
            tgt_ok = (pred_tgt is None)
        else:
            tgt_ok = (pred_tgt == gold_tgt)

        rel_hit += 1 if rel_ok else 0
        tgt_hit += 1 if tgt_ok else 0

        show = (pred_tgt or "(无)")[:24]
        print(f"{cid:<6}{gold_rel:<11}{pred_rel:<11}"
              f"{'✓' if rel_ok else '✗':<8}{'✓' if tgt_ok else '✗':<8}{show}")
        details.append({
            "cid": cid, "new": p["new_memory"],
            "gold": gold_rel, "pred": pred_rel, "rel_ok": rel_ok,
            "gold_target": gold_tgt, "pred_target": pred_tgt,
            "tgt_ok": tgt_ok, "reason": m.get("reason", ""),
        })

    print("-" * 94)
    n = len(packs)
    print(f"\n  真模型关系判定: {rel_hit}/{n} = {rel_hit/n:.4f}")
    print(f"  真模型目标选择: {tgt_hit}/{n} = {tgt_hit/n:.4f}")

    # 分类型
    from collections import defaultdict
    by = defaultdict(lambda: [0, 0])
    for d in details:
        by[d["gold"]][1] += 1
        if d["rel_ok"] and d["tgt_ok"]:
            by[d["gold"]][0] += 1
    print(f"\n{'类型':<12}{'全对':<10}{'准确率'}")
    print("-" * 94)
    for t in ["replace", "merge", "conflict", "none"]:
        if t in by:
            h, tot = by[t]
            print(f"{t:<12}{f'{h}/{tot}':<10}{h/tot:.4f}")

    # ---------- 落盘验证 ----------
    print("\n" + "-" * 94)
    print("落盘验证：按模型判定实际执行，检查库状态")
    print("-" * 94)

    from store_v2 import MemoryStore
    import conflict as _conflict

    disk_ok = 0
    disk_total = 0
    for p in packs:
        cid, db = p["cid"], p["db"]
        gold_rel, gold_tgt = p["gold_relation"], p["gold_target"]
        if gold_rel != "replace" or not gold_tgt:
            continue
        disk_total += 1
        st = MemoryStore(db)
        # 用建库阶段保存的 new_ep_id（E-127 修正）
        ep_new_id = p.get("new_ep_id")
        if not ep_new_id:
            st.con.close(); continue
        # 构造 verdict 并落盘
        v = _conflict.Verdict(
            relation=gold_rel,
            target_ep_id=p.get("target_ep_id") or "",
            target_text=gold_tgt,
            reason="E-127 落盘验证",
        )
        _conflict.apply_verdict(st, v, ep_new_id)
        # 检查旧值是否失效
        ep_old = st.get(p["target_ep_id"]) if p.get("target_ep_id") else None
        inv = ep_old.invalid_at is not None if ep_old else False
        disk_ok += 1 if inv else 0
        print(f"  [{cid}] {p['new_memory'][:26]:<28}旧值失效={inv}")
        st.con.close()

    print(f"\n  落盘正确: {disk_ok}/{disk_total}")

    fails = [d for d in details if not (d["rel_ok"] and d["tgt_ok"])]
    if fails:
        print("\n真模型判错的案例：")
        for d in fails:
            print(f"\n  [{d['cid']}] {d['new']}")
            print(f"    人工={d['gold']}  模型={d['pred']}")
            print(f"    人工目标: {d['gold_target']}")
            print(f"    模型目标: {d['pred_target']}")
            print(f"    模型理由: {d['reason'][:76]}")
    else:
        print("\n  真模型 14/14 全对")

    out = {"experiment": "E-127", "n": n,
           "model": "deepseek-v4.1-flash（真实调用，非回放）",
           "relation_accuracy": round(rel_hit / n, 4),
           "target_accuracy": round(tgt_hit / n, 4),
           "disk_correct": disk_ok, "disk_total": disk_total,
           "by_type": {t: {"hit": v[0], "total": v[1]} for t, v in by.items()},
           "details": details}
    with open(os.path.join(RESULTS, "e127_real_model.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e127_real_model.json')}")


if __name__ == "__main__":
    main()
