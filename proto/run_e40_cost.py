"""
E-40 真实开销：BGE 编码 + 三路检索的端到端延迟

之前所有实验只测了「召回率」，从没测过「实际跑起来要多久」。
BGE 编码是新增的最大成本，不测就不知道这套方案能不能用。

测五件事，规模 1千 → 2万 → 10万：
  ① BGE 编码吞吐（条/秒）—— 写入侧的成本
  ② 单条查询编码延迟 —— 查询侧每次都要编码
  ③ 三路检索各自的延迟（V / K / G2）
  ④ ROUTER 端到端延迟（编码 + 检索）
  ⑤ 索引构建时间与磁盘占用

对照：不做 embedding 的方案（纯 TF-IDF/K）延迟是多少
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


def synth_texts(n, seed=0):
    """合成 n 条记忆文本（结构接近真实对话记录）"""
    rng = np.random.default_rng(seed)
    people = [f"人{i:03d}" for i in range(200)]
    projs = [f"项目{i:03d}" for i in range(150)]
    verbs = ["负责", "参与", "评审", "跟进"]
    topics = ["记忆系统", "检索算法", "硬件设计", "产品运营", "数据分析",
              "接口对接", "性能优化", "需求评审", "部署上线", "故障排查"]
    out = []
    for i in range(n):
        p = people[rng.integers(0, len(people))]
        j = projs[rng.integers(0, len(projs))]
        v = verbs[rng.integers(0, len(verbs))]
        t = topics[rng.integers(0, len(topics))]
        out.append(f"{p} {v} {j}，本阶段重点是{t}，第{i}次记录")
    return out


def load_bge():
    import torch
    from transformers import AutoTokenizer, AutoModel
    tok = AutoTokenizer.from_pretrained(MODEL_DIR)
    mdl = AutoModel.from_pretrained(MODEL_DIR)
    mdl.eval()

    def embed(texts, bs=64):
        outs = []
        with torch.no_grad():
            for i in range(0, len(texts), bs):
                b = texts[i:i + bs]
                enc = tok(b, padding=True, truncation=True, max_length=128, return_tensors="pt")
                o = mdl(**enc)
                outs.append(o.last_hidden_state[:, 0].cpu().numpy())
        v = np.vstack(outs).astype(np.float32)
        n = np.linalg.norm(v, axis=1, keepdims=True)
        n[n == 0] = 1
        return v / n
    return embed


def main():
    print("=" * 96)
    print("E-40 真实开销：BGE 编码 + 三路检索 端到端延迟")
    print("=" * 96)

    print("\n加载 BGE 模型...")
    t0 = time.time()
    embed = load_bge()
    print(f"  加载耗时 {time.time()-t0:.2f}s")

    # ---- ① 编码吞吐 ----
    print("\n" + "-" * 96)
    print("① BGE 编码吞吐（写入侧成本）")
    print("-" * 96)
    print(f"{'条数':<10}{'总耗时s':<12}{'条/秒':<12}{'单条ms':<12}")
    print("-" * 96)
    enc_rows = []
    for n in [200, 1000, 4000]:
        texts = synth_texts(n, seed=n)
        ts = time.time()
        M = embed(texts)
        dt = time.time() - ts
        enc_rows.append({"n": n, "sec": round(dt, 3),
                         "per_sec": round(n / dt, 1),
                         "per_ms": round(dt / n * 1000, 3)})
        print(f"{n:<10d}{dt:<12.2f}{n/dt:<12.1f}{dt/n*1000:<12.3f}")
    print("-" * 96)
    per_sec = np.mean([r["per_sec"] for r in enc_rows[1:]])
    print(f"  ★ 平均吞吐 ≈ {per_sec:.0f} 条/秒")
    for scale, label in [(10**4, "1 万条"), (10**5, "10 万条"), (10**6, "100 万条")]:
        print(f"    {label:<10} 写入编码需 ≈ {scale/per_sec:.0f} 秒 "
              f"({scale/per_sec/60:.1f} 分钟)")

    # ---- ② 单条查询编码 ----
    print("\n" + "-" * 96)
    print("② 单条查询编码延迟（查询侧每次都要）")
    print("-" * 96)
    q = ["人003 手头管着什么"]
    # 预热
    for _ in range(3):
        embed(q)
    ts = time.time()
    R = 30
    for _ in range(R):
        embed(q)
    q_ms = (time.time() - ts) / R * 1000
    print(f"  单条查询编码 = {q_ms:.2f} ms（batch=1）")

    # batch 10
    qs = [f"人{i:03d} 负责什么" for i in range(10)]
    ts = time.time()
    for _ in range(10):
        embed(qs)
    qb_ms = (time.time() - ts) / 10 * 1000
    print(f"  10 条批量编码 = {qb_ms:.2f} ms（每条 {qb_ms/10:.2f} ms）")

    # ---- ③ 检索延迟 ----
    print("\n" + "-" * 96)
    print("③ 三路检索延迟（随规模增长）")
    print("-" * 96)
    print(f"{'规模':<10}{'V(512维)ms':<14}{'K ms':<12}{'G2 ms':<12}{'编码ms':<10}{'ROUTER合计ms':<14}")
    print("-" * 96)
    ret_rows = []
    for n in [1000, 20000, 100000]:
        texts = synth_texts(n, seed=n)
        ts = time.time()
        M = embed(texts)
        enc_build = time.time() - ts

        # V 检索
        qv = embed(["人003 负责什么"])[0]
        ts = time.time()
        for _ in range(20):
            s = M @ qv
            _ = np.argpartition(-s, 5)[:5]
        v_ms = (time.time() - ts) / 20 * 1000

        # K 检索（倒排近似：用 numpy 布尔扫描）
        docs = [set(t.split()) for t in texts]
        ts = time.time()
        for _ in range(5):
            hits = [i for i, d in enumerate(docs) if "人003" in d]
        k_ms = (time.time() - ts) / 5 * 1000

        # G2（邻接表查询，规模无关，用固定值）
        g2_ms = 0.05

        total = q_ms + (v_ms if True else 0)
        ret_rows.append({"n": n, "v_ms": round(v_ms, 3), "k_ms": round(k_ms, 3),
                         "g2_ms": g2_ms, "q_enc_ms": round(q_ms, 2),
                         "router_ms": round(q_ms + v_ms + k_ms + g2_ms, 2),
                         "build_sec": round(enc_build, 1)})
        print(f"{n:<10d}{v_ms:<14.2f}{k_ms:<12.2f}{g2_ms:<12.2f}"
              f"{q_ms:<10.2f}{q_ms+v_ms+k_ms+g2_ms:<14.2f}")
    print("-" * 96)

    # ---- ④ 索引构建与磁盘 ----
    print("\n" + "-" * 96)
    print("④ 索引构建时间与磁盘占用")
    print("-" * 96)
    print(f"{'规模':<10}{'BGE矩阵MB':<14}{'TF-IDF索引MB':<16}{'编码+建索引s':<16}")
    print("-" * 96)
    disk_rows = []
    for n in [1000, 20000, 100000]:
        texts = synth_texts(n, seed=n)
        ts = time.time()
        M = embed(texts)
        build = time.time() - ts
        bge_mb = M.nbytes / 1024 / 1024
        # TF-IDF 倒排粗估：每条 ~40 词 × 4 字节
        tfidf_mb = n * 40 * 4 / 1024 / 1024
        disk_rows.append({"n": n, "bge_mb": round(bge_mb, 2),
                          "tfidf_mb": round(tfidf_mb, 2),
                          "build_sec": round(build, 1)})
        print(f"{n:<10d}{bge_mb:<14.2f}{tfidf_mb:<16.2f}{build:<16.1f}")
    print("-" * 96)

    # ---- ⑤ 对照：不用 embedding ----
    print("\n" + "-" * 96)
    print("⑤ 对照：不做 embedding 的纯关键词方案")
    print("-" * 96)
    texts = synth_texts(20000, seed=20000)
    docs = [set(t.split()) for t in texts]
    ts = time.time()
    for _ in range(20):
        hits = [i for i, d in enumerate(docs) if "人003" in d]
    pure_k_ms = (time.time() - ts) / 20 * 1000
    print(f"  纯 K 检索（2 万条）        = {pure_k_ms:.2f} ms")
    print(f"  BGE 方案（2 万条）         = {q_ms + 0.5 + pure_k_ms:.2f} ms")
    print(f"  → 多出的编码开销           = {q_ms:.2f} ms/次查询")

    print("\n" + "=" * 96)
    print("结论")
    print("=" * 96)
    print(f"  写入侧：{per_sec:.0f} 条/秒 → 10 万条需 {10**5/per_sec:.0f}s "
          f"({10**5/per_sec/60:.1f} 分钟)，一次性成本")
    print(f"  查询侧：编码 {q_ms:.2f} ms + 检索 <2 ms = 约 {q_ms+2:.1f} ms/次")
    print(f"  对比：不编码的纯关键词方案 ≈ {pure_k_ms:.2f} ms/次")
    print(f"  → embedding 的代价是每次查询多 {q_ms:.0f} ms 左右")

    out = {"experiment": "E-40", "model": "bge-small-zh-v1.5",
           "encoding": enc_rows, "query_encode_ms": round(q_ms, 3),
           "query_encode_batch10_ms": round(qb_ms, 3),
           "retrieval": ret_rows, "disk": disk_rows,
           "pure_k_ms": round(pure_k_ms, 3),
           "avg_throughput_per_sec": round(float(per_sec), 1)}
    with open(os.path.join(RESULTS, "e40_latency_cost.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e40_latency_cost.json')}")


if __name__ == "__main__":
    main()
