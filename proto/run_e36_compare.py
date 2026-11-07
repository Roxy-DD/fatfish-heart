"""
E-36 单用 / 组合 / 不组合 —— 系统对比

回答用户的三个问题：
  1. 各分布（查询类型）上，最佳的是哪个？
  2. 怎么组合？
  3. 不组合（单用一个）会不会更好？

修正 E-35 的泄漏：
  多跳查询只给「起点实体」，不给答案旁边的词。

五种查询类型（覆盖真实记忆检索）：
  Q1 单跳事实    "人003 负责什么项目"         ← 精确实体
  Q2 两跳关系    "人003 所在项目的负责人是谁"   ← 只给起点
  Q3 话题模糊    "关于记忆系统的讨论"          ← 无精确词
  Q4 精确标识    "任务 T-4417 是什么"          ← ID/编号
  Q5 改写同义    "谁管着那个项目"              ← 与文本用词不同

四个基础检索器：
  V 向量   K 全文   G1 图1跳   G2 图2跳

组合方式：
  RRF      倒数排名融合
  WSUM     归一化加权求和
  VOTE     投票
  CASCADE  级联：先 K，置信度不足再 V，仍不足再 G2
  ROUTER   规则路由：按查询特征选一路

以及：ORACLE 路由（作弊上限，仅供定位差距）
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict

os.environ.setdefault("KMP_DIFFERENT", "1")
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import numpy as np

RESULTS = os.path.join(_HERE, "results")
K = 5


# ================= 语料 =================
def build(seed=0):
    rng = np.random.default_rng(seed)
    topics = ["记忆系统", "检索算法", "硬件设计", "产品运营", "数据分析"]
    T = np.eye(len(topics), dtype=np.float32)

    people = [f"人{i:03d}" for i in range(30)]
    projs = [f"项目{i:03d}" for i in range(20)]
    tasks = [f"T-{4400+i}" for i in range(20)]
    depts = [f"部门{i}" for i in range(5)]

    mems, edges = [], defaultdict(list)

    def add(subj, rel, obj, text, vec):
        i = len(mems)
        v = vec / max(np.linalg.norm(vec), 1e-9)
        mems.append({"id": i, "subj": subj, "rel": rel, "obj": obj,
                     "text": text, "vec": v})
        edges[(subj, rel)].append(obj)
        return i

    # 人 → 负责 → 项目
    for p in people:
        pr = projs[rng.integers(0, len(projs))]
        add(p, "负责", pr, f"{p} 负责 {pr}", T[rng.integers(0, 5)] + rng.normal(0, .08, 5))
    # 项目 → 负责人 → 人
    for pr in projs:
        ow = people[rng.integers(0, len(people))]
        add(pr, "负责人", ow, f"{pr} 的负责人是 {ow}", T[rng.integers(0, 5)] + rng.normal(0, .08, 5))
        add(pr, "属于", depts[rng.integers(0, 5)], f"{pr} 属于部门", T[rng.integers(0, 5)] + rng.normal(0, .08, 5))
    # 任务 → 编号（Q4 用）
    for tk in tasks:
        ow = people[rng.integers(0, len(people))]
        add(tk, "执行人", ow, f"任务 {tk} 由 {ow} 执行", T[rng.integers(0, 5)] + rng.normal(0, .08, 5))
    # 话题描述（Q3 用，无精确实体）
    for i in range(40):
        ti = rng.integers(0, 5)
        add("-", "话题", topics[ti],
            f"这一段是在讨论{topics[ti]}，其中提到了第{i}种做法",
            T[ti] + rng.normal(0, .05, 5))
    # 改写同义（Q5 用）：用不同词描述同一件事
    for p in people[:15]:
        pr = edges.get((p, "负责"), [])
        if pr:
            add(p, "经办", pr[0], f"{p} 手上管着 {pr[0]}",
                T[rng.integers(0, 5)] + rng.normal(0, .08, 5))

    return mems, edges, people, projs, tasks, topics, T


# ================= 检索器 =================
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
        if qvec is None:
            return []
        s = self.M @ qvec
        return list(np.argsort(-s)[:k])

    def k(self, terms, k=K):
        if not terms:
            return []
        sc = np.zeros(self.N)
        for i, d in enumerate(self.docs):
            for t in terms:
                if t in d:
                    sc[i] += np.log((self.N - self.df[t] + .5) / (self.df[t] + .5) + 1)
        order = np.argsort(-sc)
        return [int(i) for i in order[:k] if sc[i] > 0]

    def g(self, edges, start, hops=1, k=K):
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


# ================= 查询 =================
def queries(mems, edges, people, projs, tasks, topics, T, n=30, seed=2):
    rng = np.random.default_rng(seed)
    qs = []

    for _ in range(n):   # Q1 单跳
        p = people[rng.integers(0, len(people))]
        gold = {m["id"] for m in mems if m["subj"] == p and m["rel"] == "负责"}
        if gold:
            qs.append({"kind": "Q1_单跳", "terms": [p, "负责"], "qvec": None,
                       "start": p, "gold": gold})
    for _ in range(n):   # Q2 两跳：只给起点人名
        p = people[rng.integers(0, len(people))]
        pr = edges.get((p, "负责"), [])
        if not pr:
            continue
        pr = pr[0]
        gold = {m["id"] for m in mems if m["subj"] == pr and m["rel"] == "负责人"}
        gold |= {m["id"] for m in mems if m["subj"] == p and m["rel"] == "负责"}
        if gold:
            qs.append({"kind": "Q2_两跳", "terms": [p], "qvec": None,
                       "start": p, "gold": gold})
    for _ in range(n):   # Q3 话题：无精确词
        ti = rng.integers(0, 5)
        gold = {m["id"] for m in mems if m["rel"] == "话题" and m["obj"] == topics[ti]}
        if gold:
            qs.append({"kind": "Q3_话题", "terms": [], "qvec": T[ti],
                       "start": None, "gold": gold})
    for _ in range(n):   # Q4 精确标识
        tk = tasks[rng.integers(0, len(tasks))]
        gold = {m["id"] for m in mems if m["subj"] == tk}
        if gold:
            qs.append({"kind": "Q4_标识", "terms": [tk], "qvec": None,
                       "start": tk, "gold": gold})
    for _ in range(n):   # Q5 改写同义：用"管着"，文本是"负责"
        p = people[rng.integers(0, len(people))]
        gold = {m["id"] for m in mems if m["subj"] == p and m["rel"] == "经办"}
        if gold:
            qs.append({"kind": "Q5_改写", "terms": [p, "管着"], "qvec": None,
                       "start": p, "gold": gold})
    return qs


# ================= 组合 =================
def rrf(lists, k=K, c=60):
    sc = defaultdict(float)
    for l in lists:
        for r, d in enumerate(l):
            sc[d] += 1.0 / (c + r + 1)
    return sorted(sc, key=lambda d: -sc[d])[:k]


def wsum(pairs, k=K):
    """pairs: [(ids, weight)]，用排名归一化后加权"""
    sc = defaultdict(float)
    for ids, w in pairs:
        n = max(len(ids), 1)
        for r, d in enumerate(ids):
            sc[d] += w * (1.0 - r / n)
    return sorted(sc, key=lambda d: -sc[d])[:k]


def rec(ids, gold, k=K):
    return len(set(ids[:k]) & gold) / len(gold) if gold else None


def main():
    mems, edges, people, projs, tasks, topics, T = build()
    ix = Idx(mems)
    qs = queries(mems, edges, people, projs, tasks, topics, T)
    kinds = ["Q1_单跳", "Q2_两跳", "Q3_话题", "Q4_标识", "Q5_改写"]
    print("=" * 104)
    print("E-36 单用 / 组合 / 不组合")
    print("=" * 104)
    print(f"记忆 {len(mems)} 条，查询 {len(qs)} 条，k={K}")

    methods = ["V", "K", "G1", "G2",
               "RRF", "WSUM", "VOTE", "CASCADE", "ROUTER", "ORACLE"]
    res = {m: {kk: [] for kk in kinds} for m in methods}

    for q in qs:
        v = ix.v(q["qvec"]); kk = ix.k(q["terms"])
        g1 = ix.g(edges, q["start"], 1); g2 = ix.g(edges, q["start"], 2)

        # 级联：K 若命中足够就停，否则加 V，再否则加 G2
        casc = list(kk)
        if len(casc) < 2:
            casc = rrf([kk, v])
        if len(casc) < 2 and q["start"]:
            casc = rrf([casc, g2])

        # 规则路由
        if q["terms"] and any("T-" in t for t in q["terms"]):
            route = kk                      # 有编号 → 全文
        elif q["start"] and not q["qvec"]:
            route = g2 if len(g2) >= 2 else rrf([g2, kk])   # 有实体、无向量 → 图
        elif q["qvec"] is not None and not q["terms"]:
            route = v                       # 无词、有向量 → 向量
        else:
            route = rrf([v, kk])            # 都有 → 融合

        # oracle：按类型直接选最优（作弊上限）
        orc = {"Q1_单跳": kk, "Q2_两跳": g2, "Q3_话题": v,
               "Q4_标识": kk, "Q5_改写": rrf([v, kk])}[q["kind"]]

        out = {"V": v, "K": kk, "G1": g1, "G2": g2,
               "RRF": rrf([v, kk, g2]),
               "WSUM": wsum([(v, .4), (kk, .35), (g2, .25)]),
               "VOTE": rrf([v, kk, g1, g2]),
               "CASCADE": casc, "ROUTER": route, "ORACLE": orc}
        for m, ids in out.items():
            r = rec(ids, q["gold"])
            if r is not None:
                res[m][q["kind"]].append(r)

    # 表格
    print("\n" + "-" * 104)
    print(f"{'方法':<10}" + "".join(f"{k:<12}" for k in kinds) + f"{'均值':<10}{'最差':<10}")
    print("-" * 104)
    table = {}
    for m in methods:
        row, allv = {}, []
        for k in kinds:
            x = res[m][k]
            row[k] = round(float(np.mean(x)), 4) if x else None
            if x:
                allv.extend(x)
        row["mean"] = round(float(np.mean(allv)), 4) if allv else None
        row["worst"] = round(float(min(row[k] for k in kinds if row[k] is not None)), 4)
        table[m] = row
        print(f"{m:<10}" + "".join(
            (f"{row[k]:<12.4f}" if row[k] is not None else f"{'—':<12}") for k in kinds)
            + f"{row['mean']:<10.4f}{row['worst']:<10.4f}")
    print("-" * 104)

    # 逐类型最优
    print("\n① 各分布上的最佳（不含 ORACLE）:")
    for k in kinds:
        cand = [(m, table[m][k]) for m in methods
                if m != "ORACLE" and table[m][k] is not None]
        best = max(cand, key=lambda x: x[1])
        print(f"   {k:<10} 最佳={best[0]:<10} {best[1]:.4f}")

    print("\n② 组合 vs 最好的单用:")
    singles = {m: table[m]["mean"] for m in ["V", "K", "G1", "G2"]}
    best_single = max(singles, key=singles.get)
    combos = {m: table[m]["mean"] for m in ["RRF", "WSUM", "VOTE", "CASCADE", "ROUTER"]}
    best_combo = max(combos, key=combos.get)
    print(f"   最好单用 = {best_single} ({singles[best_single]:.4f})")
    print(f"   最好组合 = {best_combo} ({combos[best_combo]:.4f})")
    print(f"   差值 = {combos[best_combo]-singles[best_single]:+.4f}")

    print("\n③ 不组合是否更好（按最差项比）:")
    for m in ["V", "K", "G1", "G2"]:
        print(f"   {m:<6} 均值={table[m]['mean']:.4f}  最差={table[m]['worst']:.4f}")
    for m in ["RRF", "WSUM", "VOTE", "CASCADE", "ROUTER"]:
        print(f"   {m:<6} 均值={table[m]['mean']:.4f}  最差={table[m]['worst']:.4f}")

    out = {"experiment": "E-36", "n_mems": len(mems), "n_q": len(qs), "k": K,
           "kinds": kinds, "table": table, "best_single": best_single,
           "best_combo": best_combo}
    with open(os.path.join(RESULTS, "e36_single_vs_combo.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e36_single_vs_combo.json')}")


if __name__ == "__main__":
    main()
