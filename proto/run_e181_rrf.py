"""
E-181 多查询扩展 + RRF 融合（标准 IR 方法）

E-180 的关键数据：
  单跳失败中 88.2% 的 evidence 余弦排名在 11-200 之间
  → 证据"有点相关"但排不进前 10
  → 这是典型的「单一查询信号不足」问题

标准解法：多查询扩展 + Reciprocal Rank Fusion
  ① 用多个查询变体分别检索
  ② 按 RRF 合并排名（不依赖分数尺度，只看名次）

查询变体（机械生成，不用 LLM，先测机制是否有效）：
  Q0 原问题
  Q1 实词串联（去掉停用词，只留内容词）
  Q2 每 3 个实词组成一段（模拟子问题）
  Q3 原问题 + 最强实体词重复（强调主体）

RRF 公式：score(d) = Σ 1/(k + rank_i(d))，k=60（标准值）

注意：本实验用真实 Embedder 和真实向量矩阵，
      不手写 store.search 的打分逻辑（E-173 教训）。
"""
from __future__ import annotations

import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e181")
os.makedirs(TASK, exist_ok=True)
RESULTS = os.path.join(_HERE, "results")
DATA = os.path.join(_HERE, "work", "e165", "locomo10.json")

K = 10
N_CONV = 3
RRF_K = 60

STOP = set("""
a an the and or but if then than that this these those
is are was were be been being am do does did doing done
have has had having will would shall should can could may might must
i you he she it we they me him her us them my your his its our their
of in on at to from by with without for about as into over under
what which who whom whose when where why how
not no yes so such very just also too only more most much many
there here all any some each every both few other another
""".split())


def content_words(q):
    return [w for w in re.findall(r"[a-z]+|\d+", (q or "").lower())
            if len(w) >= 3 and w not in STOP]


def make_variants(q):
    """机械生成查询变体"""
    out = [q]
    cw = content_words(q)

    # V1 实词串联
    if cw:
        out.append(" ".join(cw))

    # V2 每 3 个实词一段（模拟子问题）
    if len(cw) >= 6:
        for i in range(0, len(cw), 3):
            seg = " ".join(cw[i:i + 3])
            if len(seg) >= 6:
                out.append(seg)

    # V3 最强实体强调（第一个实词重复 3 次）
    if cw:
        out.append((cw[0] + " ") * 3 + q)

    # 去重保序
    seen, res = set(), []
    for v in out:
        v = v.strip()
        if v and v not in seen:
            seen.add(v)
            res.append(v)
    return res[:6]


def main():
    from store_v2 import MemoryStore
    from store_v1 import Embedder
    import numpy as np

    data = json.load(open(DATA, encoding="utf-8"))

    print("=" * 94)
    print("E-181 多查询扩展 + RRF 融合")
    print("=" * 94)
    print(f"\nRRF k={RRF_K}，每个查询最多 6 个变体\n")

    emb = Embedder.get()

    total = 0
    hit_base = 0     # 基线：真实 store.search
    hit_rrf = 0      # RRF 融合
    hit_rrf_only = 0

    # 逐对话
    for ci, conv in enumerate(data[:N_CONV]):
        sid = conv.get("sample_id")
        qa = conv.get("qa") or []
        c = conv.get("conversation") or {}

        sessions = []
        j = 1
        while f"session_{j}" in c:
            if c[f"session_{j}"]:
                sessions.append((j, c.get(f"session_{j}_date_time", ""),
                                 c[f"session_{j}"]))
            j += 1

        db = os.path.join(TASK, f"{sid}.db")
        for suf in ("", "-wal", "-shm"):
            p = db + suf
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass
        st = MemoryStore(db)

        mark_of = {}
        for snum, dt, turns in sessions:
            for ti, turn in enumerate(turns, start=1):
                spk = turn.get("speaker") or ""
                txt = turn.get("text") or turn.get("content") or ""
                txt = re.sub(r"\s+", " ", str(txt)).strip()
                if len(txt) < 5:
                    continue
                full = f"[{dt}] {spk}: {txt}" if dt else f"{spk}: {txt}"
                try:
                    ep = st.add(full[:800], resolve_conflict=False)
                    mark_of[ep.ep_id] = f"D{snum}:{ti}"
                except Exception:
                    pass

        # 向量矩阵（真实）
        st._load_vectors(include_invalid=False)
        norms = np.linalg.norm(st._vec, axis=1)
        norms[norms == 0] = 1.0

        for q in qa:
            question = q.get("question") or ""
            ev = q.get("evidence") or []
            if not isinstance(ev, list):
                ev = [ev]
            ev = [str(e).strip() for e in ev if e]
            if not question or not ev:
                continue

            total += 1

            # ---- 基线：真实 store.search ----
            res0 = st.search(question, top=K)
            got0 = {mark_of.get(t[0].ep_id) for t in res0}
            got0.discard(None)
            h0 = any(e in got0 for e in ev)
            hit_base += 1 if h0 else 0

            # ---- RRF 融合 ----
            variants = make_variants(question)
            rrf = {}
            for vi, v in enumerate(variants):
                nv = emb.encode(v)[0]
                sims = (st._vec @ nv) / norms / (
                    float(np.linalg.norm(nv)) or 1.0)
                # 取该变体的 top-30
                kk = min(30, len(sims))
                topi = np.argpartition(-sims, kk - 1)[:kk]
                topi = topi[np.argsort(-sims[topi])]
                for rank, i in enumerate(topi, start=1):
                    eid = st._vec_ids[int(i)]
                    rrf[eid] = rrf.get(eid, 0.0) + 1.0 / (RRF_K + rank)

            order = sorted(rrf, key=lambda k: -rrf[k])[:K]
            got1 = {mark_of.get(e) for e in order}
            got1.discard(None)
            h1 = any(e in got1 for e in ev)
            hit_rrf += 1 if h1 else 0
            if h1 and not h0:
                hit_rrf_only += 1

        st.con.close()
        print(f"  [{ci}] {sid} 完成（累计 {total} 题，"
              f"基线 {hit_base}，RRF {hit_rrf}）")

    # ---------- 汇总 ----------
    print("\n" + "=" * 94)
    print("汇总")
    print("=" * 94)

    r_base = hit_base / max(1, total)
    r_rrf = hit_rrf / max(1, total)

    print(f"\n  题数: {total}")
    print(f"  基线（真实 store.search）: {hit_base}/{total} = {r_base:.4f}")
    print(f"  RRF 融合（多查询扩展）:    {hit_rrf}/{total} = {r_rrf:.4f}")
    print(f"  差值: {r_rrf - r_base:+.4f}")
    print(f"  仅 RRF 救回: {hit_rrf_only} 题")

    print("\n" + "=" * 94)
    print("结论")
    print("=" * 94)

    if r_rrf > r_base + 0.02:
        print(f"\n  → ✅ 多查询扩展 + RRF 有效（+{r_rrf-r_base:.4f}）")
        print(f"  → 说明 E-180 里的「语义排名 11-200」问题可以靠多路召回解决")
        print(f"  → 下一步：用 LLM 生成更好的查询变体，替代机械变体")
    elif abs(r_rrf - r_base) <= 0.02:
        print(f"\n  → 多查询扩展效果不显著（{r_rrf-r_base:+.4f}）")
        print(f"  → 机械生成的变体不够，需要 LLM 生成语义改写")
    else:
        print(f"\n  → ❌ RRF 反而更差（{r_rrf-r_base:+.4f}）")
        print(f"  → 融合引入了噪声")

    out = {"experiment": "E-181", "n": total, "k": K,
           "baseline": round(r_base, 4),
           "rrf": round(r_rrf, 4),
           "delta": round(r_rrf - r_base, 4),
           "rrf_only_rescued": hit_rrf_only,
           "n_variants_max": 6}
    with open(os.path.join(RESULTS, "e181_rrf.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e181_rrf.json')}")


if __name__ == "__main__":
    main()
