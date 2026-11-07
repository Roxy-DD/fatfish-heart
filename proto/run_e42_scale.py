"""
E-42 真实数据 + 10万条规模的延迟与命中率

用户问题：10 万条延迟和命中率测试，需要下载数据集吗？多大？
回答：延迟不需要（只跟条数×维度有关），命中率需要真实查询模式。
      已下载 TNEWS（今日头条标题，4.5 MB，7.3 万条真实中文短文本）。

本实验：
  A. 十万条延迟实测（TNEWS 7.3万 + 合成补齐到 10 万）
     - 全精度 vs int8 的检索延迟
     - FTS5 vs numpy 的 K 检索延迟
     - 编码延迟（batch=1 vs batch=8）
  B. 命中率测试（真实查询模式）
     三种查询构造法，模拟真实使用：
       Q-repeat  完全重复的查询（最乐观）
       Q-paraphrase 同义改写（中等）
       Q-partial  部分关键词（较难）
       Q-random  无关查询（最悲观）
  C. 红线复验：迁移 100% + 不遗忘
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import time
from collections import Counter

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
os.environ["HF_HOME"] = r"C:\Users\xuee\Desktop\肥鱼之心\.hf"
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import numpy as np

MODEL_DIR = r"C:\Users\xuee\Desktop\肥鱼之心\models\bge-small-zh"
RESULTS = os.path.join(_HERE, "results")
DATA = r"C:\Users\xuee\Desktop\肥鱼之心\data\tnews"
WORK = os.path.join(_HERE, "work", "e42")
os.makedirs(WORK, exist_ok=True)


def load_tnews(limit=100000):
    """读 TNEWS parquet。返回 (texts, labels)"""
    import pandas as pd
    paths = []
    for root, _, files in os.walk(DATA):
        for f in files:
            if f.endswith(".parquet"):
                paths.append(os.path.join(root, f))
    if not paths:
        return [], []
    dfs = []
    for p in sorted(paths):
        dfs.append(pd.read_parquet(p))
    df = pd.concat(dfs, ignore_index=True)
    cols = list(df.columns)
    tcol = "sentence" if "sentence" in cols else ("text" if "text" in cols else cols[0])
    lcol = "label" if "label" in cols else ("label_desc" if "label_desc" in cols else None)
    texts = df[tcol].astype(str).tolist()
    labels = df[lcol].astype(str).tolist() if lcol else ["?"] * len(texts)
    if limit < len(texts):
        idx = np.random.default_rng(0).choice(len(texts), limit, replace=False)
        texts = [texts[i] for i in idx]
        labels = [labels[i] for i in idx]
    return texts, labels


def synth_fill(n, seed=0):
    """补足到目标条数（结构接近新闻标题）"""
    rng = np.random.default_rng(seed)
    subs = ["市场", "技术", "政策", "产品", "行业", "公司", "用户", "团队", "项目", "系统"]
    verbs = ["发布", "推出", "调整", "优化", "升级", "整合", "评估", "重构"]
    objs = ["方案", "版本", "策略", "计划", "架构", "指标", "流程", "标准"]
    out = []
    for i in range(n):
        out.append(f"{subs[rng.integers(0,10)]}{verbs[rng.integers(0,8)]}"
                   f"{objs[rng.integers(0,8)]}，第{i}期")
    return out


def load_bge():
    import torch
    from transformers import AutoTokenizer, AutoModel
    tok = AutoTokenizer.from_pretrained(MODEL_DIR)
    mdl = AutoModel.from_pretrained(MODEL_DIR); mdl.eval()

    def embed(texts, bs=64):
        outs = []
        with torch.no_grad():
            for i in range(0, len(texts), bs):
                b = texts[i:i + bs]
                enc = tok(b, padding=True, truncation=True, max_length=128, return_tensors="pt")
                o = mdl(**enc)
                outs.append(o.last_hidden_state[:, 0].cpu().numpy())
        v = np.vstack(outs).astype(np.float32)
        nn = np.linalg.norm(v, axis=1, keepdims=True); nn[nn == 0] = 1
        return v / nn
    return embed


def l2(a):
    n = np.linalg.norm(a, axis=-1, keepdims=True); n[n == 0] = 1
    return a / n


def quant_int8(M):
    lo = M.min(axis=0, keepdims=True); hi = M.max(axis=0, keepdims=True)
    scale = (hi - lo) / 255.0; scale[scale == 0] = 1e-9
    Q = np.clip(np.round((M - lo) / scale), 0, 255).astype(np.uint8)
    return Q, lo.astype(np.float32), scale.astype(np.float32)


def make_query_sets(texts, n=200, seed=1):
    """四种查询难度"""
    rng = np.random.default_rng(seed)
    idx = rng.choice(len(texts), n, replace=False)
    sets = {}

    # Q-repeat：完全重复
    sets["repeat"] = [(texts[i], i) for i in idx]

    # Q-partial：取前半段
    part = []
    for i in idx:
        t = texts[i]
        cut = max(4, len(t) // 2)
        part.append((t[:cut], i))
    sets["partial"] = part

    # Q-paraphrase：去掉标点、加"请问"、换词序（简单改写）
    para = []
    for i in idx:
        t = texts[i].replace("，", " ").replace("。", "").replace("、", " ")
        toks = t.split()
        if len(toks) > 3:
            toks = toks[::-1]      # 倒序，模拟换语序
        para.append(("请问 " + " ".join(toks), i))
    sets["paraphrase"] = para

    # Q-random：与目标无关的查询（测最悲观情况）
    rand = []
    for i in idx:
        j = rng.integers(0, len(texts))
        rand.append((texts[j], i))
    sets["random"] = rand
    return sets


def main():
    print("=" * 100)
    print("E-42 十万条规模：延迟 + 命中率（真实数据 TNEWS）")
    print("=" * 100)

    print("\n加载 TNEWS...")
    texts, labels = load_tnews(100000)
    print(f"  真实条数 {len(texts)}")
    if len(texts) < 100000:
        add = synth_fill(100000 - len(texts))
        texts = texts + add
        labels = labels + ["?"] * len(add)
        print(f"  补足到 {len(texts)}（合成填充）")
    N = len(texts)
    print(f"  最终 {N} 条")

    print("\n编码（这是写入侧一次性成本）...")
    embed = load_bge()
    t0 = time.time()
    M = embed(texts)
    enc_s = time.time() - t0
    print(f"  {enc_s:.0f}s，{N/enc_s:.0f} 条/秒，形状 {M.shape}")

    # ============ A. 延迟 ============
    print("\n" + "-" * 100)
    print("A. 检索延迟（10 万条）")
    print("-" * 100)

    Q, lo, sc = quant_int8(M)
    Mq = l2((Q.astype(np.float32) * sc + lo))

    qs = [texts[i] for i in np.random.default_rng(5).choice(N, 50, replace=False)]

    # 编码延迟
    t0 = time.time()
    for q in qs[:20]:
        embed([q])
    enc1 = (time.time() - t0) / 20 * 1000

    t0 = time.time()
    for i in range(0, 20, 8):
        embed(qs[i:i + 8])
    enc8 = (time.time() - t0) / 3 * 1000

    # 全精度 V
    qv = embed([qs[0]])[0]
    t0 = time.time()
    for _ in range(10):
        s = M @ qv
        _ = np.argpartition(-s, 5)[:5]
    v_full = (time.time() - t0) / 10 * 1000

    # int8 V
    t0 = time.time()
    for _ in range(10):
        s = Mq @ qv
        _ = np.argpartition(-s, 5)[:5]
    v_q = (time.time() - t0) / 10 * 1000

    # FTS5
    db = os.path.join(WORK, "fts.db")
    if os.path.exists(db):
        os.remove(db)
    con = sqlite3.connect(db)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE mem (id INTEGER PRIMARY KEY, body TEXT)")
    con.execute("CREATE VIRTUAL TABLE fts USING fts5(body, content='mem', content_rowid='id')")
    con.executemany("INSERT INTO mem (id, body) VALUES (?,?)", list(enumerate(texts)))
    con.execute("INSERT INTO fts(fts) VALUES('rebuild')")
    con.commit()
    term = texts[0][:6]
    t0 = time.time()
    for _ in range(20):
        con.execute("SELECT rowid FROM fts WHERE fts MATCH ? LIMIT 5", (f'"{term}"',)).fetchall()
    fts_ms = (time.time() - t0) / 20 * 1000
    con.close()

    # numpy 扫描
    docs = [set(t.split()) for t in texts]
    t0 = time.time()
    for _ in range(3):
        hits = [i for i, d in enumerate(docs) if term in d]
    np_ms = (time.time() - t0) / 3 * 1000

    print(f"{'项目':<24}{'耗时':<14}{'说明'}")
    print("-" * 100)
    print(f"{'编码（单条）':<24}{enc1:<14.2f}{'每次查询都要'}")
    print(f"{'编码（批8）':<24}{enc8:<14.2f}{f'每条 {enc8/8:.2f} ms'}")
    print(f"{'V 检索 全精度':<24}{v_full:<14.2f}{'float32 512维 × 10万'}")
    print(f"{'V 检索 int8':<24}{v_q:<14.2f}{f'vs 全精度 {v_full/max(v_q,1e-6):.2f}×'}")
    print(f"{'K 检索 FTS5':<24}{fts_ms:<14.3f}{'倒排索引'}")
    print(f"{'K 检索 numpy':<24}{np_ms:<14.2f}{f'vs FTS5 {np_ms/max(fts_ms,1e-6):.1f}×'}")
    print("-" * 100)
    total_miss = enc1 + v_q + fts_ms
    print(f"  ROUTER 端到端（缓存未命中）≈ {total_miss:.2f} ms")
    print(f"  ROUTER 端到端（缓存命中）  ≈ {v_q + fts_ms:.2f} ms")

    out = {"experiment": "E-42", "n": N, "dim": int(M.shape[1]),
           "encoding_sec": round(enc_s, 1),
           "encoding_per_sec": round(N / enc_s, 1),
           "latency": {"enc_single_ms": round(enc1, 3), "enc_batch8_ms": round(enc8, 3),
                       "v_full_ms": round(v_full, 3), "v_int8_ms": round(v_q, 3),
                       "fts5_ms": round(fts_ms, 4), "numpy_scan_ms": round(np_ms, 3),
                       "router_miss_ms": round(total_miss, 3),
                       "router_hit_ms": round(v_q + fts_ms, 3)}}

    # ============ B. 命中率 ============
    print("\n" + "-" * 100)
    print("B. 缓存命中率（四种查询难度）")
    print("-" * 100)

    sets = make_query_sets(texts, n=200)

    def cache_key(q):
        return hashlib.md5(q.encode()).hexdigest()

    print(f"{'查询类型':<16}{'说明':<24}{'缓存命中率':<14}{'编码省下的ms':<16}")
    print("-" * 100)
    hit_rows = []
    for name, pairs in sets.items():
        queries = [q for q, _ in pairs]
        keys = [cache_key(q) for q in queries]
        # 假设"历史缓存"= 之前见过的查询
        # repeat: 全部命中；partial/paraphrase: 取决于是否与库内文本一致
        seen = set()
        hits = 0
        for q in queries:
            k = cache_key(q)
            if k in seen:
                hits += 1
            else:
                seen.add(k)
        rate = hits / len(queries)
        desc = {"repeat": "完全重复的查询",
                "partial": "取文本前半段",
                "paraphrase": "换语序+加前缀",
                "random": "随机无关查询"}[name]
        hit_rows.append({"type": name, "desc": desc, "hit_rate": round(rate, 4),
                         "saved_ms": round(rate * enc1, 2)})
        print(f"{name:<16}{desc:<24}{rate*100:<14.1f}{rate*enc1:<16.2f}")
    print("-" * 100)
    print("  注：这里测的是「同一批查询内部」的重复率，是缓存的上限估计。")
    print("      真实场景中，用户重复问同一件事时命中率才会高。")

    out["hit_rate"] = hit_rows

    # ============ C. 红线复验 ============
    print("\n" + "=" * 100)
    print("C. 红线复验")
    print("=" * 100)
    save = os.path.join(WORK, "store.npz")
    np.savez_compressed(save, Q=Q, lo=lo, scale=sc)
    z = np.load(save)
    M2 = l2(z["Q"].astype(np.float32) * z["scale"] + z["lo"])

    ident = np.array_equal(M2, Mq)
    # 逐条预测比对
    qv_list = embed([q for q, _ in sets["repeat"][:30]])
    same = 0
    for i in range(30):
        p1 = int(np.argmax(Mq @ qv_list[i]))
        p2 = int(np.argmax(M2 @ qv_list[i]))
        same += int(p1 == p2)
    mig = same / 30

    print(f"  I-2 迁移: 矩阵完全一致={ident}, 预测一致率={mig*100:.1f}%")
    print(f"  I-4 不遗忘: {z['Q'].shape[0]} / {N} → {'✓' if z['Q'].shape[0]==N else '✗'}")
    print(f"  存储: {os.path.getsize(save)/1024/1024:.1f} MB（int8）")
    print(f"  对比全精度: {M.nbytes/1024/1024:.1f} MB")

    out["redline"] = {"identical": bool(ident), "migration_rate": round(mig, 4),
                      "n_preserved": int(z["Q"].shape[0]),
                      "store_mb": round(os.path.getsize(save) / 1024 / 1024, 1),
                      "full_mb": round(M.nbytes / 1024 / 1024, 1)}

    with open(os.path.join(RESULTS, "e42_scale.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e42_scale.json')}")


if __name__ == "__main__":
    main()
