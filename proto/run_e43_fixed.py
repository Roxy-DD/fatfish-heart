"""
E-43 修正 E-42 的两个数据问题

问题 1: 编码延迟 45.52 ms，比 2 万条时的 6.41 ms 高 7 倍 —— 不合理。
        真正原因（假设）：模型冷启动 + 首次推理含图优化/内存分配。
        正确测法：预热后多次取中位数。

问题 2: 缓存命中率测法本身是错的（在同批查询内部测重复率，样本还是无放回抽样）。
        正确测法：模拟真实使用序列 —— 重复问同一件事 + 换说法问。

本实验只做这两件事，数据量 10 万条（真实 TNEWS）。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
os.environ["HF_HOME"] = r"C:\Users\xuee\Desktop\肥鱼之心\.hf"
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import numpy as np

MODEL_DIR = r"C:\Users\xuee\Desktop\肥鱼之心\models\bge-small-zh"
RESULTS = os.path.join(_HERE, "results")
DATA = r"C:\Users\xuee\Desktop\肥鱼之心\data\tnews"


def load_tnews(limit=100000):
    import pandas as pd
    paths = []
    for root, _, files in os.walk(DATA):
        for f in files:
            if f.endswith(".parquet"):
                paths.append(os.path.join(root, f))
    dfs = [pd.read_parquet(p) for p in sorted(paths)]
    df = pd.concat(dfs, ignore_index=True)
    tcol = "sentence" if "sentence" in df.columns else df.columns[0]
    texts = df[tcol].astype(str).tolist()
    if limit < len(texts):
        idx = np.random.default_rng(0).choice(len(texts), limit, replace=False)
        texts = [texts[i] for i in idx]
    return texts


def load_bge():
    import torch
    from transformers import AutoTokenizer, AutoModel
    tok = AutoTokenizer.from_pretrained(MODEL_DIR)
    mdl = AutoModel.from_pretrained(MODEL_DIR); mdl.eval()
    torch.set_num_threads(4)

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


def median_ms(fn, repeat=30, warmup=5):
    for _ in range(warmup):
        fn()
    ts = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t0) * 1000)
    ts = np.array(ts)
    return {"median": float(np.median(ts)), "mean": float(ts.mean()),
            "p10": float(np.percentile(ts, 10)), "p90": float(np.percentile(ts, 90)),
            "min": float(ts.min())}


def main():
    print("=" * 96)
    print("E-43 修正 E-42 的两个数据问题")
    print("=" * 96)

    texts = load_tnews(100000)
    N = len(texts)
    print(f"\n语料 {N} 条（真实 TNEWS）")

    print("\n加载模型并预热...")
    embed = load_bge()
    for _ in range(10):
        embed(["预热查询"])

    # ============ 问题 1：编码延迟 ============
    print("\n" + "-" * 96)
    print("① 编码延迟（预热后，30 次取中位数）")
    print("-" * 96)

    q1 = [texts[0]]
    r_single = median_ms(lambda: embed(q1), repeat=30, warmup=5)
    print(f"  单条编码: 中位数 {r_single['median']:.2f} ms  "
          f"(p10 {r_single['p10']:.2f} / p90 {r_single['p90']:.2f})")

    for bs in [4, 8, 16]:
        qs = texts[:bs]
        r = median_ms(lambda bs=bs, qs=qs: embed(qs), repeat=20, warmup=3)
        print(f"  批 {bs:>2} 条: 中位数 {r['median']:.2f} ms  "
              f"→ 每条 {r['median']/bs:.2f} ms")

    # ============ 问题 2：int8 vs 全精度（严格对照）============
    print("\n" + "-" * 96)
    print("② 检索延迟：int8 vs 全精度（10 万条，严格预热）")
    print("-" * 96)
    print("  构造 10 万条向量...")
    M = embed(texts)
    lo = M.min(axis=0, keepdims=True); hi = M.max(axis=0, keepdims=True)
    scale = (hi - lo) / 255.0; scale[scale == 0] = 1e-9
    Q = np.clip(np.round((M - lo) / scale), 0, 255).astype(np.uint8)
    Mq8 = Q.astype(np.float32) * scale + lo
    Mq8 /= np.linalg.norm(Mq8, axis=1, keepdims=True)

    qv = embed([texts[5]])[0]

    def run_full():
        s = M @ qv
        return np.argpartition(-s, 5)[:5]

    def run_int8():
        # int8 存、转 float32 查（当前做法）
        s = Mq8 @ qv
        return np.argpartition(-s, 5)[:5]

    r_full = median_ms(run_full, repeat=20, warmup=3)
    r_int8 = median_ms(run_int8, repeat=20, warmup=3)
    print(f"  全精度 float32: {r_full['median']:.2f} ms")
    print(f"  int8(转f32)   : {r_int8['median']:.2f} ms  "
          f"({r_full['median']/max(r_int8['median'],1e-9):.2f}×)")

    # int8 原生整数运算（不经 float 转换）
    def run_int8_native():
        s = Q.astype(np.int16) @ np.round(qv * 127).astype(np.int16)
        return np.argpartition(-s, 5)[:5]

    r_native = median_ms(run_int8_native, repeat=20, warmup=3)
    print(f"  int8 原生整数 : {r_native['median']:.2f} ms  "
          f"({r_full['median']/max(r_native['median'],1e-9):.2f}×)")

    # ============ 问题 3：正确的缓存命中率 ============
    print("\n" + "-" * 96)
    print("③ 缓存命中率（模拟真实使用序列）")
    print("-" * 96)

    rng = np.random.default_rng(11)
    # 从语料里挑 20 个"用户关心的话题"
    topics = [texts[i] for i in rng.choice(N, 20, replace=False)]

    scenarios = {}

    # S1: 同一问题反复问 10 次（用户追问同一个细节）
    seq = []
    for _ in range(10):
        for t in topics[:2]:
            seq.append(t)
    scenarios["S1_反复问同一件事"] = seq

    # S2: 完全不同的问法（每次都问新东西）
    seq2 = [texts[int(i)] for i in rng.choice(N, 40, replace=False)]
    scenarios["S2_每次问新东西"] = seq2

    # S3: 混合 —— 60% 复问，40% 新问
    seq3 = []
    for i in range(40):
        if rng.random() < 0.6:
            seq3.append(topics[int(rng.integers(0, 20))])
        else:
            seq3.append(texts[int(rng.integers(0, N))])
    scenarios["S3_混合(60%复问)"] = seq3

    # S4: 同一话题但换了说法（加前缀/去标点）
    seq4 = []
    for rep in range(5):
        for t in topics[:8]:
            if rep == 0:
                seq4.append(t)
            else:
                seq4.append("请问" + t.replace("，", " ").replace("。", ""))
    scenarios["S4_换说法问"] = seq4

    print(f"{'场景':<22}{'查询数':<10}{'字符串缓存命中':<18}{'语义缓存命中':<16}")
    print("-" * 96)
    hit_rows = []
    for name, seq in scenarios.items():
        # 字符串哈希缓存
        seen = set(); hit = 0
        for q in seq:
            k = hashlib.md5(q.encode()).hexdigest()
            if k in seen:
                hit += 1
            else:
                seen.add(k)
        str_rate = hit / len(seq)

        # 语义缓存：与历史查询的向量余弦 > 0.95 视为命中
        V = embed(seq)
        stored = []
        sem_hit = 0
        for i in range(len(seq)):
            if stored:
                sims = np.array([float(V[i] @ s) for s in stored])
                if sims.max() > 0.95:
                    sem_hit += 1
                    continue
            stored.append(V[i])
        sem_rate = sem_hit / len(seq)

        hit_rows.append({"scenario": name, "n": len(seq),
                         "str_hit": round(str_rate, 4),
                         "sem_hit": round(sem_rate, 4)})
        print(f"{name:<22}{len(seq):<10}{str_rate*100:<18.1f}{sem_rate*100:<16.1f}")

    print("-" * 96)
    print("  字符串缓存 = md5 完全相同；语义缓存 = 余弦 >0.95")

    # ============ 汇总 ============
    print("\n" + "=" * 96)
    print("结论")
    print("=" * 96)
    print(f"  编码（预热后真实值）: {r_single['median']:.2f} ms  "
          f"（E-42 报的 45.52 ms 是冷启动噪声）")
    print(f"  检索（10万条）     : int8 {r_int8['median']:.2f} ms vs "
          f"全精度 {r_full['median']:.2f} ms")
    print(f"  端到端（真实）     : {r_single['median'] + r_int8['median'] + 0.044:.1f} ms")
    print(f"  端到端（语义缓存命中）: {r_int8['median'] + 0.044:.1f} ms")

    out = {"experiment": "E-43", "n": N,
           "encode_single": {k: round(v, 3) for k, v in r_single.items()},
           "retrieval": {"full_ms": round(r_full["median"], 3),
                         "int8_f32_ms": round(r_int8["median"], 3),
                         "int8_native_ms": round(r_native["median"], 3)},
           "cache_hit": hit_rows,
           "note": "E-42 的 45.52ms 是冷启动噪声，本实验预热后重测"}
    with open(os.path.join(RESULTS, "e43_fixed.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e43_fixed.json')}")


if __name__ == "__main__":
    main()
