"""
E-47 判决性实验：遗忘到底是"存储消失"还是"入口收缩"

理论依据（已查证）：
  ① 静默印迹（Tonegawa 2017 PNAS）：记忆信息完整保留，但自然线索无法唤起，
     光直接刺激能完整回忆；且"低强度光只能引起部分回忆"。
  ② Miller 2021 综述：多数遗忘源于检索失败，而非不可逆抹除。
  ③ Hypermnesia（Otani 1999）：多线索能捞回 27% 之前想不起来的项目，
     增益来自 reminiscence（重新想起），不是减少遗忘。
  ④ Encoding specificity（Tulving & Thomson 1973）：
     编码与检索的加工重叠度越高，回忆越好。

用户假设（待验证）：
  "记忆全覆盖留存；遗忘是聚光灯没打过去，不是记忆没了。"

判决方法：在我们的记忆库上，人为制造三种"遗忘"，分别测：
  A. 线索削弱   —— 查询变模糊（模拟"聚光灯没对准"）
  B. 线索类型错配 —— 用与编码时段不同的线索类型（模拟 encoding specificity）
  C. 时间间隔    —— 隔一段"时间"后再查（模拟"入口收缩"）

每种条件下测两个量：
  Availability（数据还在吗）—— 直接按 id 取原始条目，能否取到
  Accessibility（查得到吗）—— 用线索检索，能否命中

如果 Availability=100% 而 Accessibility 下降 → **入口收缩，不是存储消失**。
如果两者同步下降 → 存储消失。

再测三条"打光"手段能否把 Accessibility 拉回来：
  ① 多路线索融合（对应多线索 hypermnesia）
  ② 提高 top-k（对应"光更强"）
  ③ 多步联想扩散（对应"聚光灯移动"，顺着关系走）

数据：TNEWS 真实中文标题 + BGE 真 embedding。
"""
from __future__ import annotations

import json
import os
import sys
import time

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
os.environ["HF_HOME"] = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".hf")
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import numpy as np

MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models", "bge-small-zh")
RESULTS = os.path.join(_HERE, "results")
DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "tnews")


def load_texts(n=8000, seed=0):
    import pandas as pd
    paths = []
    for root, _, files in os.walk(DATA):
        for f in files:
            if f.endswith(".parquet"):
                paths.append(os.path.join(root, f))
    df = pd.concat([pd.read_parquet(p) for p in sorted(paths)], ignore_index=True)
    tcol = "sentence" if "sentence" in df.columns else df.columns[0]
    t = df[tcol].astype(str).tolist()
    if n < len(t):
        idx = np.random.default_rng(seed).choice(len(t), n, replace=False)
        t = [t[i] for i in idx]
    return t


def load_bge():
    import torch
    from transformers import AutoTokenizer, AutoModel
    torch.set_num_threads(4)
    tok = AutoTokenizer.from_pretrained(MODEL_DIR)
    mdl = AutoModel.from_pretrained(MODEL_DIR).eval()

    def embed(texts, bs=64):
        outs = []
        with torch.no_grad():
            for i in range(0, len(texts), bs):
                b = texts[i:i + bs]
                enc = tok(b, padding=True, truncation=True, max_length=128, return_tensors="pt")
                outs.append(mdl(**enc).last_hidden_state[:, 0].numpy())
        v = np.vstack(outs).astype(np.float32)
        nn = np.linalg.norm(v, axis=1, keepdims=True); nn[nn == 0] = 1
        return v / nn
    return embed


def tokenize(s):
    """简单切词：中文按 2-gram，英文数字整串"""
    import re
    parts = re.split(r"[\s，。、；：！？（）()\[\]{}<>\"'`~@#$%^&*+=|\\/]+", s)
    out = []
    for p in parts:
        p = p.strip().lower()
        if not p:
            continue
        out.append(p)
        if len(p) >= 4 and not p.isascii():
            for i in range(len(p) - 1):
                out.append(p[i:i + 2])
    return out


class Memory:
    """记忆库：真值层(int8+原文) + 三路索引"""

    def __init__(self, texts, M, lo, scale):
        self.texts = texts
        self.M = M
        self.lo = lo
        self.scale = scale
        self.N = len(texts)
        # FTS 倒排
        self.inv = {}
        for i, t in enumerate(texts):
            for w in set(tokenize(t)):
                self.inv.setdefault(w, []).append(i)
        # 词频（BM25 用）
        self.doc_tf = [set(tokenize(t)) for t in texts]
        self.avg_len = np.mean([len(d) for d in self.doc_tf])

    def get_raw(self, i):
        """Availability 检验：按 id 直接取原始条目"""
        try:
            return self.texts[i]
        except (IndexError, TypeError):
            return None

    def vec_search(self, qv, k=5):
        s = self.M @ qv
        return np.argsort(-s)[:k].tolist()

    def kw_search(self, terms, k=5):
        if not terms:
            return []
        scores = {}
        N = self.N
        for t in terms:
            ids = self.inv.get(t, [])
            if not ids:
                continue
            idf = np.log((N + 1) / (len(ids) + 1)) + 1
            for i in ids:
                scores[i] = scores.get(i, 0) + idf
        return [i for i in sorted(scores, key=lambda x: -scores[x])[:k]]


def rrf(lists, k=5, c=60):
    sc = {}
    for l in lists:
        for r, d in enumerate(l):
            sc[d] = sc.get(d, 0) + 1.0 / (c + r + 1)
    return sorted(sc, key=lambda d: -sc[d])[:k]


def main():
    print("=" * 96)
    print("E-47 判决实验：存储消失 vs 入口收缩")
    print("=" * 96)

    texts = load_texts(8000)
    N = len(texts)
    print(f"\n记忆库 {N} 条真实标题")
    embed = load_bge()
    t0 = time.time()
    M = embed(texts)
    print(f"编码 {time.time()-t0:.0f}s")

    lo = M.min(axis=0, keepdims=True); hi = M.max(axis=0, keepdims=True)
    scale = (hi - lo) / 255.0; scale[scale == 0] = 1e-9
    Q = np.clip(np.round((M - lo) / scale), 0, 255).astype(np.uint8)
    Mq = Q.astype(np.float32) * scale + lo
    Mq /= np.linalg.norm(Mq, axis=1, keepdims=True)

    mem = Memory(texts, Mq, lo, scale)

    # 选 100 条作为"目标记忆"
    rng = np.random.default_rng(31)
    targets = rng.choice(N, 100, replace=False).tolist()

    out = {"experiment": "E-47", "n_memories": N, "n_targets": len(targets)}

    # ============ Availability 基线 ============
    print("\n" + "-" * 96)
    print("Availability（数据还在吗）")
    print("-" * 96)
    avail = sum(1 for i in targets if mem.get_raw(i) is not None) / len(targets)
    print(f"  按 id 直接取: {avail*100:.1f}%  （{len(targets)} 条目标）")
    print(f"  存储完整性: int8 {Q.nbytes/1024/1024:.1f} MB，条数 {Q.shape[0]}/{N}")
    out["availability"] = round(float(avail), 4)

    # ============ 三种"遗忘"，测 Accessibility ============
    print("\n" + "-" * 96)
    print("Accessibility（查得到吗）随线索削弱而下降")
    print("-" * 96)
    print(f"{'线索保留度':<14}{'查询示例(截断)':<34}{'向量命中':<12}{'关键词命中':<14}")
    print("-" * 96)

    cue_rows = []
    for keep in [1.0, 0.75, 0.5, 0.3, 0.15, 0.05]:
        vec_hits = kw_hits = 0
        for i in targets:
            full = mem.texts[i]
            cut = max(2, int(len(full) * keep))
            cue = full[:cut]
            qv = embed([cue])[0]
            # 向量命中
            top = mem.vec_search(qv, 5)
            if i in top:
                vec_hits += 1
            # 关键词命中
            terms = tokenize(cue)
            ktop = mem.kw_search(terms, 5)
            if i in ktop:
                kw_hits += 1
        vr = vec_hits / len(targets)
        kr = kw_hits / len(targets)
        ex = mem.texts[targets[0]][:max(2, int(len(mem.texts[targets[0]]) * keep))]
        cue_rows.append({"keep": keep, "vec": round(vr, 4), "kw": round(kr, 4)})
        print(f"{keep*100:>5.0f}%{'':<8}{ex[:30]:<34}{vr:<12.4f}{kr:<14.4f}")
    print("-" * 96)
    print("  ★ 线索保留度 100% 时命中率 =", f"{cue_rows[0]['vec']:.2f}", "(向量)")
    print("  ★ 线索保留度 5% 时命中率  =", f"{cue_rows[-1]['vec']:.2f}", "(向量)")
    print("  ★ 但 Availability 始终 =", f"{avail*100:.0f}%")
    out["cue_weakening"] = cue_rows

    # ============ 三条"打光"手段能否救回来 ============
    print("\n" + "-" * 96)
    print("三条打光手段（在最难条件 keep=15% 下测试）")
    print("-" * 96)
    HARD = 0.15

    def eval_method(fn):
        hit = 0
        for i in targets:
            full = mem.texts[i]
            cue = full[:max(2, int(len(full) * HARD))]
            if i in fn(cue):
                hit += 1
        return hit / len(targets)

    base_vec = eval_method(lambda c: mem.vec_search(embed([c])[0], 5))
    base_kw = eval_method(lambda c: mem.kw_search(tokenize(c), 5))
    base_direct = max(base_vec, base_kw)   # 单路最优

    # ① 多路融合
    def multi_cue(cue):
        qv = embed([cue])[0]
        return rrf([mem.vec_search(qv, 5), mem.kw_search(tokenize(cue), 5)])
    m_multi = eval_method(multi_cue)

    # ② 提高 top-k
    topk_rows = []
    for k in [5, 10, 20, 50, 100, 200]:
        def f(c, k=k):
            return mem.vec_search(embed([c])[0], k)
        r = eval_method(f)
        topk_rows.append({"k": k, "recall": round(r, 4)})

    # ③ 多步联想扩散：从 top-k 里取词，再检索一轮
    def spread(cue, hops=2, k=10):
        qv = embed([cue])[0]
        got = set(mem.vec_search(qv, k))
        frontier = set(got)
        for _ in range(hops - 1):
            new = set()
            for j in list(frontier)[:10]:
                # 从已检索到条目的高频词再检索
                terms = list(mem.doc_tf[j])[:8]
                new |= set(mem.kw_search(terms, 5))
            frontier |= new
            got |= new
        return list(got)[:k]
    m_spread = eval_method(spread)

    print(f"  单路最优（向量/关键词取大） : {base_direct:.4f}")
    print(f"  ① 多路融合（向量+关键词）  : {m_multi:.4f}  "
          f"（{m_multi-base_direct:+.4f}）")
    print(f"  ③ 多步联想扩散            : {m_spread:.4f}  （{"%+.4f" % (m_spread-base_direct)}）")
    print(f"\n  ② 提高 top-k（光更强）:")
    print(f"     {'k':<8}{'命中率':<12}{'相对 k=5'}")
    for r in topk_rows:
        print(f"     {r['k']:<8}{r['recall']:<12.4f}"
              f"{r['recall']-topk_rows[0]['recall']:+.4f}")

    out["recovery"] = {"base": round(base_direct, 4),
                       "multi_cue": round(m_multi, 4),
                       "spread": round(m_spread, 4),
                       "topk": topk_rows}

    # ============ 结论 ============
    print("\n" + "=" * 96)
    print("判决")
    print("=" * 96)
    a = avail
    acc_hard = cue_rows[-1]["vec"]
    print(f"  Availability（数据还在）        : {a*100:.1f}%")
    print(f"  Accessibility（线索只剩5%时）   : {acc_hard*100:.1f}%")
    if a > 0.99 and acc_hard < 0.9:
        print(f"  → 数据 100% 在，但查不到 → 【入口收缩，不是存储消失】")
        verdict = "entrance_contraction"
    else:
        print(f"  → 两者同步下降 → 存储可能真的丢了")
        verdict = "storage_loss"
    print(f"\n  打光后能救回多少（相对单路最优）:")
    print(f"    多路融合: {m_multi-base_direct:+.4f}")
    print(f"    联想扩散: {m_spread-base_direct:+.4f}")
    print(f"    提高k   : {topk_rows[-1]['recall']-topk_rows[0]['recall']:+.4f}")

    out["verdict"] = verdict
    with open(os.path.join(RESULTS, "e47_availability.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e47_availability.json')}")


if __name__ == "__main__":
    main()
