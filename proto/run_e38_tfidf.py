"""
E-38 用 TF-IDF 替代 hash 假向量 —— 验证"低分是不是假向量造成的"

E-37 的问题：
  向量是 hash(实体名) 拼的，没有真实语义 → Q3 只有 0.219。

E-38 换两种真实的"语义"表示，各自都零下载、本机可跑：
  TFIDF   词袋 + IDF 权重（真正的语料统计，不是 hash）
  LSA     TF-IDF 再做 SVD 降到 32 维（近似"语义 embedding"）

然后重跑同一套五类查询 + 同样的组合策略，直接对比：
  hash-V  vs  TFIDF  vs  LSA

如果 TFIDF/LSA 的 Q3 明显高于 0.219，就证明"低分是假向量造成的"。
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
K = 5
TOPICS = ["记忆系统", "检索算法", "硬件设计", "产品运营", "数据分析"]


# ---------- 语料（与 E-37 同构） ----------
def build(seed=0):
    rng = np.random.default_rng(seed)
    people = [f"人{i:03d}" for i in range(30)]
    projs = [f"项目{i:03d}" for i in range(20)]
    tasks = [f"T-{4400+i}" for i in range(20)]
    depts = [f"部门{i}" for i in range(5)]
    mems, edges = [], defaultdict(list)

    def add(subj, rel, obj, text):
        i = len(mems)
        mems.append({"id": i, "subj": subj, "rel": rel, "obj": obj, "text": text})
        edges[(subj, rel)].append(obj)

    for p in people:
        pr = projs[rng.integers(0, len(projs))]
        add(p, "负责", pr, f"{p} 负责 {pr}")
    for pr in projs:
        ow = people[rng.integers(0, len(people))]
        add(pr, "负责人", ow, f"{pr} 的负责人是 {ow}")
        add(pr, "属于", depts[rng.integers(0, 5)], f"{pr} 属于某个部门")
    for tk in tasks:
        ow = people[rng.integers(0, len(people))]
        add(tk, "执行人", ow, f"任务 {tk} 由 {ow} 执行")
    for i in range(40):
        ti = int(rng.integers(0, 5))
        add("-", "话题", TOPICS[ti], f"这一段在讨论 {TOPICS[ti]} 的第 {i} 种做法")
    for p in people[:15]:
        pr = edges.get((p, "负责"), [])
        if pr:
            add(p, "经办", pr[0], f"{p} 手上管着 {pr[0]}")
    return mems, edges, people, projs, tasks


# ---------- 三种向量表示 ----------
def tok(s):
    """
    修正（第二版）：实体名必须保持完整。
    旧版按单字切中文，"人003" → ["人","003"]，而"003"同时出现在"项目003"里
    → 实体身份丢失 → K 被系统性压低。
    新版：按空白/标点切分，每个片段整体作为一个 token；
          中文长片段再补 2-gram 提高召回。
    """
    import re
    parts = re.split(r"[\s，。、；：！？（）()\[\]{}<>\"'`~@#$%^&*+=|\\/]+", s)
    out = []
    for p in parts:
        p = p.strip().lower()
        if not p:
            continue
        out.append(p)                      # 整体保留（人003、项目017、T-4417）
        # 纯中文长片段：补 2-gram
        if len(p) >= 4 and not p.isascii():
            for i in range(len(p) - 1):
                out.append(p[i:i + 2])
    return out


def tfidf_matrix(texts):
    docs = [tok(t) for t in texts]
    df = defaultdict(int)
    for d in docs:
        for w in set(d):
            df[w] += 1
    N = len(docs)
    vocab = {w: i for i, w in enumerate(sorted(df))}
    M = np.zeros((N, len(vocab)), np.float32)
    for i, d in enumerate(docs):
        for w in d:
            M[i, vocab[w]] += 1
        # TF 归一化
        nz = M[i] > 0
        if nz.any():
            M[i, nz] = 1 + np.log(M[i, nz])
    idf = np.array([np.log((N + 1) / (df[w] + 1)) + 1 for w in vocab], np.float32)
    M = M * idf
    # L2 归一化
    norms = np.linalg.norm(M, axis=1, keepdims=True)
    norms[norms == 0] = 1
    return M / norms, vocab, idf


def query_tfidf(qtext, vocab, idf):
    v = np.zeros(len(vocab), np.float32)
    for w in tok(qtext):
        if w in vocab:
            v[vocab[w]] += 1
    nz = v > 0
    if nz.any():
        v[nz] = 1 + np.log(v[nz])
    v = v * idf
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


def lsa_fit(M, dim=32):
    # 截断 SVD（用协方差特征分解，避免大矩阵 SVD）
    C = M.T @ M
    w, V = np.linalg.eigh(C)
    idx = np.argsort(-w)[:dim]
    V = V[:, idx]
    return V


# ---------- 检索器 ----------
class Idx:
    def __init__(self, mems, Mvec):
        self.mems = mems
        self.M = Mvec
        self.docs = [set(tok(m["text"])) for m in mems]
        self.df = defaultdict(int)
        for d in self.docs:
            for t in d:
                self.df[t] += 1
        self.N = len(mems)

    def v(self, qvec, k=K):
        if qvec is None or not np.any(qvec):
            return []
        s = self.M @ qvec
        return list(np.argsort(-s)[:k])

    def kk(self, terms, k=K):
        if not terms:
            return []
        # 修正：查询词也必须用同一套 tok() 切分，否则"人003"匹配不上单字文档
        q_tokens = set()
        for t in terms:
            q_tokens.update(tok(t))
        # 同时保留整串（英文/编号本身就是整串 token 的情况）
        q_tokens.update(t for t in (s.lower().strip() for s in terms) if t)
        if not q_tokens:
            return []
        sc = np.zeros(self.N)
        for i, d in enumerate(self.docs):
            for t in q_tokens:
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
    for j in range(n):
        p = people[rng.integers(0, len(people))]
        gold = {m["id"] for m in mems if m["subj"] == p and m["rel"] == "负责"}
        if gold:
            qs.append({"kind": "Q1_单跳", "text": p, "terms": [p],
                       "start": p, "gold": gold})
    for j in range(n):
        p = people[rng.integers(0, len(people))]
        pr = edges.get((p, "负责"), [])
        if not pr:
            continue
        pr = pr[0]
        gold = {m["id"] for m in mems if m["subj"] == pr and m["rel"] == "负责人"}
        gold |= {m["id"] for m in mems if m["subj"] == p and m["rel"] == "负责"}
        if gold:
            qs.append({"kind": "Q2_两跳", "text": p, "terms": [p],
                       "start": p, "gold": gold})
    for j in range(n):
        ti = int(rng.integers(0, 5))
        gold = {m["id"] for m in mems if m["rel"] == "话题" and m["obj"] == TOPICS[ti]}
        if gold:
            # 关键：查询用**不同措辞**表达同一话题，不给原文精确词
            alt = {"记忆系统": "记得东西存下来",
                   "检索算法": "怎么找到想要的内容",
                   "硬件设计": "电路和芯片",
                   "产品运营": "用户和增长",
                   "数据分析": "从数字里看出规律"}[TOPICS[ti]]
            qs.append({"kind": "Q3_话题", "text": alt, "terms": [],
                       "start": None, "gold": gold, "topic": TOPICS[ti]})
    for j in range(n):
        tk = tasks[rng.integers(0, len(tasks))]
        gold = {m["id"] for m in mems if m["subj"] == tk}
        if gold:
            qs.append({"kind": "Q4_标识", "text": tk, "terms": [tk],
                       "start": tk, "gold": gold})
    for j in range(n):
        p = people[rng.integers(0, len(people))]
        gold = {m["id"] for m in mems if m["subj"] == p and m["rel"] == "经办"}
        if gold:
            qs.append({"kind": "Q5_改写", "text": p, "terms": [p],
                       "start": p, "gold": gold})
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
    qs = make_q(mems, edges, people, projs, tasks)
    kinds = ["Q1_单跳", "Q2_两跳", "Q3_话题", "Q4_标识", "Q5_改写"]
    texts = [m["text"] for m in mems]

    print("=" * 100)
    print("E-38 TF-IDF / LSA 替代 hash 假向量")
    print("=" * 100)
    print(f"记忆 {len(mems)} 条  查询 {len(qs)} 条  k={K}")

    # 三种表示
    M_tfidf, vocab, idf = tfidf_matrix(texts)
    V_lsa = lsa_fit(M_tfidf, 32)
    M_lsa = M_tfidf @ V_lsa
    nl = np.linalg.norm(M_lsa, axis=1, keepdims=True); nl[nl == 0] = 1
    M_lsa = M_lsa / nl

    reps = {
        "TFIDF": (M_tfidf, lambda q: query_tfidf(q["text"], vocab, idf)),
        "LSA32": (M_lsa, lambda q: (lambda v: v / max(np.linalg.norm(v), 1e-9))(
            query_tfidf(q["text"], vocab, idf) @ V_lsa)),
    }

    print(f"  TF-IDF 维度 {M_tfidf.shape[1]}   LSA 维度 {M_lsa.shape[1]}")

    summary = {}
    for name, (Mrep, qfn) in reps.items():
        ix = Idx(mems, Mrep)
        res = {m: {kk: [] for kk in kinds} for m in ["V", "K", "G2", "ROUTER", "RRF"]}
        for q in qs:
            qv = qfn(q)
            v = ix.v(qv)
            kk = ix.kk(q["terms"])
            g2 = ix.g(edges, q["start"], 2)
            routes = {"Q1_单跳": kk if kk else v,
                      "Q2_两跳": g2 if g2 else kk,
                      "Q3_话题": v,
                      "Q4_标识": kk if kk else v,
                      "Q5_改写": rrf([v, kk])}
            out = {"V": v, "K": kk, "G2": g2,
                   "ROUTER": routes[q["kind"]],
                   "RRF": rrf([v, kk, g2])}
            for m, ids in out.items():
                r = rec(ids, q["gold"], K)
                if r is not None:
                    res[m][q["kind"]].append(r)

        print("\n" + "-" * 100)
        print(f"【{name}】")
        print(f"{'方法':<9}" + "".join(f"{x:<11}" for x in kinds) + f"{'均值':<9}")
        print("-" * 100)
        tb = {}
        for m in ["V", "K", "G2", "ROUTER", "RRF"]:
            row = {kk: (round(float(np.mean(res[m][kk])), 4) if res[m][kk] else None)
                   for kk in kinds}
            vals = [x for kk in kinds for x in res[m][kk]]
            mean = round(float(np.mean(vals)), 4) if vals else 0
            tb[m] = {**row, "mean": mean}
            print(f"{m:<9}" + "".join(
                (f"{row[kk]:<11.4f}" if row[kk] is not None else f"{'—':<11}")
                for kk in kinds) + f"{mean:<9.4f}")
        summary[name] = tb

    # 与 hash 版对比
    print("\n" + "=" * 100)
    print("对比：hash 假向量（E-37）vs TF-IDF vs LSA")
    print("=" * 100)
    hash_v_q3 = 0.2189      # E-37 实测
    hash_v_mean = 0.1535
    print(f"{'表示':<12}{'V 的 Q3':<12}{'V 均值':<12}{'ROUTER 均值':<14}")
    print("-" * 60)
    print(f"{'hash(假)':<12}{hash_v_q3:<12.4f}{hash_v_mean:<12.4f}{0.8177:<14.4f}")
    for name in summary:
        print(f"{name:<12}{summary[name]['V']['Q3_话题']:<12.4f}"
              f"{summary[name]['V']['mean']:<12.4f}"
              f"{summary[name]['ROUTER']['mean']:<14.4f}")

    out = {"experiment": "E-38", "n_mems": len(mems), "n_q": len(qs),
           "k": K, "kinds": kinds, "summary": summary,
           "hash_baseline": {"V_Q3": hash_v_q3, "V_mean": hash_v_mean,
                             "ROUTER_mean": 0.8177},
           "note": "TF-IDF/LSA 是真实语料统计；hash 版无真实语义"}
    with open(os.path.join(RESULTS, "e38_tfidf.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e38_tfidf.json')}")


if __name__ == "__main__":
    main()
