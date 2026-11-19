"""MemoryAgentBench 冲突消解评测（FISH-32 / E-541）

════════════════════════════════════════════════════════════
为什么测这个
════════════════════════════════════════════════════════════
MemoryAgentBench 的 Conflict Resolution 直接对应我们的 R4 红线：
「新记忆覆盖旧记忆，但旧值要可溯源」。

数据来自 MQUAKE 的反事实编辑对：同一事实先出现真值，后出现矛盾版本。
Agent 被要求**优先采信后来的信息**。这测的正是"知识更新不漂移"。

════════════════════════════════════════════════════════════
为什么不用官方脚本
════════════════════════════════════════════════════════════
官方流程需要 LLM 生成答案（generation_max_length=10），
本地没有 OPENAI_API_KEY。

但评分本身是 `substring_exact_match`（纯字符串匹配，不需要 judge）。
所以我们自己实现：把 context 灌进记忆库 → 用问题检索 → 
看检索到的 top-k 里是否包含正确答案的子串。

这测的是**检索层能否把更新后的事实捞出来**，
比官方"LLM 生成再匹配"更严格也更能暴露问题。

════════════════════════════════════════════════════════════
数据事实（已核实）
════════════════════════════════════════════════════════════
· 数据集：ai-hyz/MemoryAgentBench, split=Conflict_Resolution
· 8 行，每行 context ≈ 26K 字符 + 100 questions
· 字段：context / questions / answers / metadata
· metadata.qa_pair_ids 含 source 标识（factconsolidation_sh_6k 等）

用法：
    python run_e541_mab_conflict.py
    python run_e541_mab_conflict.py --max-rows 1 --top 5
"""
import argparse
import json
import os
import sys
import tempfile
import time
import warnings

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "cosmos_mem"))

import httpx2_compat  # noqa: F401  必须先于 datasets


def chunk_context(text, size=400):
    """把长 context 切成记忆片段

    ★ 关键修正（E-541 实测发现）：
    第一版按 400 字符硬切，会把完整事实切断
    （如 "322. Amala Paul is a citizen of Belgium." 被拆成两半），
    导致答案丢失、命中率只有 19%。

    实测：context 是编号事实列表 "0. xxx. 1. yyy. ..."，
    98/100 的答案完整存在于**单条事实**内。
    所以必须按事实边界切，保持每条事实完整。
    """
    import re
    # 匹配 "N. 内容." 的形式
    facts = re.findall(r"\d+\.\s+[^.]*\.", text)
    if facts:
        return [f.strip() for f in facts if f.strip()]

    # 兜底：没有编号事实时按句子切
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    chunks, buf = [], ""
    for p in parts:
        if not p.strip():
            continue
        if len(buf) + len(p) <= size:
            buf = (buf + " " + p).strip()
        else:
            if buf:
                chunks.append(buf)
            buf = p[:size]
    if buf:
        chunks.append(buf)
    return chunks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-rows", type=int, default=2)
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--chunk", type=int, default=400)
    ap.add_argument("--row", type=int, default=None,
                    help="指定第几行（sh 在 4-7，mh 在 0-3）")
    args = ap.parse_args()

    from datasets import load_dataset
    from store_v2 import MemoryStore

    print("=" * 70)
    print("E-541 MemoryAgentBench 冲突消解评测")
    print("=" * 70)

    print("\n[1] 加载数据")
    ds = load_dataset("ai-hyz/MemoryAgentBench", split="Conflict_Resolution")
    print(f"    共 {len(ds)} 行")
    if args.row is not None:
        rows = ds.select([args.row])
    else:
        rows = ds.select(range(min(args.max_rows, len(ds))))

    all_stats = []
    for ri, row in enumerate(rows):
        ctx = row.get("context") or ""
        qs = row.get("questions") or []
        ans = row.get("answers") or []
        meta = row.get("metadata") or {}
        source = (meta.get("qa_pair_ids") or ["?"])[0]

        print(f"\n[2.{ri}] source={source}")
        print(f"    context {len(ctx)} 字符, {len(qs)} 问题")

        chunks = chunk_context(ctx, args.chunk)
        print(f"    切块: {len(chunks)} 段")

        tmp = tempfile.mkdtemp(prefix="mab_")
        db = os.path.join(tmp, "mab.db")
        st = MemoryStore(db)

        # 写入（按顺序，后面的会覆盖前面的 —— 正好测冲突消解）
        t0 = time.time()
        for i, ck in enumerate(chunks):
            st.add(ck, atmosphere="mab")
        n_ep = st.con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
        print(f"    写入 {n_ep} 条记忆, {time.time()-t0:.1f}s")

        # 逐问题检索，看 top-k 里有没有正确答案
        hit, total = 0, 0
        per_q = []
        for qi, q in enumerate(qs):
            gold = ans[qi] if qi < len(ans) else []
            if isinstance(gold, str):
                gold = [gold]
            if not gold:
                continue
            total += 1
            hits = st.search(q, top=args.top)
            texts = " ".join(getattr(h[0], "raw_text", "") or "" for h in hits).lower()
            ok = any(str(g).lower() in texts for g in gold if g)
            if ok:
                hit += 1
            per_q.append({"q": q[:60], "gold": gold[:2], "hit": ok})

        rate = hit / total if total else 0
        print(f"    检索命中: {hit}/{total} = {rate:.1%}")
        all_stats.append({
            "row": ri, "source": source,
            "n_chunks": len(chunks), "n_questions": total,
            "hit": hit, "rate": round(rate, 4),
        })
        st.con.close()
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 70)
    print("结果")
    print("=" * 70)
    print(f"\n  {'行':<4}{'source':<32}{'命中':>8}{'率':>8}")
    print("  " + "-" * 54)
    tot_h, tot_q = 0, 0
    for s in all_stats:
        print(f"  {s['row']:<4}{s['source'][:30]:<32}"
              f"{s['hit']:>4}/{s['n_questions']:<3}{s['rate']:>8.1%}")
        tot_h += s["hit"]
        tot_q += s["n_questions"]
    print("  " + "-" * 54)
    overall = tot_h / tot_q if tot_q else 0
    print(f"  {'合计':<36}{tot_h:>4}/{tot_q:<3}{overall:>8.1%}")

    out = {
        "experiment": "E-541",
        "benchmark": "MemoryAgentBench / Conflict_Resolution",
        "dataset": "ai-hyz/MemoryAgentBench",
        "top_k": args.top,
        "chunk_size": args.chunk,
        "total_questions": tot_q,
        "total_hit": tot_h,
        "hit_rate": round(overall, 4),
        "per_row": all_stats,
        "note": "自实现评测：把 context 切块灌入记忆库后用问题检索，"
                "看 top-k 是否含正确答案子串。不依赖 LLM judge。",
    }
    p = os.path.join(HERE, "results", "e541_mab_conflict.json")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n  写入 {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
