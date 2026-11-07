"""
E-186b 最终正确评分：按对话隔离的 mark 映射 + 文本匹配

之前所有评分都错在 mark 映射：
  · evidence 是 D{session}:{turn}，每个对话都从 session_1 开始编号
  · 跨对话遍历时，后一个对话的 session_1 覆盖前一个

正确做法：
  ① 按对话隔离，每个对话单独建 mark -> text 映射
  ② evidence 的 D 编号只在「当前对话」内有效
  ③ 判断 pick 命中的方式：pick 的文本 == evidence 对应的文本

关键：recoverable.json 里没存对话来源，
      所以需要重新生成，带上 sid 和 mark。
"""
from __future__ import annotations

import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e186b")
RESULTS = os.path.join(_HERE, "results")
DATA = os.path.join(_HERE, "work", "e165", "locomo10.json")

K = 10
POOL = 30
N_CONV = 2


def norm(s):
    return re.sub(r"\s+", "", (s or "").lower())


def main():
    from store_v2 import MemoryStore

    data = json.load(open(DATA, encoding="utf-8"))
    picks = {r["cid"]: r for r in
             json.load(open(os.path.join(TASK, "picks.json"), encoding="utf-8"))}

    # 重建 recoverable，这次每个条目带 sid，并按 sid 隔离 mark
    # 但 picks.json 的 cid 是 E-186b 生成的顺序，我需要精确复现那个顺序
    # E-186b 的顺序：遍历 data[:N_CONV]，每个 conv 内遍历 qa，只收可救回题

    recoverable = []
    base_hit = 0
    total = 0

    for conv in data[:N_CONV]:
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

        db = os.path.join(TASK, f"{sid}_final.db")
        for suf in ("", "-wal", "-shm"):
            p = db + suf
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass
        st = MemoryStore(db)

        mark_of = {}
        text_of_mark = {}
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
                    mark = f"D{snum}:{ti}"
                    mark_of[ep.ep_id] = mark
                    text_of_mark[mark] = full
                except Exception:
                    pass

        for q in qa:
            question = q.get("question") or ""
            ev = q.get("evidence") or []
            if not isinstance(ev, list):
                ev = [ev]
            ev = [str(e).strip() for e in ev if e]
            if not question or not ev:
                continue

            total += 1
            res = st.search(question, top=POOL)
            cands = []
            for tup in res:
                ep = tup[0]
                cands.append({
                    "ep_id": ep.ep_id,
                    "mark": mark_of.get(ep.ep_id),
                    "text": ep.raw_text[:400],
                })

            top10_marks = {c2["mark"] for c2 in cands[:K] if c2["mark"]}
            if any(e in top10_marks for e in ev):
                base_hit += 1
                continue

            pool_marks = {c2["mark"] for c2 in cands if c2["mark"]}
            if not any(e in pool_marks for e in ev):
                continue

            # 可救回题：记录 ev 对应的文本（按对话内 mark）
            ev_texts = [text_of_mark.get(e, "") for e in ev]
            recoverable.append({
                "sid": sid,
                "question": question,
                "ev": ev,
                "ev_texts": ev_texts,
                "candidates": cands,
            })

        st.con.close()

    print("=" * 94)
    print("E-186b 最终正确评分")
    print("=" * 94)
    print(f"\n总 {total} 题，基线命中 {base_hit}，可救回 {len(recoverable)}")

    # 现在按顺序匹配 picks（picks.json 的 cid 顺序 = recoverable 的生成顺序）
    hit = 0
    n = 0
    details = []

    for i, rec in enumerate(recoverable):
        cid = f"Q{i:02d}"
        pk = picks.get(cid, {}).get("picks") or []

        # pick 的文本
        ep_to_text = {c["ep_id"]: c["text"] for c in rec["candidates"]}
        pick_texts = [ep_to_text.get(p, "") for p in pk]

        # 判断：pick 文本是否 == evidence 文本
        n += 1
        ok = False
        for pt in pick_texts:
            pt_n = norm(pt)
            for et in rec["ev_texts"]:
                et_n = norm(et)
                if not pt_n or not et_n:
                    continue
                if pt_n == et_n:
                    ok = True
                    break
                # 子串（去时间戳前缀后）
                if len(pt_n) > 30 and len(et_n) > 30:
                    if pt_n[-60:] == et_n[-60:] or et_n[-60:] == pt_n[-60:]:
                        ok = True
                        break
            if ok:
                break

        hit += 1 if ok else 0
        if not ok and len(details) < 5:
            details.append({
                "cid": cid, "q": rec["question"][:50],
                "ev": rec["ev"],
                "ev_text": rec["ev_texts"][0][-60:] if rec["ev_texts"] else "",
                "pick_text": pick_texts[0][-60:] if pick_texts else "",
            })

    rate = hit / max(1, n)
    print(f"\n  精排命中: {hit}/{n} = {rate:.4f}")
    print(f"  整体: 基线 {base_hit/total:.4f} → 精排后 "
          f"{(base_hit + hit)/total:.4f}  (+{hit/total:.4f})")

    print(f"\n  未命中案例:")
    for d in details:
        print(f"\n    [{d['cid']}] {d['q']}")
        print(f"      ev 尾: ...{d['ev_text']}")
        print(f"      pick尾: ...{d['pick_text']}")

    out = {"experiment": "E-186b-final2", "n": n, "hit": hit,
           "rate": round(rate, 4),
           "baseline": round(base_hit / total, 4),
           "overall_gain": round(hit / total, 4)}
    with open(os.path.join(RESULTS, "e186b_rerank_final2.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e186b_rerank_final2.json')}")


if __name__ == "__main__":
    main()
