"""
E-39 真 embedding（bge-small-zh-v1.5）补齐「话题模糊」这一类

E-38 的问题：TF-IDF 是词袋，我手写的近义改写与原文零词重合 → Q3 = 0.000
这是 TF-IDF 的能力边界，不是"向量检索不行"。

本实验用真实中文 embedding 重测同一套五类查询，对比三种"语义"表示：
  TFIDF   （词袋，E-38 基线）
  BGE     （bge-small-zh-v1.5，512 维，真语义）
  BGE+TF  （拼接，真语义 + 精确词）

同时保留 K / G2 / ROUTER 作为对照，直接看：
  ① Q3 能到多少
  ② 组合是否还需要（如果 BGE 单路就能覆盖全部，就不需要组合）
  ③ 每种查询的最佳
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
os.environ["HF_HOME"] = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".hf")
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import numpy as np

MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models", "bge-small-zh")
RESULTS = os.path.join(_HERE, "results")
K = 5
TOPICS = ["记忆系统", "检索算法", "硬件设计", "产品运营", "数据分析"]


# ---------- 语料（与 E-38 完全一致） ----------
def build(seed=0):
    rng = np.random.default_rng(seed)
    people = [f"人{i:03d}" for i in range(30)]
    projs = [f"项目{i:03d}" for i in range(20)]
    tasks = [f"T-{4400+i}" for i in range(20)]
    depts = [f"部门{i}" for i in range(5)]
    mems, edges = [], defaultdict(list)

    def add(subj, rel, obj, text):
        mems.append({"id": len(mems), "subj": subj, "rel": rel, "obj": obj, "text": text})
        edges[(subj, rel)].append(obj)

    for p in people:
        add(p, "负责", projs[rng.integers(0, len(projs))], f"{p} 负责 {projs[rng.integers(0, len(projs))]}")
    # 上句有随机不一致，重写严谨版
    mems.clear(); edges.clear()
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


def make_q(mems, edges, people, tasks, n=30, seed=2):
    rng = np.random.default_rng(seed)
    qs = []
    ALT = {"记忆系统": "记得东西存下来",
           "检索算法": "怎么找到想要的内容",
           "硬件设计": "电路和芯片",
           "产品运营": "用户和增长",
           "数据分析": "从数字里看出规律"}
    for _ in range(n):
        p = people[rng.integers(0, len(people))]
        g = {m["id"] for m in mems if m["subj"] == p and m["rel"] == "负责"}
        if g:
            qs.append({"kind": "Q1_单跳", "text": p, "terms": [p], "start": p, "gold": g})
    for _ in range(n):
        p = people[rng.integers(0, len(people))]
        pr = edges.get((p, "负责"), [])
        if not pr:
            continue
        pr = pr[0]
        g = {m["id"] for m in mems if m["subj"] == pr and m["rel"] == "负责人"}
        g |= {m["id"] for m in mems if m["subj"] == p and m["rel"] == "负责"}
        if g:
            qs.append({"kind": "Q2_两跳", "text": p, "terms": [p], "start": p, "gold": g})
    for _ in range(n):
        ti = int(rng.integers(0, 5))
        t = TOPICS[ti]
        g = {m["id"] for m in mems if m["rel"] == "话题" and m["obj"] == t}
        if g:
            qs.append({"kind": "Q3_话题", "text": ALT[t], "terms": [], "start": None, "gold": g})
    for _ in range(n):
        tk = tasks[rng.integers(0, len(tasks))]
        g = {m["id"] for m in mems if m["subj"] == tk}
        if g:
            qs.append({"kind": "Q4_标识", "text": tk, "terms": [tk], "start": tk, "gold": g})
    for _ in range(n):
        p = people[rng.integers(0, len(people))]
        g = {m["id"] for m in mems if m["subj"] == p and m["rel"] == "经办"}
        if g:
            qs.append({"kind": "Q5_改写", "text": p, "terms": [p], "start": p, "gold": g})
    return qs


def l2(a):
    n = np.linalg.norm(a, axis=-1, keepdims=True)
    n[n == 0] = 1
    return a / n


def load_bge():
    import torch
    from transformers import AutoTokenizer, AutoModel
    tok = AutoTokenizer.from_pretrained(MODEL_DIR)
    mdl = AutoModel.from_pretrained(MODEL_DIR)
    mdl.eval()

    def embed(texts, bs=32):
        out = []
        with torch.no_grad():
            for i in range(0, len(texts), bs):
                batch = texts[i:i + bs]
                enc = tok(batch, padding=True, truncation=True, max_length=128, return_tensors="pt")
                o = mdl(**enc)
                # CLS pooling（bge 用 [CLS]）
                v = o.last_hidden_state[:, 0]
                out.append(v.cpu().numpy())
        v = np.vstack(out).astype(np.float32)
        return l2(v)
    return embed


def main():
    mems, edges, people, projs, tasks = build()
    qs = make_q(mems, edges, people, tasks)
    kinds = ["Q1_单跳", "Q2_两跳", "Q3_话题", "Q4_标识", "Q5_改写"]
    texts = [m["text"] for m in mems]
    print("=" * 100)
    print("E-39 真 embedding（bge-small-zh-v1.5）补齐话题模糊")
    print("=" * 100)
    print(f"记忆 {len(mems)} 条  查询 {len(qs)} 条  k={K}")

    print("\n加载 BGE 模型...")
    embed = load_bge()
    print("  编码记忆...")
    M_bge = embed(texts)
    print(f"  BGE 维度 {M_bge.shape[1]}")
    print("  编码查询...")
    qvec_bge = embed([q["text"] for q in qs])
    print("  完成")

    class Idx:
        def __init__(self, mems, M):
            self.mems, self.M = mems, M
            self.docs = [set(m["text"].split()) for m in mems]
            self.df = defaultdict(int)
            for d in self.docs:
                for t in d:
                    self.df[t] += 1
            self.N = len(mems)

        def v(self, qv, k=K):
            if qv is None:
                return []
            s = self.M @ qv
            return list(np.argsort(-s)[:k])

        def kk(self, terms, k=K):
            if not terms:
                return []
            sc = np.zeros(self.N)
            for i, d in enumerate(self.docs):
                for t in terms:
                    if t in d:
                        sc[i] += np.log((self.N - self.df[t] + .5) / (self.df[t] + .5) + 1)
            o = np.argsort(-sc)
            return [int(i) for i in o[:k] if sc[i] > 0]

        def g(self, edges, start, hops, k=K):
            if not start:
                return []
            fr, vis, rel = {start}, {start}, set()
            for _ in range(hops):
                nx = set()
                for e in fr:
                    for (s, r), objs in edges.items():
                        if s == e:
                            rel.add((s, r, tuple(objs)))
                            nx |= {o for o in objs if o not in vis}
                vis |= nx; fr = nx
            out = []
            for m in self.mems:
                for (s, r, objs) in rel:
                    if m["subj"] == s and m["rel"] == r and m["obj"] in objs:
                        out.append(m["id"]); break
            return out[:k]

    def rrf(lists, k=K, c=60):
        sc = defaultdict(float)
        for l in lists:
            for r, d in enumerate(l):
                sc[d] += 1.0 / (c + r + 1)
        return sorted(sc, key=lambda d: -sc[d])[:k]

    def rec(ids, gold):
        return len(set(ids[:K]) & gold) / len(gold) if gold else None

    ix = Idx(mems, M_bge)
    res = {m: {kk: [] for kk in kinds}
           for m in ["V_bge", "K", "G2", "V+K", "V+K+G2", "ROUTER"]}

    for qi, q in enumerate(qs):
        qv = qvec_bge[qi]
        v = ix.v(qv)
        kk = ix.kk(q["terms"])
        g2 = ix.g(edges, q["start"], 2)
        route = {"Q1_单跳": kk if kk else v,
                 "Q2_两跳": g2 if g2 else kk,
                 "Q3_话题": v,
                 "Q4_标识": kk if kk else v,
                 "Q5_改写": rrf([v, kk])}[q["kind"]]
        out = {"V_bge": v, "K": kk, "G2": g2,
               "V+K": rrf([v, kk]),
               "V+K+G2": rrf([v, kk, g2]),
               "ROUTER": route}
        for m, ids in out.items():
            r = rec(ids, q["gold"])
            if r is not None:
                res[m][q["kind"]].append(r)

    print("\n" + "-" * 100)
    print(f"{'方法':<10}" + "".join(f"{x:<11}" for x in kinds) + f"{'均值':<9}{'最差':<9}")
    print("-" * 100)
    table = {}
    for m in ["V_bge", "K", "G2", "V+K", "V+K+G2", "ROUTER"]:
        row = {kk: (round(float(np.mean(res[m][kk])), 4) if res[m][kk] else None)
               for kk in kinds}
        vals = [x for kk in kinds for x in res[m][kk]]
        mean = round(float(np.mean(vals)), 4) if vals else 0
        nz = [row[kk] for kk in kinds if row[kk] is not None]
        worst = round(float(min(nz)), 4) if nz else 0
        table[m] = {**row, "mean": mean, "worst": worst}
        print(f"{m:<10}" + "".join(
            (f"{row[kk]:<11.4f}" if row[kk] is not None else f"{'—':<11}")
            for kk in kinds) + f"{mean:<9.4f}{worst:<9.4f}")
    print("-" * 100)

    print("\n① 与 TF-IDF（E-38）对比，看 Q3 是否被补齐：")
    print(f"   {'表示':<12}{'Q3_话题':<12}{'V 均值':<12}")
    print("-" * 50)
    print(f"   {'TFIDF':<12}{0.0000:<12.4f}{0.6679:<12.4f}")
    print(f"   {'BGE':<12}{table['V_bge']['Q3_话题']:<12.4f}{table['V_bge']['mean']:<12.4f}")

    print("\n② 组合是否还需要（BGE 单路 vs 组合）：")
    print(f"   V_bge 单路    均值={table['V_bge']['mean']:.4f}  最差={table['V_bge']['worst']:.4f}")
    print(f"   ROUTER 组合   均值={table['ROUTER']['mean']:.4f}  最差={table['ROUTER']['worst']:.4f}")
    print(f"   差值          {table['ROUTER']['mean']-table['V_bge']['mean']:+.4f}")

    print("\n③ 各类型最佳：")
    for kk in kinds:
        cand = [(m, table[m][kk]) for m in table if table[m][kk] is not None]
        b = max(cand, key=lambda x: x[1])
        print(f"   {kk:<10} {b[0]:<10} {b[1]:.4f}")

    out = {"experiment": "E-39", "model": "BAAI/bge-small-zh-v1.5",
           "n_mems": len(mems), "n_q": len(qs), "dim": int(M_bge.shape[1]),
           "k": K, "kinds": kinds, "table": table,
           "tfidf_baseline": {"Q3": 0.0, "V_mean": 0.6679}}
    with open(os.path.join(RESULTS, "e39_bge.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e39_bge.json')}")


if __name__ == "__main__":
    main()

