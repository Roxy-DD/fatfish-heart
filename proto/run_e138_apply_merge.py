"""
E-138 执行融合并验证效果

模型判定结果：11 组全部 same_event=true
  F00 2→1, F01 5→1, F02 3→1, F03 3→1, F04 2→1,
  F05 2→1, F06 3→1, F07 2→1, F08 2→1, F09 2→1, F10 2→1
  共 30 条 → 11 条，省 19 条

验证：
  ① 融合后原文全部保留在审计记录里（R2）
  ② 被合并记录标 superseded_by 且不再返回（检索正确）
  ③ 融合文本包含双方独有信息（不丢信息）
  ④ 红线：数据条数不变（只标失效不删）
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e137")
RESULTS = os.path.join(_HERE, "results")
DB = os.path.join(_HERE, "cosmos_mem", "dsh_mem.db")


def main():
    from store_v2 import MemoryStore
    import merge as _merge

    st = MemoryStore(DB)

    groups = {g["cid"]: g for g in
              json.load(open(os.path.join(TASK, "fuzzy.json"), encoding="utf-8"))}
    verdicts = {v["cid"]: v for v in
                json.load(open(os.path.join(TASK, "merged.json"), encoding="utf-8"))}

    print("=" * 92)
    print("E-138 执行融合")
    print("=" * 92)

    before_total = st.con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
    before_valid = st.con.execute(
        "SELECT COUNT(*) FROM episodes WHERE invalid_at IS NULL").fetchone()[0]
    print(f"\n融合前: 总 {before_total}  有效 {before_valid}")

    print(f"\n{'组':<7}{'条数':<8}{'动作':<12}{'保留':<12}{'结果'}")
    print("-" * 92)

    results = []
    for cid in sorted(groups):
        g = groups[cid]
        v = verdicts.get(cid, {})
        n_before = len(g["ep_ids"])
        actions = _merge.apply_merge(st, g, v)
        ok = "kept" in actions
        n_merged = len(actions.get("merged", []))

        print(f"{cid:<7}{n_before:<8}{'融合' if ok else '跳过':<12}"
              f"{actions.get('kept', '-')[:10]:<12}"
              f"{'✓ ' + str(n_merged) + ' 条并入' if ok else actions.get('skipped', '')}")

        results.append({
            "cid": cid, "n_before": n_before,
            "kept": actions.get("kept"), "merged": actions.get("merged", []),
            "ok": ok, "skipped": actions.get("skipped"),
            "merged_text": actions.get("merged_text"),
        })

    # ---------- 验证 ----------
    after_total = st.con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
    after_valid = st.con.execute(
        "SELECT COUNT(*) FROM episodes WHERE invalid_at IS NULL").fetchone()[0]

    print("\n" + "=" * 92)
    print("验证")
    print("=" * 92)

    # ① R2：总数不变
    ok_r2 = (after_total == before_total)
    print(f"\n① R2 不遗忘（不许物理删除）")
    print(f"   融合前总记录 {before_total} → 融合后 {after_total}"
          f"  {'✅ 未删除' if ok_r2 else '❌ 有删除'}")

    # ② 被合并记录不再返回
    print(f"\n② 被合并记录应不再出现在检索结果里")
    n_merged_total = sum(len(r["merged"]) for r in results)
    print(f"   已标记失效 {n_merged_total} 条")
    print(f"   有效记忆 {before_valid} → {after_valid}"
          f"  {'✅ 减少 ' + str(before_valid - after_valid) + ' 条' if after_valid < before_valid else '⚠ 无变化'}")

    # 抽样检查一条
    sample = next((r for r in results if r["merged"]), None)
    if sample:
        kept_text = st.get(sample["kept"]).raw_text
        res = st.search(kept_text[:20], top=5)
        ids = [ep.ep_id for ep, *_ in res]
        merged_still = any(m in ids for m in sample["merged"])
        print(f"\n   抽查 {sample['cid']}: 保留 [{sample['kept'][:8]}]")
        print(f"     融合文本: {kept_text[:70]}")
        print(f"     被合并的 {len(sample['merged'])} 条是否仍返回: "
              f"{'❌ 仍返回' if merged_still else '✅ 已排除'}")

    # ③ 审计记录含原文
    print(f"\n③ 审计记录是否保留全部原文")
    n_audit = st.con.execute(
        "SELECT COUNT(*) FROM meta WHERE key LIKE 'merge:%'").fetchone()[0]
    print(f"   merge 审计记录: {n_audit} 条")
    if n_audit:
        k, val = st.con.execute(
            "SELECT key, val FROM meta WHERE key LIKE 'merge:%' LIMIT 1").fetchone()
        d = json.loads(val)
        n_before_texts = len(d.get("before_texts") or {})
        print(f"   示例 {k}: 保留原文 {n_before_texts} 条，"
              f"diff {len(d.get('diffs') or [])} 项")

    # ④ 信息不丢失检查
    print(f"\n④ 融合文本是否含各方独有信息")
    loss_check = []
    for r in results:
        if not r["ok"] or not r["merged_text"]:
            continue
        kept = st.get(r["kept"])
        if not kept:
            continue
        g = groups[r["cid"]]
        merged_text = kept.raw_text
        # 检查：原文里的关键片段（长 6 字以上）是否都在融合文本里
        missing = []
        for t in g["texts"]:
            # 取原文的实词片段做抽查
            for seg in _key_segments(t):
                if seg not in merged_text:
                    missing.append(seg)
        loss_check.append({
            "cid": r["cid"], "n_texts": len(g["texts"]),
            "missing_segments": missing[:5],
            "missing_count": len(missing),
        })

    n_clean = sum(1 for x in loss_check if x["missing_count"] == 0)
    print(f"   检查 {len(loss_check)} 组，完全无丢失 {n_clean} 组")
    for x in loss_check:
        if x["missing_count"] > 0:
            print(f"     {x['cid']}: 缺失 {x['missing_count']} 个片段 "
                  f"{x['missing_segments']}")

    # ---------- 汇总 ----------
    print("\n" + "=" * 92)
    print("汇总")
    print("=" * 92)
    n_ok = sum(1 for r in results if r["ok"])
    print(f"  融合成功: {n_ok}/{len(results)} 组")
    print(f"  有效记忆: {before_valid} → {after_valid}"
          f"（减少 {before_valid - after_valid} 条重复）")
    print(f"  总记录:  {before_total} → {after_total}（{'不变 ✅' if ok_r2 else '变了 ❌'}）")
    print(f"  审计保留原文: {'✅' if n_audit == n_ok else '⚠ ' + str(n_audit) + ' 条'}")
    print(f"  信息无丢失: {n_clean}/{len(loss_check)} 组")

    out = {"experiment": "E-138",
           "before_total": before_total, "after_total": after_total,
           "before_valid": before_valid, "after_valid": after_valid,
           "n_groups": len(results), "n_merged_ok": n_ok,
           "n_records_merged": n_merged_total,
           "r2_no_delete": ok_r2,
           "audit_records": n_audit,
           "info_loss": loss_check,
           "details": results}
    with open(os.path.join(RESULTS, "e138_merge_applied.json"),
              "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    st.con.close()
    print(f"\n写入 {os.path.join(RESULTS, 'e138_merge_applied.json')}")


def _key_segments(text, n=3):
    """取原文里最有辨识度的片段（用于检查融合是否丢信息）"""
    t = (text or "").strip()
    segs = []
    # 引号内的内容
    import re
    for m in re.finditer(r"[“\"']([^”\"']{4,})[”\"']", t):
        segs.append(m.group(1))
    # 长数字/英文
    for m in re.finditer(r"[A-Za-z]{4,}|\d{3,}", t):
        segs.append(m.group(0))
    return segs[:n]


if __name__ == "__main__":
    main()
