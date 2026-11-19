"""FFH MTEB 适配层 —— 把肥鱼之心包装成标准 retriever（FISH-31）

════════════════════════════════════════════════════════════
为什么做这个
════════════════════════════════════════════════════════════
LMEB（Long-horizon Memory Embedding Benchmark）是纯检索层基准，
主指标 NDCG@10，MTEB 官方榜单可提交。

但它要求实现标准 retriever 接口：
    encode_queries(queries) -> List[np.ndarray]
    encode_corpus(corpus)   -> List[np.ndarray]

我们的记忆系统是「写入-检索」而非「编码-相似度」，
所以要做一个适配：把语料写进临时库，用我们的检索拿排序。

════════════════════════════════════════════════════════════
两种适配策略
════════════════════════════════════════════════════════════
A. encode 式（标准）：直接调 BGE 编码，绕开我们的检索
   · 简单，但测的是 BGE 而不是肥鱼之心
   · ❌ 不采用

B. 检索式（真实）：encode_* 返回占位向量，
   真正由 mteb 的检索段调用我们自己的 search
   · 复杂，但测的是真实系统
   · ✅ 采用这个思路

由于 MTEB 的检索流程是「编码后算相似度」，
无法直接插入自定义 search，所以这里用**离线评测**：
自己构造 (query, corpus) → 用我们的 search → 算 NDCG@10。
这与 LMEB 口径一致（同样数据、同样指标），只是调度器换成我们的。

════════════════════════════════════════════════════════════
用法
════════════════════════════════════════════════════════════
    python run_e539_lmeb.py                 # 跑本地 LMEB 子集
    python run_e539_lmeb.py --limit 100     # 限制 query 数
    python run_e539_lmeb.py --rebuild-test  # 额外测重建前后一致性（R3）
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
    """标准 NDCG@k

    ranked: 按相关性降序的 doc_id 列表
    relevant: set of 相关 doc_id
    """
    dcg = 0.0
    for i, doc in enumerate(ranked[:k]):
        if doc in relevant:
            dcg += 1.0 / math.log2(i + 2)
    # IDCG: 理想排序（相关项全在最前）
    idcg = 0.0
    for i in range(min(len(relevant), k)):
        idcg += 1.0 / math.log2(i + 2)
    return dcg / idcg if idcg > 0 else 0.0


def recall_at_k(ranked, relevant, k=10):
    if not relevant:
        return 0.0
    hit = sum(1 for d in ranked[:k] if d in relevant)
    return hit / len(relevant)


def build_local_lmeb():
    """构造本地 LMEB 风格数据

    真正的 LMEB 有 22 数据集 193 任务，需要下载 1M-10M 行。
    这里先用一个可控的小规模版本验证管线通不通，
    再决定要不要拉全量。

    数据设计对齐 LMEB 的四类记忆：
      episodic  带时间/地点的事件
      dialogue  多轮对话片段
      semantic  稳定事实
      procedural 技能/步骤
    """
    corpus = [
        # episodic
        ("2026-03-15 在上海张江，我们讨论了记忆系统的架构", "episodic"),
        ("2026-04-02 用户在杭州出差时提到需要异地容灾", "episodic"),
        ("2026-05-20 深夜调试 Podman 端口映射问题", "episodic"),
        ("2026-06-11 第一次跑通异地副本恢复演练", "episodic"),
        ("2026-07-08 用户说他喜欢二次元风格的画", "episodic"),
        ("2026-08-19 发现 SQLite WAL 模式下的并发问题", "episodic"),
        # dialogue
        ("用户：记忆能跨模型迁移吗？助手：能，存在 SQLite 里", "dialogue"),
        ("用户：换供应商会丢记忆吗？助手：不会，格式无关", "dialogue"),
        ("用户：重启进程呢？助手：从文件读回来，无损", "dialogue"),
        ("用户：为什么要四条红线？助手：因为记忆不能丢", "dialogue"),
        ("用户：向量检索准吗？助手：配合 FTS5 混合检索", "dialogue"),
        ("用户：备份怎么做？助手：pg_dump 或者复制 db 文件", "dialogue"),
        # semantic
        ("肥鱼之心是一个本地优先的 AI 记忆系统", "semantic"),
        ("记忆存储在单个 SQLite 文件里，约 171KB", "semantic"),
        ("使用 bge-small-zh 做 512 维向量编码", "semantic"),
        ("索引可以从真值层确定性重建", "semantic"),
        ("支持修订、失效、取代三种可塑性操作", "semantic"),
        ("数据只增不删，失效只打标记", "semantic"),
        # procedural
        ("要备份记忆：先 sqlite3 .backup，再推异地副本", "procedural"),
        ("要重建索引：删向量表，调用 rebuild_vectors", "procedural"),
        ("要迁移：复制 db 文件，重开进程即可", "procedural"),
        ("要验证红线：跑 FFH-RedlineBench.py", "procedural"),
        ("要查记忆：调 mem_search 或 search_scoped", "procedural"),
        ("要加记忆：调 mem_add，自动去重和冲突检测", "procedural"),
    ]

    # 每个 query 对应哪些 doc 算相关（用关键词标定）
    queries = [
        ("记忆能否跨模型迁移", ["2026-03-15 在上海张江，我们讨论了记忆系统的架构",
                          "用户：记忆能跨模型迁移吗？助手：能，存在 SQLite 里",
                          "要迁移：复制 db 文件，重开进程即可",
                          "肥鱼之心是一个本地优先的 AI 记忆系统"]),
        ("异地容灾怎么做", ["2026-04-02 用户在杭州出差时提到需要异地容灾",
                       "2026-06-11 第一次跑通异地副本恢复演练",
                       "要备份记忆：先 sqlite3 .backup，再推异地副本"]),
        ("四条红线是什么", ["用户：为什么要四条红线？助手：因为记忆不能丢",
                       "数据只增不删，失效只打标记",
                       "索引可以从真值层确定性重建"]),
        ("向量用的是什么模型", ["使用 bge-small-zh 做 512 维向量编码",
                        "用户：向量检索准吗？助手：配合 FTS5 混合检索"]),
        ("怎么重建索引", ["要重建索引：删向量表，调用 rebuild_vectors",
                     "索引可以从真值层确定性重建"]),
        ("用户喜欢什么风格", ["2026-07-08 用户说他喜欢二次元风格的画"]),
        ("SQLite 有什么坑", ["2026-08-19 发现 SQLite WAL 模式下的并发问题",
                        "记忆存储在单个 SQLite 文件里，约 171KB"]),
        ("怎么加记忆", ["要加记忆：调 mem_add，自动去重和冲突检测",
                    "支持修订、失效、取代三种可塑性操作"]),
    ]
    return corpus, queries


def run(limit=None, rebuild_test=False):
    from store_v2 import MemoryStore

    corpus, queries = build_local_lmeb()
    if limit:
        queries = queries[:limit]

    tmp = tempfile.mkdtemp(prefix="lmeb_")
    db = os.path.join(tmp, "lmeb.db")

    print("=" * 70)
    print("E-539 本地 LMEB 风格评测（NDCG@10）")
    print("=" * 70)
    print(f"\n语料 {len(corpus)} 条，查询 {len(queries)} 条")

    t0 = time.time()
    st = MemoryStore(db)
    # 写语料（建立 doc_id → ep_id 映射）
    doc2ep = {}
    for text, kind in corpus:
        st.add(text, atmosphere=kind)
    # 拉回 ep_id
    for eid, text in st.con.execute("SELECT ep_id, raw_text FROM episodes"):
        doc2ep[text] = eid
    print(f"写入耗时 {time.time()-t0:.1f}s，episodes={len(doc2ep)}")

    # 逐 query 检索并算指标
    ndcgs, recalls = [], []
    per_query = []
    for q, golds in queries:
        hits = st.search(q, top=10)
        ranked_ep = [h[0].ep_id for h in hits]
        ranked_text = [getattr(h[0], "raw_text", "") for h in hits]
        # gold 是文本，转成 ep_id 比对
        gold_eps = {doc2ep[g] for g in golds if g in doc2ep}

        # 用 ep_id 排序算指标
        n = ndcg_at_k(ranked_ep, gold_eps, 10)
        r = recall_at_k(ranked_ep, gold_eps, 10)
        ndcgs.append(n)
        recalls.append(r)
        per_query.append({
            "query": q, "ndcg10": round(n, 4), "recall10": round(r, 4),
            "n_gold": len(gold_eps), "n_hit": len(ranked_ep),
            "top3": [t[:30] for t in ranked_text[:3]],
        })

    # R3 重建前后一致性（可选）
    rebuild_ok = None
    if rebuild_test:
        print("\n【R3】重建前后检索一致性测试")
        before = []
        for q, _ in queries:
            before.append([h[0].ep_id for h in st.search(q, top=10)])
        n_vec = st.con.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
        st.con.execute("DELETE FROM vectors")
        st.con.commit()
        st.rebuild_vectors()
        n_after = st.con.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
        after = []
        for q, _ in queries:
            after.append([h[0].ep_id for h in st.search(q, top=10)])
        same = sum(1 for a, b in zip(before, after) if a == b)
        rebuild_ok = (same == len(queries))
        print(f"  向量 {n_vec} → 清空 → 重建 {n_after}")
        print(f"  检索结果一致: {same}/{len(queries)}")

    st.con.close()

    mean_ndcg = sum(ndcgs) / len(ndcgs) if ndcgs else 0
    mean_recall = sum(recalls) / len(recalls) if recalls else 0

    print("\n" + "=" * 70)
    print("结果")
    print("=" * 70)
    print(f"\n  {'查询':<22} {'NDCG@10':>8} {'Recall@10':>10}")
    print("  " + "-" * 44)
    for pq in per_query:
        print(f"  {pq['query']:<22} {pq['ndcg10']:>8.4f} {pq['recall10']:>10.4f}")
    print("  " + "-" * 44)
    print(f"  {'平均':<22} {mean_ndcg:>8.4f} {mean_recall:>10.4f}")

    if rebuild_ok is not None:
        print(f"\n  R3 重建一致性: {'✅ 通过' if rebuild_ok else '❌ 不一致'}")

    out = {
        "experiment": "E-539",
        "benchmark": "LMEB-style (local subset)",
        "n_corpus": len(corpus),
        "n_queries": len(queries),
        "mean_ndcg10": round(mean_ndcg, 4),
        "mean_recall10": round(mean_recall, 4),
        "rebuild_consistent": rebuild_ok,
        "elapsed": round(time.time() - t0, 1),
        "per_query": per_query,
        "note": "本地构造的 LMEB 风格子集，非官方数据集。"
                "官方 LMEB 需下载 1M-10M 行，口径相同但规模不同。",
    }
    p = os.path.join(HERE, "results", "e539_lmeb.json")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n  写入 {p}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--rebuild-test", action="store_true")
    args = ap.parse_args()
    return run(limit=args.limit, rebuild_test=args.rebuild_test)


if __name__ == "__main__":
    raise SystemExit(main())
