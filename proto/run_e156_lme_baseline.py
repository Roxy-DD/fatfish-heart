"""
E-156 LongMemEval 适配层修正版 + 基线对照

E-155 的问题：
  · single-session-assistant 0/3 —— 只抽了 User 的话，丢掉 Assistant
  · 没有基线对照，0.4643 无法解读

本实验修正：
  ① 适配层同时写入 User 和 Assistant 的内容（真实对话记忆应该两条都记）
  ② 加三个基线对照：
     B1 随机检索（下界）
     B2 BM25-only（纯关键词）
     B3 纯语义（只用向量）
     对照我们的三路组合（语义+关键词+结构化）

这样"0.4643"才有参照系。
"""
from __future__ import annotations

import json
import os
import random
import re
import sys
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e156")
os.makedirs(TASK, exist_ok=True)
RESULTS = os.path.join(_HERE, "results")

K = 10
N_QUESTIONS = 60


def fetch_rows(offset=0, length=60):
    url = ("https://datasets-server.huggingface.co/rows"
           f"?dataset=MemoryAsModality%2FLongMemEval&config=default"
           f"&split=train&offset={offset}&length={length}")
    req = urllib.request.Request(url, headers={"User-Agent": "research"})
    with urllib.request.urlopen(req, timeout=90) as r:
        d = json.loads(r.read().decode("utf-8"))
    return [row["row"] for row in d.get("rows", [])]


def split_turns(doc):
    """
    切出 User 和 Assistant 的每一轮（E-155 只切了 User，漏掉 Assistant）

    返回 [(date, role, text), ...]
    """
    out = []
    cur_date = ""
    # 按 User: 或 Assistant: 切
    for m in re.finditer(
            r"\[Date:\s*([^\]]+)\]|(?:^|\n)(User|Assistant):\s*",
            doc):
        pass

    # 更稳的做法：按行扫描
    cur_date = ""
    cur_role = ""
    buf = []
    for line in doc.split("\n"):
        dm = re.match(r"\[Date:\s*([^\]]+)\]", line.strip())
        if dm:
            cur_date = dm.group(1).strip()
            continue
        rm = re.match(r"(User|Assistant):\s*(.*)", line)
        if rm:
            # 结束上一轮
            if cur_role and buf:
                txt = re.sub(r"\s+", " ", " ".join(buf)).strip()
                if len(txt) >= 15:
                    out.append((cur_date, cur_role, txt[:800]))
            cur_role = rm.group(1)
            buf = [rm.group(2)] if rm.group(2).strip() else []
        else:
            if cur_role:
                buf.append(line)
    if cur_role and buf:
        txt = re.sub(r"\s+", " ", " ".join(buf)).strip()
        if len(txt) >= 15:
            out.append((cur_date, cur_role, txt[:800]))
    return out


def answer_in_text(answer, text):
    if not answer or not text:
        return False
    a = re.sub(r"[^\w\s]", " ", answer.lower())
    t = re.sub(r"[^\w\s]", " ", text.lower())
    words = [w for w in a.split() if len(w) >= 3]
    if not words:
        return a.strip() in t
    hit = sum(1 for w in words if w in t)
    return hit / len(words) >= 0.6


def main():
    from store_v2 import MemoryStore
    from store_v1 import Embedder

    print("=" * 94)
    print("E-156 LongMemEval 适配层修正 + 基线对照")
    print("=" * 94)

    print(f"\n拉取 {N_QUESTIONS} 题…")
    try:
        rows = fetch_rows(0, N_QUESTIONS)
    except Exception as e:
        print(f"❌ {e}")
        return
    print(f"  拿到 {len(rows)} 题")

    # 只评测非 abstention 的
    tests = [r for r in rows if not r.get("is_abstention")]
    print(f"  非 abstention: {len(tests)} 题")

    results = {}
    details = []
    random.seed(42)

    print("\n" + "-" * 94)
    print("逐题：三路组合 vs 三个基线")
    print("-" * 94)
    print(f"{'#':<4}{'题型':<22}{'记忆':<6}{'组合':<6}{'纯语义':<8}"
          f"{'BM25':<7}{'随机':<7}")
    print("-" * 94)

    for i, row in enumerate(tests):
        q = row.get("question") or ""
        ans = row.get("answer") or ""
        qtype = row.get("question_type") or ""
        docs = row.get("documents") or []

        db_i = os.path.join(TASK, f"q{i:03d}.db")
        for suf in ("", "-wal", "-shm"):
            p = db_i + suf
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass
        st = MemoryStore(db_i)

        # 修正：User 和 Assistant 都写入
        items = []
        for doc in docs:
            for date, role, txt in split_turns(doc):
                items.append((role, txt))
                try:
                    st.add(txt, resolve_conflict=False)
                except Exception:
                    pass

        if not items:
            st.con.close()
            continue

        all_texts = [t for _, t in items]

        # ---- 我们的三路组合 ----
        res = st.search(q, top=K)
        combo_texts = [ep.raw_text for ep, *_ in res]
        combo_ok = any(answer_in_text(ans, t) for t in combo_texts)

        # ---- 纯语义（只看向量）----
        import numpy as np
        st._load_vectors(include_invalid=False)
        if st._vec is not None and len(st._vec):
            nv = Embedder.get().encode(q)[0]
            norms = np.linalg.norm(st._vec, axis=1)
            norms[norms == 0] = 1.0
            sims = (st._vec @ nv) / norms
            top_idx = np.argsort(-sims)[:K]
            sem_texts = [st.get(st._vec_ids[int(j)]).raw_text
                         for j in top_idx if st.get(st._vec_ids[int(j)])]
            sem_ok = any(answer_in_text(ans, t) for t in sem_texts)
        else:
            sem_ok = False

        # ---- BM25 关键词 ----
        terms = [w for w in re.findall(r"\w{3,}", q.lower())]
        bm25_ok = False
        if terms:
            scored = []
            for t in all_texts:
                tl = t.lower()
                s = sum(tl.count(w) for w in terms)
                if s > 0:
                    scored.append((s, t))
            scored.sort(key=lambda x: -x[0])
            bm25_texts = [t for _, t in scored[:K]]
            bm25_ok = any(answer_in_text(ans, t) for t in bm25_texts)

        # ---- 随机 ----
        rnd = random.sample(all_texts, min(K, len(all_texts)))
        rnd_ok = any(answer_in_text(ans, t) for t in rnd)

        print(f"{i:<4}{qtype[:20]:<22}{len(items):<6}"
              f"{'✅' if combo_ok else '❌':<6}"
              f"{'✅' if sem_ok else '❌':<8}"
              f"{'✅' if bm25_ok else '❌':<7}"
              f"{'✅' if rnd_ok else '❌':<7}")

        details.append({
            "i": i, "qtype": qtype, "n_mem": len(items),
            "combo": combo_ok, "semantic": sem_ok,
            "bm25": bm25_ok, "random": rnd_ok,
        })
        st.con.close()

    # ---------- 汇总 ----------
    print("\n" + "=" * 94)
    print("汇总")
    print("=" * 94)

    n = len(details)
    if n == 0:
        print("  无有效题目")
        return

    def rate(key):
        return sum(1 for d in details if d[key]) / n

    methods = [
        ("我们的三路组合", "combo"),
        ("纯语义（向量）", "semantic"),
        ("BM25 关键词", "bm25"),
        ("随机（下界）", "random"),
    ]
    print(f"\n  {'方法':<20}{'可及率':<12}{'vs 随机'}")
    print("  " + "-" * 60)
    base = rate("random")
    res_summary = {}
    for name, key in methods:
        r = rate(key)
        res_summary[key] = round(r, 4)
        print(f"  {name:<20}{r:<12.4f}{r - base:+.4f}")

    # 分题型（只看组合）
    from collections import defaultdict
    by = defaultdict(lambda: [0, 0])
    for d in details:
        by[d["qtype"]][1] += 1
        by[d["qtype"]][0] += 1 if d["combo"] else 0

    print(f"\n  三路组合分题型：")
    print(f"  {'题型':<26}{'可及/总':<12}{'可及率'}")
    print("  " + "-" * 60)
    for t in sorted(by, key=lambda x: -by[x][1]):
        h, tot = by[t]
        print(f"  {t[:24]:<26}{f'{h}/{tot}':<12}{h/tot:.4f}")

    # ---------- 结论 ----------
    print("\n" + "=" * 94)
    print("结论")
    print("=" * 94)

    combo = res_summary["combo"]
    print(f"\n  题数: {n}")
    print(f"  三路组合: {combo:.4f}")
    print(f"  纯语义:   {res_summary['semantic']:.4f}")
    print(f"  BM25:     {res_summary['bm25']:.4f}")
    print(f"  随机:     {res_summary['random']:.4f}")

    best_single = max(res_summary["semantic"], res_summary["bm25"])
    print(f"\n  组合 vs 最好单路: {combo - best_single:+.4f}")
    print(f"  组合 vs 随机:     {combo - base:+.4f}")

    if combo > best_single:
        print(f"\n  → ✅ 三路组合优于任一单路，验证了多路融合的价值")
    elif abs(combo - best_single) < 0.02:
        print(f"\n  → 三路组合与最好单路持平")
    else:
        print(f"\n  → ⚠ 组合低于最好单路，融合策略需要调")

    out = {"experiment": "E-156", "n": n, "k": K,
           "methods": res_summary,
           "by_type": {t: {"hit": v[0], "total": v[1]}
                       for t, v in by.items()},
           "details": details}
    with open(os.path.join(RESULTS, "e156_lme_baseline.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e156_lme_baseline.json')}")


if __name__ == "__main__":
    main()
