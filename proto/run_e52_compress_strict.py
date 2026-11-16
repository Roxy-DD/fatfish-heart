"""
E-52 严格同预算下的压缩验证（修正 E-51 的测量错误）

E-51 的错误：
  "同预算比较"里，top-200 × 每条8字 = 1600 字，而预算是 102 字，超了 15 倍。
  所以 +0.27 不是压缩的功劳，是花了更多字符。

修正做法（严格）：
  对每个字符预算 B，比较三种策略，都**严格不超 B**：
    FULL   取完整原文，能放几条放几条
    CUT    取更多条，每条截断到 B/n，严格 n×per ≤ B
    KW     取更多条，每条只留关键词句，再按 B 裁剪总长
  指标：召回（目标是否出现在最终注入内容里）

这样才能回答：同样的字符预算，压缩能不能换到更高召回？
"""
from __future__ import annotations

import json
import os
import re
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
CUE_FRAC = 0.15


def load_texts(n=5000, seed=0):
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


def main():
    print("=" * 96)
    print("E-52 严格同字符预算下：压缩能否换到更高召回")
    print("=" * 96)

    texts = load_texts(5000)
    N = len(texts)
    embed = load_bge()
    print(f"\n记忆库 {N} 条，编码中...")
    t0 = time.time()
    M = embed(texts)
    print(f"  编码 {time.time()-t0:.0f}s")

    rng = np.random.default_rng(9)
    targets = rng.choice(N, 200, replace=False).tolist()
    cues = {m: embed([texts[m][:max(2, int(len(texts[m]) * CUE_FRAC))]])[0]
            for m in targets}

    TOPN = 200
    print("预计算 top-200...")
    top_ids = {}
    for m in targets:
        s = M @ cues[m]
        top_ids[m] = np.argsort(-s)[:TOPN].tolist()

    avg_len = float(np.mean([len(t) for t in texts]))
    print(f"  平均记忆长度 {avg_len:.0f} 字")

    def eval_full(budget):
        """完整原文，能放几条放几条（严格不超预算）"""
        hit, used = 0, 0
        for m in targets:
            acc, n = 0, 0
            for i in top_ids[m]:
                L = len(texts[i])
                if acc + L > budget:
                    break
                acc += L
                n += 1
                if i == m:
                    hit += 1
                    break          # 命中即可，不继续
            used += acc
        return hit / len(targets), used / len(targets), n

    def eval_cut(budget, n_take):
        """取 n_take 条，每条截断到 budget/n_take（严格不超）"""
        per = budget // n_take
        if per < 2:
            return None
        hit, used = 0, 0
        for m in targets:
            sel = top_ids[m][:n_take]
            if m in sel:
                hit += 1
            used += sum(min(len(texts[i]), per) for i in sel)
        return hit / len(targets), used / len(targets)

    def eval_kw(budget, n_take):
        """取 n_take 条，每条只留含查询词的句子，再按预算裁总量"""
        per = budget // n_take
        if per < 2:
            return None
        hit, used = 0, 0
        for m in targets:
            sel = top_ids[m][:n_take]
            if m in sel:
                hit += 1
            terms = set(texts[m][:8])
            tot = 0
            for i in sel:
                sents = [s for s in re.split(r"[。！？；]", texts[i]) if s.strip()]
                keep = "。".join([s for s in sents if any(t in s for t in terms)])
                piece = keep if keep else texts[i]
                tot += min(len(piece), per)
            used += tot
        return hit / len(targets), used / len(targets)

    # ============ 主表：严格同预算 ============
    print("\n" + "-" * 96)
    print("严格同字符预算比较")
    print("-" * 96)
    print(f"{'预算':<8}{'FULL(条数)':<18}{'CUT 最优(n条)':<22}"
          f"{'KW 最优(n条)':<22}{'压缩增益'}")
    print("-" * 96)

    table = []
    for budget in [60, 120, 240, 480, 960, 1920]:
        r_full, u_full, n_full = eval_full(budget)

        best_cut = (None, -1, 0, 0)
        best_kw = (None, -1, 0, 0)
        for n_take in [2, 3, 5, 8, 10, 15, 20, 30, 50, 100, 200]:
            rc = eval_cut(budget, n_take)
            if rc and rc[0] > best_cut[1] and rc[1] <= budget * 1.02:
                best_cut = (n_take, rc[0], rc[1], budget // n_take)
            rk = eval_kw(budget, n_take)
            if rk and rk[0] > best_kw[1] and rk[1] <= budget * 1.02:
                best_kw = (n_take, rk[0], rk[1], budget // n_take)

        gain = best_cut[1] - r_full
        table.append({"budget": budget, "full_r": round(r_full, 4),
                      "full_n": n_full, "full_used": round(u_full, 1),
                      "cut_n": best_cut[0], "cut_per": best_cut[3],
                      "cut_r": round(best_cut[1], 4), "cut_used": round(best_cut[2], 1),
                      "kw_n": best_kw[0], "kw_r": round(best_kw[1], 4),
                      "gain": round(gain, 4)})
        print(f"{budget:<8}{f'{r_full:.4f}({n_full}条)':<18}"
              f"{f'{best_cut[1]:.4f}({best_cut[0]}条×{best_cut[3]}字)':<22}"
              f"{f'{best_kw[1]:.4f}({best_kw[0]}条)':<22}{gain:+.4f}")
    print("-" * 96)
    print("  注：used 为实际平均字符数，均已校验 ≤ 预算的 102%")

    # ============ 预算翻倍效应 ============
    print("\n" + "-" * 96)
    print("同样是 2 倍预算，哪种策略收益大")
    print("-" * 96)
    print(f"{'预算变化':<16}{'FULL 增益':<14}{'CUT 增益':<14}{'结论'}")
    print("-" * 96)
    for a, b in [(60, 120), (120, 240), (240, 480), (480, 960)]:
        ra = next(r for r in table if r["budget"] == a)
        rb = next(r for r in table if r["budget"] == b)
        df = rb["full_r"] - ra["full_r"]
        dc = rb["cut_r"] - ra["cut_r"]
        win = "压缩更陡" if dc > df else "完整更陡"
        print(f"{a}→{b:<10}{df:+.4f}{'':<8}{dc:+.4f}{'':<8}{win}")
    print("-" * 96)

    # ============ 结论 ============
    print("\n" + "=" * 96)
    print("结论")
    print("=" * 96)
    gains = [r["gain"] for r in table]
    print(f"  压缩增益范围: {min(gains):+.4f} ~ {max(gains):+.4f}")
    print(f"  平均增益: {np.mean(gains):+.4f}")
    ok = np.mean(gains) > 0.02
    print(f"  → {'压缩确实有效 ✓' if ok else '压缩无效 ✗'}")

    # 关键点：小预算时压缩优势最大？
    small = [r for r in table if r["budget"] <= 240]
    large = [r for r in table if r["budget"] > 240]
    print(f"\n  小预算(≤240字)平均增益: {np.mean([r['gain'] for r in small]):+.4f}")
    print(f"  大预算(>240字)平均增益: {np.mean([r['gain'] for r in large]):+.4f}")

    out = {"experiment": "E-52", "n_mem": N, "n_targets": len(targets),
           "avg_len": round(avg_len, 1), "table": table,
           "gain_min": round(float(min(gains)), 4),
           "gain_max": round(float(max(gains)), 4),
           "gain_mean": round(float(np.mean(gains)), 4),
           "compression_effective": bool(ok)}
    with open(os.path.join(RESULTS, "e52_compress_strict.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e52_compress_strict.json')}")


if __name__ == "__main__":
    main()
