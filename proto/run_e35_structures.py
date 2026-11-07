"""
E-35 存储/检索结构对比：扁平 vs 关键词 vs 图遍历

背景：之前我只有"扁平向量 + 类内 top3"一种结构，从没测过图。
文献里 Zep/Graphiti 用图（三层：episode/semantic entity/community），
但那是"别人的方案"，我没在我们的数据上验证过。

本实验用**受控合成记忆库**，隔离三种检索各自的能力边界：

记忆库长这样（模拟真实个人记忆）：
    (主体, 关系, 客体)  + 一句自然语言描述 + 一个话题向量

三种查询类型（对应真实使用场景）：
    Q1 单跳事实   "张三的项目是什么"        → 一条记忆里就有答案
    Q2 关系跳转   "张三所在项目的负责人是谁"  → 需要 A→项目→负责人 两跳
    Q3 话题模糊   "那个做记忆系统的"          → 没有精确词，只能靠语义

四种检索器：
    V   扁平向量（话题向量余弦，取 top-k）
    K   关键词（BM25 风格：实体名精确匹配）
    G1  图 1 跳
    G2  图 2 跳
    H   V+K+G2 混合（RRF 融合）

指标：Recall@k（正确答案是否在返回的 k 条里）

不预设门槛，只记录：**每种结构在哪类查询上赢、在哪类上输。**
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import numpy as np

RESULTS = os.path.join(_HERE, "results")


# ---------------- 合成记忆库 ----------------
def build_corpus(n_people=40, n_projects=25, seed=0):
    rng = np.random.default_rng(seed)
    people = [f"人{i:03d}" for i in range(n_people)]
    projects = [f"项目{i:03d}" for i in range(n_projects)]
    topics = ["记忆系统", "检索算法", "硬件设计", "产品运营", "数据分析"]

    # 话题向量（5 维，可解释）
    topic_vec = np.eye(len(topics), dtype=np.float32)

    mems = []       # 每条记忆
    edges = defaultdict(list)   # 图邻接表

    # 每个人有项目
    for p in people:
        proj = projects[rng.integers(0, n_projects)]
        topic = rng.integers(0, len(topics))
        t = topics[topic]
        mems.append({
            "id": len(mems),
            "subj": p, "rel": "负责", "obj": proj,
            "text": f"{p} 负责 {proj}",
            "vec": topic_vec[topic] + rng.normal(0, 0.08, len(topics)).astype(np.float32),
        })
        edges[(p, "负责")].append(proj)
        edges[(proj, "负责人")].append(p)

    # 每个项目属于某个部门 / 有个负责人
    for proj in projects:
        dept = f"部门{rng.integers(0, 5)}"
        owner = people[rng.integers(0, n_people)]
        topic = rng.integers(0, len(topics))
        mems.append({
            "id": len(mems),
            "subj": proj, "rel": "负责人", "obj": owner,
            "text": f"{proj} 的负责人是 {owner}",
            "vec": topic_vec[topic] + rng.normal(0, 0.08, len(topics)).astype(np.float32),
        })
        edges[(proj, "负责人")].append(owner)
        edges[(proj, "属于")].append(dept)
        edges[(dept, "包含")].append(proj)

        mems.append({
            "id": len(mems),
            "subj": proj, "rel": "属于", "obj": dept,
            "text": f"{proj} 属于 {dept}",
            "vec": topic_vec[rng.integers(0, len(topics))] + rng.normal(0, 0.08, len(topics)).astype(np.float32),
        })

    # 一些话题性描述（无精确实体，用于 Q3）
    for i in range(30):
        topic = topics[rng.integers(0, len(topics))]
        mems.append({
            "id": len(mems),
            "subj": "-", "rel": "话题", "obj": topic,
            "text": f"这是一段关于{topic}的讨论记录，提到了具体做法 {i}",
            "vec": topic_vec[topics.index(topic)] + rng.normal(0, 0.05, len(topics)).astype(np.float32),
        })

    for m in mems:
        v = m["vec"]
        m["vec"] = v / max(np.linalg.norm(v), 1e-9)
    return mems, edges, people, projects, topics, topic_vec


# ---------------- 检索器 ----------------
def ret_v(mems, qvec, k=5):
    M = np.array([m["vec"] for m in mems], np.float32)
    s = M @ qvec
    return list(np.argsort(-s)[:k])


def ret_k(mems, terms, k=5):
    """极简 BM25：按词命中数 + 稀有度加权"""
    N = len(mems)
    df = defaultdict(int)
    docs = []
    for m in mems:
        toks = set(m["text"].split())
        docs.append(toks)
        for t in toks:
            df[t] += 1
    scores = []
    for d in docs:
        sc = 0.0
        for t in terms:
            if t in d:
                idf = np.log((N - df[t] + 0.5) / (df[t] + 0.5) + 1.0)
                sc += idf
        scores.append(sc)
    order = np.argsort(-np.array(scores))
    return [i for i in order[:k] if scores[i] > 0]


def ret_graph(mems, edges, start_entity, hops=1, k=5):
    """
    图遍历：从 start_entity 出发，走 hops 跳，收集经过的关系边，
    返回的是"包含这些关系的记忆 id"。
    """
    frontier = {start_entity}
    collected_rels = set()
    visited = {start_entity}
    for _ in range(hops):
        nxt = set()
        for e in frontier:
            for (s, r), objs in edges.items():
                if s == e:
                    collected_rels.add((s, r, tuple(objs)))
                    for o in objs:
                        if o not in visited:
                            nxt.add(o)
        visited |= nxt
        frontier = nxt
    # 记忆 id：subject 落在 collected 的 (s,r,o) 里
    ids = []
    for m in mems:
        for (s, r, objs) in collected_rels:
            if m["subj"] == s and m["rel"] == r and m["obj"] in objs:
                ids.append(m["id"])
                break
    return ids[:k]


def ret_hybrid(lists, k=5, k_rrf=60):
    """RRF 融合多个有序列表"""
    score = defaultdict(float)
    for lst in lists:
        for rank, doc in enumerate(lst):
            score[doc] += 1.0 / (k_rrf + rank + 1)
    order = sorted(score, key=lambda d: -score[d])
    return order[:k]


# ---------------- 查询生成 ----------------
def make_queries(mems, edges, people, projects, topics, topic_vec, n_each=40, seed=1):
    rng = np.random.default_rng(seed)
    qs = []

    # Q1 单跳事实："X 负责什么项目"
    for _ in range(n_each):
        p = people[rng.integers(0, len(people))]
        gold = [m["id"] for m in mems if m["subj"] == p and m["rel"] == "负责"]
        if gold:
            qs.append({"kind": "Q1_单跳", "terms": [p, "负责"],
                       "qvec": np.array([mems[gold[0]]["vec"]], np.float32)[0],
                       "start": p, "gold": set(gold)})

    # Q2 关系跳转："X 所在项目的负责人是谁" → 两跳
    for _ in range(n_each):
        p = people[rng.integers(0, len(people))]
        projs = edges.get((p, "负责"), [])
        if not projs:
            continue
        proj = projs[0]
        owners = edges.get((proj, "负责人"), [])
        if not owners:
            continue
        owner = owners[0]
        gold = {m["id"] for m in mems if m["subj"] == proj and m["rel"] == "负责人"}
        gold |= {m["id"] for m in mems if m["subj"] == p and m["rel"] == "负责"}
        qs.append({"kind": "Q2_两跳", "terms": [proj, "负责人"],
                   "qvec": None, "start": p, "gold": gold})

    # Q3 话题模糊：无精确词
    for _ in range(n_each):
        ti = rng.integers(0, len(topics))
        t = topics[ti]
        gold = {m["id"] for m in mems if m["rel"] == "话题" and m["obj"] == t}
        if not gold:
            continue
        qs.append({"kind": "Q3_话题", "terms": [],     # 故意不给精确词
                   "qvec": topic_vec[ti], "start": None, "gold": gold})
    return qs


def recall_at(ids, gold, k=5):
    if not gold:
        return None
    return len(set(ids[:k]) & gold) / len(gold)


def main():
    mems, edges, people, projects, topics, topic_vec = build_corpus()
    qs = make_queries(mems, edges, people, projects, topics, topic_vec)
    print("=" * 92)
    print("E-35 存储/检索结构对比（受控合成记忆库）")
    print("=" * 92)
    print(f"记忆条数 {len(mems)}  图边 {sum(len(v) for v in edges.values())}  "
          f"查询 {len(qs)}")

    kinds = ["Q1_单跳", "Q2_两跳", "Q3_话题"]
    methods = ["V_向量", "K_关键词", "G1_图1跳", "G2_图2跳", "H_混合"]
    res = {m: {k: [] for k in kinds} for m in methods}

    for q in qs:
        gold = q["gold"]
        k = 5
        # V
        if q["qvec"] is not None:
            v_ids = ret_v(mems, q["qvec"], k)
        else:
            v_ids = []
        # K
        k_ids = ret_k(mems, q["terms"], k) if q["terms"] else []
        # G1 / G2
        if q["start"]:
            g1 = ret_graph(mems, edges, q["start"], hops=1, k=k)
            g2 = ret_graph(mems, edges, q["start"], hops=2, k=k)
        else:
            g1, g2 = [], []
        # H
        h = ret_hybrid([x for x in (v_ids, k_ids, g2) if x], k)

        for name, ids in [("V_向量", v_ids), ("K_关键词", k_ids),
                          ("G1_图1跳", g1), ("G2_图2跳", g2), ("H_混合", h)]:
            r = recall_at(ids, gold, k)
            if r is not None:
                res[name][q["kind"]].append(r)

    print("\n" + "-" * 92)
    print(f"{'检索方式':<14}" + "".join(f"{k:<16}" for k in kinds))
    print("-" * 92)
    table = {}
    for m in methods:
        row = {}
        for k in kinds:
            v = res[m][k]
            row[k] = round(float(np.mean(v)), 4) if v else None
        table[m] = row
        print(f"{m:<14}" + "".join(
            (f"{row[k]:<16.4f}" if row[k] is not None else f"{'—':<16}") for k in kinds))
    print("-" * 92)

    print("\n各查询类型的最优：")
    for k in kinds:
        cand = [(m, table[m][k]) for m in methods if table[m][k] is not None]
        if cand:
            best = max(cand, key=lambda x: x[1])
            print(f"  {k:<10} 最优 = {best[0]:<12} Recall@5 = {best[1]:.4f}")

    out = {"experiment": "E-35", "n_mems": len(mems), "n_queries": len(qs),
           "kinds": kinds, "table": table,
           "note": "受控合成；V=话题向量，K=BM25风格，G=图遍历，H=RRF融合"}
    with open(os.path.join(RESULTS, "e35_storage_structures.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e35_storage_structures.json')}")


if __name__ == "__main__":
    main()
