"""在真实 MTEB 任务上评测肥鱼之心（FISH-31）

════════════════════════════════════════════════════════════
为什么不用标准 mteb.evaluate
════════════════════════════════════════════════════════════
mteb 的检索流程是「encode(queries) → 算余弦相似度」，
无法插入我们自己的 search（FTS5 + 向量 + 加权）。

所以这里手动调度：
  1. 从 mteb 拉取任务数据（corpus / queries / qrels）
  2. 把 corpus 写进我们的库
  3. 对每个 query 调我们的 search
  4. 用标准 NDCG@10 公式算分

口径与 LMEB 一致（同样数据、同样指标），只是调度器换成我们的。

════════════════════════════════════════════════════════════
任务选择依据（已实测规模）
════════════════════════════════════════════════════════════
  LMEBMLDR      1636 samples / 100 queries   ← 可跑
  LMEB_SciFact  1936 samples / 188 queries   ← 可跑
  LoCoMo        31386 samples / 1964 queries ← 可跑（慢）
  LongMemEval   1426430 samples / 500 queries ← 太大，先跳过
  ConvoMem      506088 samples / 5853 queries ← 太大

用法：
    python run_e540_mteb_real.py --task LMEBMLDR
    python run_e540_mteb_real.py --task LMEBMLDR --limit 20
"""
import argparse
import json
import math
import os
import sys
import tempfile
import time
import warnings

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "cosmos_mem"))


def ndcg_at_k(ranked, relevant, k=10):
    dcg = sum(1.0 / math.log2(i + 2)
              for i, d in enumerate(ranked[:k]) if d in relevant)
    idcg = sum(1.0 / math.log2(i + 2)
               for i in range(min(len(relevant), k)))
    return dcg / idcg if idcg > 0 else 0.0


def recall_at_k(ranked, relevant, k=10):
    if not relevant:
        return 0.0
    return sum(1 for d in ranked[:k] if d in relevant) / len(relevant)


def load_task_data(task_name):
    """从 mteb 加载任务的 corpus / queries / qrels"""
    import mteb

    print(f"  加载任务 {task_name} ...")
    task = mteb.get_task(task_name)
    print(f"    type={task.metadata.type}")
    subsets = list(task.metadata.hf_subsets or ["default"])
    print(f"    subsets={subsets}")

    # ★ mteb 2.x：load_data() 返回 None（惰性加载），
    #   加载后从 task.dataset 读：
    #   task.dataset = {subset: {split: {'corpus':..., 'queries':...,
    #                                   'relevant_docs':..., 'top_ranked':...}}}
    task.load_data()
    data = task.dataset
    if not data:
        raise RuntimeError(f"{task_name} 数据加载失败（task.dataset 为空）")
    all_corpus, all_queries, all_qrels = {}, {}, {}
    for sub in data:
        for split in data[sub]:
            ds = data[sub][split]
            # ★ 实测字段名（mteb 2.27.18）：
            #   corpus:  {'id', 'text', 'title'}
            #   queries: {'id', 'text'}
            #   relevant_docs: 结构需按实际类型处理
            if "corpus" in ds:
                for row in ds["corpus"]:
                    all_corpus[row["id"]] = (row.get("text") or
                                             row.get("title") or "")
            if "queries" in ds:
                for row in ds["queries"]:
                    all_queries[row["id"]] = row.get("text") or ""
            if "relevant_docs" in ds:
                rd = ds["relevant_docs"]
                if isinstance(rd, dict):
                    # ★ 实测结构：{query_id: {corpus_id: relevance_score}}
                    #   例：{'51': {'51_0': 1}, '69': {'69_0': 1}}
                    # 第一版误当成 {qid: [cid,...]}，导致全部匹配失败、
                    # NDCG 恒为 0（浪费了一次 3 分钟评测）。
                    for qid, v in rd.items():
                        if isinstance(v, dict):
                            cids = list(v.keys())
                        elif isinstance(v, (list, tuple, set)):
                            cids = list(v)
                        else:
                            cids = [v]
                        all_qrels.setdefault(str(qid), set()).update(
                            str(x) for x in cids)
                else:
                    # 形如 [{'query-id':..., 'corpus-id':...}, ...]
                    for row in rd:
                        if isinstance(row, dict):
                            qid = row.get("query-id") or row.get("query_id")
                            cid = row.get("corpus-id") or row.get("corpus_id")
                            if qid is not None and cid is not None:
                                all_qrels.setdefault(str(qid), set()).add(
                                    str(cid))
    return all_corpus, all_queries, all_qrels


def run(task_name, limit=None):
    from store_v2 import MemoryStore

    print("=" * 70)
    print(f"E-540 真实 MTEB 任务评测：{task_name}")
    print("=" * 70)

    t0 = time.time()
    corpus, queries, qrels = load_task_data(task_name)
    print(f"\n  corpus {len(corpus)} 条, queries {len(queries)} 条, "
          f"qrels {len(qrels)} 组")

    if limit:
        qids = list(queries)[:limit]
    else:
        qids = [q for q in queries if q in qrels]
    print(f"  实际评测 query: {len(qids)} 条")

    # 写进临时库
    tmp = tempfile.mkdtemp(prefix="mteb_")
    db = os.path.join(tmp, "mteb.db")
    st = MemoryStore(db)

    # corpus id → ep_id 映射（用 dict 存在内存）
    print(f"\n  写入 corpus 到临时库（{len(corpus)} 条）...")
    tw = time.time()
    cid_to_text = {}
    text_to_ep = {}
    for i, (cid, text) in enumerate(corpus.items()):
        if not text:
            continue
        cid_to_text[cid] = text
        st.add(text, atmosphere="mteb")
        if (i + 1) % 500 == 0:
            print(f"    {i+1}/{len(corpus)} ...")
    for eid, txt in st.con.execute("SELECT ep_id, raw_text FROM episodes"):
        text_to_ep[txt] = eid
    print(f"  写入完成 {time.time()-tw:.1f}s, episodes={len(text_to_ep)}")

    # 逐 query 检索
    print(f"\n  检索评测 ...")
    ndcgs, recalls, misses = [], [], 0
    per_query = []
    tq = time.time()
    for i, qid in enumerate(qids):
        qtext = queries.get(qid, "")
        if not qtext:
            continue
        gold_cids = qrels.get(qid, set())
        gold_eps = {text_to_ep[cid_to_text[c]]
                    for c in gold_cids
                    if c in cid_to_text and cid_to_text[c] in text_to_ep}
        if not gold_eps:
            misses += 1
            continue

        hits = st.search(qtext, top=10)
        ranked = [h[0].ep_id for h in hits]
        n = ndcg_at_k(ranked, gold_eps, 10)
        r = recall_at_k(ranked, gold_eps, 10)
        ndcgs.append(n)
        recalls.append(r)
        per_query.append({"qid": qid, "ndcg10": round(n, 4),
                          "recall10": round(r, 4),
                          "n_gold": len(gold_eps)})
        if (i + 1) % 20 == 0:
            print(f"    {i+1}/{len(qids)}  mean_ndcg={sum(ndcgs)/len(ndcgs):.4f}")

    st.con.close()
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

    el = time.time() - t0
    mean_n = sum(ndcgs) / len(ndcgs) if ndcgs else 0
    mean_r = sum(recalls) / len(recalls) if recalls else 0

    print("\n" + "=" * 70)
    print("结果")
    print("=" * 70)
    print(f"\n  任务: {task_name}")
    print(f"  评测 query: {len(ndcgs)} 条（{misses} 条 gold 无法映射，跳过）")
    print(f"  NDCG@10:   {mean_n:.4f}")
    print(f"  Recall@10: {mean_r:.4f}")
    print(f"  耗时: {el:.1f}s")

    out = {
        "experiment": "E-540",
        "task": task_name,
        "n_corpus": len(corpus),
        "n_queries_evaluated": len(ndcgs),
        "n_skipped": misses,
        "mean_ndcg10": round(mean_n, 4),
        "mean_recall10": round(mean_r, 4),
        "elapsed": round(el, 1),
        "per_query": per_query[:200],   # 只存前 200 条
    }
    p = os.path.join(HERE, "results", f"e540_{task_name}.json")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n  写入 {p}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", default="LMEBMLDR")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    return run(args.task, args.limit)


if __name__ == "__main__":
    raise SystemExit(main())
