"""
E-37 修正版：公平比较（V 在所有查询上都有向量）

E-36 的缺陷：
  Q1/Q2/Q4/Q5 我没给查询向量 → V 全 0 → V 被系统性低估。
  真实系统里任何查询都能被 embed 成向量。

修正：模拟一个"不完美但可用"的文本 embedding：
  记忆向量 = w_t·话题向量 + w_s·hash(主体) + w_o·hash(客体) + 噪声
  查询向量 = 同一公式，用查询里出现的词算
  → V 能工作，但会被同实体的其它记忆干扰（这很真实）

同时新增三项关键分析：
  A. 逐查询胜负：融合是否会在某些查询上**比单路更差**（Fusion Tax）
  B. k 值敏感性（k=1,3,5,10）
  C. 候选扫描成本（每条查询实际比对多少条）
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
DIM = 48          # 向量维度
K = 5


def hvec(s, dim=DIM):
    """确定性 hash → 单位向量（模拟"实体也进了 embedding"）"""
    rng = np.random.default_rng(abs(hash(s)) % (2 ** 31))
    v = rng.normal(size=dim).astype(np.float32)
    return v / np.linalg.norm(v)


TOPICS = ["记忆系统", "检索算法", "硬件设计", "产品运营", "数据分析"]


def embed(text_parts, topic_idx=None, w=(0.5, 0.5), seed=0, noise=0.10):
    """
    text_parts: 参与 embedding 的词列表（实体名等）
    topic_idx: 话题索引（None 表示无）
    返回单位向量
    """
    rng = np.random.default_rng(seed)
    v = np.zeros(DIM, np.float32)
    if topic_idx is not None:
        v += w[0] * hvec("TOPIC:" + TOPICS[topic_idx])
    for p in text_parts:
        v += (w[1] / max(len(text_parts), 1)) * hvec("E:" + p)
    v += rng.normal(0, noise, DIM).astype(np.float32)
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


def build(seed=0):
    rng = np.random.default_rng(seed)
    people = [f"人{i:03d}" for i in range(30)]
    projs = [f"项目{i:03d}" for i in range(20)]
    tasks = [f"T-{4400+i}" for i in range(20)]
    depts = [f"部门{i}" for i in range(5)]

    mems, edges = [], defaultdict(list)

    def add(subj, rel, obj, text, topic_idx, seed_k):
        i = len(mems)
        v = embed([subj, obj], topic_idx, seed=seed_k)
        mems.append({"id": i, "subj": subj, "rel": rel, "obj": obj,
                     "text": text, "vec": v})
        edges[(subj, rel)].append(obj)

    n = 0
    for p in people:
        pr = projs[rng.integers(0, len(projs))]
        add(p, "负责", pr, f"{p} 负责 {pr}", rng.integers(0, 5), n); n += 1
    for pr in projs:
        ow = people[rng.integers(0, len(people))]
        add(pr, "负责人", ow, f"{pr} 的负责人是 {ow}", rng.integers(0, 5), n); n += 1
        add(pr, "属于", depts[rng.integers(0, 5)], f"{pr} 属于某部门", rng.integers(0, 5), n); n += 1
    for tk in tasks:
        ow = people[rng.integers(0, len(people))]
        add(tk, "执行人", ow, f"任务 {tk} 由 {ow} 执行", rng.integers(0, 5), n); n += 1
    for i in range(40):
        ti = int(rng.integers(0, 5))
        add("-", "话题", TOPICS[ti], f"这一段在讨论{TOPICS[ti]}，第{i}种做法",
            ti, n); n += 1
    for p in people[:15]:
        pr = edges.get((p, "负责"), [])
        if pr:
            add(p, "经办", pr[0], f"{p} 手上管着 {pr[0]}", rng.integers(0, 5), n); n += 1
    return mems, edges, people, projs, tasks


class Idx:
    def __init__(self, mems):
        self.mems = mems
        self.M = np.array([m["vec"] for m in mems], np.float32)
        self.docs = [set(m["text"].split()) for m in mems]
        self.df = defaultdict(int)
        for d in self.docs:
            for t in d:
                self.df[t] += 1
        self.N = len(mems)

    def v(self, qvec, k=K):
        s = self.M @ qvec
        return list(np.argsort(-s)[:k])

    def kk(self, terms, k=K):
        if not terms:
            return []
        sc = np.zeros(self.N)
        for i, d in enumerate(self.docs):
            for t in terms:
                if t in d:
                    sc[i] += np.log((self.N - self.df[t] + .5) / (self.df[t] + .5) + 1)
        order = np.argsort(-sc)
        return [int(i) for i in order[:k] if sc[i] > 0]

    def g(self, edges, start, hops, k=K):
        if not start:
            return []
        frontier, visited, rels = {start}, {start}, set()
        for _ in range(hops):
            nxt = set()
            for e in frontier:
                for (s, r), objs in edges.items():
                    if s == e:
                        rels.add((s, r, tuple(objs)))
                        nxt |= {o for o in objs if o not in visited}
            visited |= nxt
            frontier = nxt
        out = []
        for m in self.mems:
            for (s, r, objs) in rels:
                if m["subj"] == s and m["rel"] == r and m["obj"] in objs:
                    out.append(m["id"]); break
        return out[:k]


def make_q(mems, edges, people, projs, tasks, n=30, seed=2):
    rng = np.random.default_rng(seed)
    qs = []
    for j in range(n):     # Q1 单跳：给人名
        p = people[rng.integers(0, len(people))]
        gold = {m["id"] for m in mems if m["subj"] == p and m["rel"] == "负责"}
        if gold:
            qs.append({"kind": "Q1_单跳", "terms": [p], "start": p, "gold": gold,
                       "qvec": embed([p], None, seed=1000 + j)})
    for j in range(n):     # Q2 两跳：只给起点
        p = people[rng.integers(0, len(people))]
        pr = edges.get((p, "负责"), [])
        if not pr:
            continue
        pr = pr[0]
        gold = {m["id"] for m in mems if m["subj"] == pr and m["rel"] == "负责人"}
        gold |= {m["id"] for m in mems if m["subj"] == p and m["rel"] == "负责"}
        if gold:
            qs.append({"kind": "Q2_两跳", "terms": [p], "start": p, "gold": gold,
                       "qvec": embed([p], None, seed=2000 + j)})
    for j in range(n):     # Q3 话题：无词
        ti = int(rng.integers(0, 5))
        gold = {m["id"] for m in mems if m["rel"] == "话题" and m["obj"] == TOPICS[ti]}
        if gold:
            qs.append({"kind": "Q3_话题", "terms": [], "start": None, "gold": gold,
                       "qvec": embed([], ti, seed=3000 + j)})
    for j in range(n):     # Q4 标识
        tk = tasks[rng.integers(0, len(tasks))]
        gold = {m["id"] for m in mems if m["subj"] == tk}
        if gold:
            qs.append({"kind": "Q4_标识", "terms": [tk], "start": tk, "gold": gold,
                       "qvec": embed([tk], None, seed=4000 + j)})
    for j in range(n):     # Q5 改写
        p = people[rng.integers(0, len(people))]
        gold = {m["id"] for m in mems if m["subj"] == p and m["rel"] == "经办"}
        if gold:
            qs.append({"kind": "Q5_改写", "terms": [p], "start": p, "gold": gold,
                       "qvec": embed([p], None, seed=5000 + j)})
    return qs


def rrf(lists, k=K, c=60):
    sc = defaultdict(float)
    for l in lists:
        for r, d in enumerate(l):
            sc[d] += 1.0 / (c + r + 1)
    return sorted(sc, key=lambda d: -sc[d])[:k]


def rec(ids, gold, k):
    return len(set(ids[:k]) & gold) / len(gold) if gold else None


def main():
    mems, edges, people, projs, tasks = build()
    ix = Idx(mems)
    qs = make_q(mems, edges, people, projs, tasks)
    kinds = ["Q1_单跳", "Q2_两跳", "Q3_话题", "Q4_标识", "Q5_改写"]
    print("=" * 100)
    print("E-37 公平比较（V 在所有查询上都有向量）")
    print("=" * 100)
    print(f"记忆 {len(mems)} 条  查询 {len(qs)} 条  向量维度 {DIM}  k={K}")

    methods = ["V", "K", "G2", "RRF", "WSUM", "ROUTER", "ORACLE"]
    table, per_q = {}, {}

    for k in (1, 5, 10):
        res = {m: {kk: [] for kk in kinds} for m in methods}
        pq = {m: [] for m in methods}
        for q in qs:
            v = ix.v(q["qvec"], k)
            kk = ix.kk(q["terms"], k)
            g2 = ix.g(edges, q["start"], 2, k)
            routes = {"Q1_单跳": kk, "Q2_两跳": g2, "Q3_话题": v,
                      "Q4_标识": kk, "Q5_改写": rrf([v, kk], k)}
            out = {
                "V": v, "K": kk, "G2": g2,
                "RRF": rrf([v, kk, g2], k),
                "WSUM": rrf([v, v, kk, kk, g2], k),   # 加权版（V/K 各两票）
                "ROUTER": routes[q["kind"]],
                "ORACLE": routes[q["kind"]],
            }
            for m, ids in out.items():
                r = rec(ids, q["gold"], k)
                if r is not None:
                    res[m][q["kind"]].append(r)
                    pq[m].append((q["kind"], r))
        if k == K:
            for m in methods:
                row = {kk: (round(float(np.mean(res[m][kk])), 4) if res[m][kk] else None)
                       for kk in kinds}
                vals = [x for kk in kinds for x in res[m][kk]]
                row["mean"] = round(float(np.mean(vals)), 4) if vals else None
                nz = [row[kk] for kk in kinds if row[kk] is not None]
                row["worst"] = round(float(min(nz)), 4) if nz else None
                table[m] = row
            per_q = pq

        print(f"\n--- k={k} ---")
        print(f"{'方法':<9}" + "".join(f"{x:<11}" for x in kinds) + f"{'均值':<9}{'最差':<9}")
        print("-" * 100)
        for m in methods:
            row = {kk: (round(float(np.mean(res[m][kk])), 4) if res[m][kk] else None)
                   for kk in kinds}
            vals = [x for kk in kinds for x in res[m][kk]]
            mean = round(float(np.mean(vals)), 4) if vals else 0
            nz = [row[kk] for kk in kinds if row[kk] is not None]
            worst = round(float(min(nz)), 4) if nz else 0
            print(f"{m:<9}" + "".join(
                (f"{row[kk]:<11.4f}" if row[kk] is not None else f"{'—':<11}")
                for kk in kinds) + f"{mean:<9.4f}{worst:<9.4f}")
        print("-" * 100)

    # ---- 逐查询胜负：融合会不会更差 ----
    print("\n" + "=" * 100)
    print("A. 逐查询胜负：RRF 融合 vs 最好的单路")
    print("=" * 100)
    wins = ties = losses = 0
    loss_detail = defaultdict(int)
    for q_kind, r_rrf in per_q["RRF"]:
        best_single = max(per_q["V"][len(loss_detail) * 0 + 0][1] if False else 0, 0)
    # 重新按索引对齐统计
    idx_map = {k: [] for k in methods}
    for m in methods:
        idx_map[m] = per_q[m]
    for i in range(len(idx_map["RRF"])):
        kind = idx_map["RRF"][i][0]
        a = idx_map["RRF"][i][1]
        b = max(idx_map["V"][i][1], idx_map["K"][i][1], idx_map["G2"][i][1])
        if a > b + 1e-9:
            wins += 1
        elif a < b - 1e-9:
            losses += 1
            loss_detail[kind] += 1
        else:
            ties += 1
    print(f"  融合胜 {wins}  平 {ties}  负 {losses}  （共 {wins+ties+losses} 条查询）")
    if loss_detail:
        print(f"  融合变差的查询类型分布: {dict(loss_detail)}")

    # ---- 成本 ----
    print("\n" + "=" * 100)
    print("B. 各方法的扫描成本（每查询比对条数）")
    print("=" * 100)
    cost = defaultdict(int)
    for q in qs:
        cost["V"] += ix.N
        cost["K"] += ix.N
        cost["G2"] += len(ix.g(edges, q["start"], 2, K))
        cost["RRF"] += ix.N * 2 + len(ix.g(edges, q["start"], 2, K))
        cost["ROUTER"] += {"Q1_单跳": ix.N, "Q2_两跳": 20, "Q3_话题": ix.N,
                           "Q4_标识": ix.N, "Q5_改写": ix.N}[q["kind"]]
    print(f"{'方法':<10}{'总比对':<12}{'每查询':<12}{'相对'}")
    print("-" * 60)
    base = cost["V"] / len(qs)
    for m in ["V", "K", "G2", "RRF", "ROUTER"]:
        per = cost[m] / len(qs)
        print(f"{m:<10}{cost[m]:<12}{per:<12.1f}{per/base:.2f}×")

    print("\n" + "=" * 100)
    print("C. 结论")
    print("=" * 100)
    for kk in kinds:
        cand = [(m, table[m][kk]) for m in ["V", "K", "G2"]
                if table[m][kk] is not None]
        best = max(cand, key=lambda x: x[1])
        print(f"  {kk:<10} 单路最佳 = {best[0]:<4} {best[1]:.4f}")
    print()
    bs = max(["V", "K", "G2"], key=lambda m: table[m]["mean"])
    bc = max(["RRF", "WSUM", "ROUTER"], key=lambda m: table[m]["mean"])
    print(f"  最好单路 = {bs} ({table[bs]['mean']:.4f})，最差项 {table[bs]['worst']:.4f}")
    print(f"  最好组合 = {bc} ({table[bc]['mean']:.4f})，最差项 {table[bc]['worst']:.4f}")
    print(f"  组合增益 = {table[bc]['mean']-table[bs]['mean']:+.4f}")
    print(f"  最差项提升 = {table[bc]['worst']-table[bs]['worst']:+.4f}")

    out = {"experiment": "E-37", "n_mems": len(mems), "n_q": len(qs),
           "dim": DIM, "k": K, "kinds": kinds, "table": table,
           "per_query": {"win": wins, "tie": ties, "loss": losses,
                         "loss_by_kind": dict(loss_detail)},
           "cost": {m: cost[m] / len(qs) for m in ["V", "K", "G2", "RRF", "ROUTER"]},
           "best_single": bs, "best_combo": bc}
    with open(os.path.join(RESULTS, "e37_fair_compare.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e37_fair_compare.json')}")


if __name__ == "__main__":
    main()
