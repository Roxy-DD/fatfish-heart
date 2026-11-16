"""
E-45 编码加速：PyTorch vs ONNX Runtime vs 动态量化 vs 换小模型

现状：编码 4.61 ms 占端到端 97%（检索已优化到 0.1 ms）。
这是唯一剩下的瓶颈。

四条路：
  A. ONNX Runtime（图优化 + 算子融合）
  B. ONNX + 动态 int8 量化
  C. PyTorch 动态量化（torch.quantization）
  D. 更小模型（bge-small 24M → 用截断维度 / 更短 max_length）

每项都必须报告：
  ① 延迟（预热后中位数）
  ② 向量一致性（对原模型输出的余弦相似度）← 这是红线：换实现不能改变语义
  ③ 端到端影响
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
WORK = os.path.join(_HERE, "work", "e45")
os.makedirs(WORK, exist_ok=True)
DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "tnews")


def load_texts(n=2000):
    import pandas as pd
    paths = []
    for root, _, files in os.walk(DATA):
        for f in files:
            if f.endswith(".parquet"):
                paths.append(os.path.join(root, f))
    df = pd.concat([pd.read_parquet(p) for p in sorted(paths)], ignore_index=True)
    tcol = "sentence" if "sentence" in df.columns else df.columns[0]
    return df[tcol].astype(str).tolist()[:n]


def median_ms(fn, repeat=30, warmup=5):
    for _ in range(warmup):
        fn()
    ts = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t0) * 1000)
    a = np.array(ts)
    return {"median": float(np.median(a)), "p10": float(np.percentile(a, 10)),
            "p90": float(np.percentile(a, 90))}


def cos_sim(a, b):
    a = a / np.linalg.norm(a, axis=-1, keepdims=True)
    b = b / np.linalg.norm(b, axis=-1, keepdims=True)
    return float((a * b).sum(axis=-1).mean())


def main():
    import torch
    from transformers import AutoTokenizer, AutoModel

    print("=" * 96)
    print("E-45 编码加速：PyTorch vs ONNX vs 量化")
    print("=" * 96)

    texts = load_texts(2000)
    print(f"\n测试文本 {len(texts)} 条")
    print(f"torch 线程数 {torch.get_num_threads()}")

    tok = AutoTokenizer.from_pretrained(MODEL_DIR)
    mdl = AutoModel.from_pretrained(MODEL_DIR).eval()

    def pt_embed(batch, maxlen=128):
        enc = tok(batch, padding=True, truncation=True, max_length=maxlen, return_tensors="pt")
        with torch.no_grad():
            o = mdl(**enc)
        v = o.last_hidden_state[:, 0].cpu().numpy()
        n = np.linalg.norm(v, axis=1, keepdims=True); n[n == 0] = 1
        return v / n

    # 基线
    print("\n" + "-" * 96)
    print("基线：PyTorch")
    print("-" * 96)
    q1 = [texts[0]]
    base = median_ms(lambda: pt_embed(q1), repeat=30, warmup=8)
    print(f"  单条: {base['median']:.2f} ms  (p10 {base['p10']:.2f} / p90 {base['p90']:.2f})")

    for bs in [8, 16, 32]:
        b = texts[:bs]
        r = median_ms(lambda b=b: pt_embed(b), repeat=15, warmup=3)
        print(f"  批{bs:>2}: {r['median']:.2f} ms → 每条 {r['median']/bs:.2f} ms")

    ref = pt_embed(texts[:200])     # 参考向量（用于一致性）

    results = {"pytorch": {"single_ms": round(base["median"], 3),
                           "p10": round(base["p10"], 3),
                           "p90": round(base["p90"], 3)}}

    # ---------- A. ONNX ----------
    print("\n" + "-" * 96)
    print("A. ONNX Runtime")
    print("-" * 96)
    onnx_path = os.path.join(WORK, "bge.onnx")
    ok_onnx = False
    try:
        if not os.path.exists(onnx_path):
            class Wrap(torch.nn.Module):
                def __init__(self, m):
                    super().__init__()
                    self.m = m
                def forward(self, ids, mask):
                    return self.m(input_ids=ids, attention_mask=mask).last_hidden_state[:, 0]

            enc = tok(texts[:2], padding=True, truncation=True, max_length=128, return_tensors="pt")
            dummy = (enc["input_ids"], enc["attention_mask"])
            torch.onnx.export(
                Wrap(mdl).eval(), dummy, onnx_path,
                input_names=["ids", "mask"],
                output_names=["cls"],
                dynamic_axes={"ids": {0: "b", 1: "s"}, "mask": {0: "b", 1: "s"},
                              "cls": {0: "b"}},
                opset_version=14, do_constant_folding=True)
            print(f"  导出 ONNX: {os.path.getsize(onnx_path)/1024/1024:.1f} MB")
        ok_onnx = True
    except Exception as e:
        print(f"  导出失败: {type(e).__name__}: {str(e)[:200]}")

    if ok_onnx:
        import onnxruntime as ort
        for threads in [4, 8, 14]:
            so = ort.SessionOptions()
            so.intra_op_num_threads = threads
            so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            sess = ort.InferenceSession(onnx_path, so, providers=["CPUExecutionProvider"])

            def ort_embed(batch):
                enc = tok(batch, padding=True, truncation=True, max_length=128, return_tensors="np")
                out = sess.run(["cls"], {"ids": enc["input_ids"].astype(np.int64),
                                         "mask": enc["attention_mask"].astype(np.int64)})[0]
                n = np.linalg.norm(out, axis=1, keepdims=True); n[n == 0] = 1
                return out / n

            r = median_ms(lambda: ort_embed(q1), repeat=30, warmup=8)
            sim = cos_sim(ort_embed(texts[:200]), ref)
            print(f"  线程{threads:>2}: 单条 {r['median']:.2f} ms  "
                  f"(p10 {r['p10']:.2f}/p90 {r['p90']:.2f})  余弦一致性 {sim:.6f}")
            results[f"onnx_t{threads}"] = {"single_ms": round(r["median"], 3),
                                           "p10": round(r["p10"], 3),
                                           "p90": round(r["p90"], 3),
                                           "cos_to_ref": round(sim, 6)}

        # 批量
        for bs in [8, 16, 32]:
            b = texts[:bs]
            r = median_ms(lambda b=b: ort_embed(b), repeat=15, warmup=3)
            print(f"  线程14 批{bs:>2}: {r['median']:.2f} ms → 每条 {r['median']/bs:.2f} ms")
            results[f"onnx_b{bs}"] = {"batch_ms": round(r["median"], 3),
                                      "per_item_ms": round(r["median"] / bs, 3)}

    # ---------- B. PyTorch 动态量化 ----------
    print("\n" + "-" * 96)
    print("B. PyTorch 动态量化（int8 权重）")
    print("-" * 96)
    try:
        qmdl = torch.quantization.quantize_dynamic(mdl, {torch.nn.Linear}, dtype=torch.qint8)
        qmdl.eval()

        def q_embed(batch):
            enc = tok(batch, padding=True, truncation=True, max_length=128, return_tensors="pt")
            with torch.no_grad():
                o = qmdl(**enc)
            v = o.last_hidden_state[:, 0].cpu().numpy()
            n = np.linalg.norm(v, axis=1, keepdims=True); n[n == 0] = 1
            return v / n

        r = median_ms(lambda: q_embed(q1), repeat=30, warmup=8)
        sim = cos_sim(q_embed(texts[:200]), ref)
        print(f"  单条 {r['median']:.2f} ms  (p10 {r['p10']:.2f}/p90 {r['p90']:.2f})")
        print(f"  余弦一致性 {sim:.6f}")
        results["pt_dynamic_int8"] = {"single_ms": round(r["median"], 3),
                                      "p10": round(r["p10"], 3),
                                      "p90": round(r["p90"], 3),
                                      "cos_to_ref": round(sim, 6)}
    except Exception as e:
        print(f"  失败: {type(e).__name__}: {str(e)[:200]}")

    # ---------- C. 缩短 max_length ----------
    print("\n" + "-" * 96)
    print("C. 缩短 max_length（减少序列长度）")
    print("-" * 96)
    for ml in [64, 32]:
        avg_len = np.mean([len(t) for t in texts[:200]])
        r = median_ms(lambda ml=ml: pt_embed(q1, ml), repeat=30, warmup=5)
        sim = cos_sim(pt_embed(texts[:200], ml), ref)
        print(f"  maxlen={ml:>3}: {r['median']:.2f} ms  余弦一致性 {sim:.4f}  "
              f"（文本均长 {avg_len:.0f} 字）")
        results[f"pt_maxlen{ml}"] = {"single_ms": round(r["median"], 3),
                                     "cos_to_ref": round(sim, 4)}

    # ---------- D. 只取前 N 维 ----------
    print("\n" + "-" * 96)
    print("D. 截断维度（512 → 256 / 128，不做降维投影）")
    print("-" * 96)
    for d in [256, 128]:
        sim = cos_sim(ref[:, :d], ref[:, :d])
        # 截断后与全维的排序一致性
        rng = np.random.default_rng(3)
        q = ref[rng.integers(0, 200, 30)]
        full_top = [set(np.argsort(-(ref @ q[i]))[:5].tolist()) for i in range(30)]
        cut_top = [set(np.argsort(-(ref[:, :d] @ q[i, :d]))[:5].tolist()) for i in range(30)]
        overlap = np.mean([len(a & b) / 5 for a, b in zip(full_top, cut_top)])
        print(f"  维度 {d}: top5 与全维重叠 {overlap*100:.1f}%")
        results[f"dim{d}"] = {"top5_overlap": round(float(overlap), 4)}

    # ---------- 汇总 ----------
    print("\n" + "=" * 96)
    print("汇总（单条编码延迟）")
    print("=" * 96)
    base_ms = base["median"]
    rows = []
    for name, v in results.items():
        if "single_ms" in v:
            rows.append((name, v["single_ms"], v.get("cos_to_ref", None)))
    rows.sort(key=lambda x: x[1])
    print(f"{'方案':<24}{'单条ms':<12}{'加速':<10}{'余弦一致性'}")
    print("-" * 96)
    for name, ms, sim in rows:
        s = f"{sim:.6f}" if sim is not None else "—"
        print(f"{name:<24}{ms:<12.2f}{base_ms/ms:<10.2f}{s}")

    out = {"experiment": "E-45", "baseline_pt_ms": round(base_ms, 3),
           "results": results}
    with open(os.path.join(RESULTS, "e45_encode_speed.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e45_encode_speed.json')}")


if __name__ == "__main__":
    main()
