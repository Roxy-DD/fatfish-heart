"""
E-160 LoCoMo 适配：检索召回评测（objective，不依赖 LLM 作答）

LoCoMo 结构（已确认）：
  顶层 = 单个对话
    sample_id         如 conv-26
    qa                199 条，字段 question/answer/evidence/category
    conversation      speaker_a/speaker_b + session_N_date_time + session_N
                      session_N 是轮次列表
    evidence          如 ["D1:3"] = 第 1 段对话的第 3 轮

类别（官方定义）：
  1 单跳  2 时间  3 多跳  4 常识/世界知识  5 对抗

关键优势：
  evidence 给了精确的轮次定位 → 可以做**纯检索召回**评测
  这比 LLM 作答更客观，且成本低（不用调模型）

评测协议：
  ① 把对话每轮写入记忆（带 session 编号标记）
  ② 用 question 检索 top-K
  ③ 检查 evidence 指定的轮次是否被召回
  ④ 报 Recall@K，按类别分

这是 Mem0/Zep 论文里 retrieval 部分的做法。
"""
from __future__ import annotations

import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e160")
os.makedirs(TASK, exist_ok=True)
RESULTS = os.path.join(_HERE, "results")
DATA = os.path.join(_HERE, "work", "e159", "locomo_sample.json")

K = 10


def main():
    from store_v2 import MemoryStore

    if not os.path.exists(DATA):
        print("缺少 LoCoMo 数据")
        return

    d = json.load(open(DATA, encoding="utf-8"))
    sid = d.get("sample_id")
    qa = d.get("qa") or []
    conv = d.get("conversation") or {}

    print("=" * 94)
    print("E-160 LoCoMo 检索召回评测")
    print("=" * 94)
    print(f"\n对话: {sid}")
    print(f"  speaker_a={conv.get('speaker_a')}  speaker_b={conv.get('speaker_b')}")
    print(f"  QA 数: {len(qa)}")

    # 找出所有 session
    sessions = []
    i = 1
    while True:
        key = f"session_{i}"
        dt_key = f"session_{i}_date_time"
        if key not in conv:
            break
        turns = conv[key]
        if turns:
            sessions.append((i, conv.get(dt_key, ""), turns))
        i += 1
    print(f"  有内容的 session: {len(sessions)}")

    # 建库：每轮一条记忆，带 D{session}:{turn} 标记
    db = os.path.join(TASK, "locomo.db")
    for suf in ("", "-wal", "-shm"):
        p = db + suf
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass
    st = MemoryStore(db)

    # ep_id -> "D1:3"
    mark_of = {}
    n_mem = 0
    for snum, dt, turns in sessions:
        for ti, turn in enumerate(turns, start=1):
            spk = turn.get("speaker") or ""
            txt = turn.get("text") or turn.get("content") or ""
            txt = re.sub(r"\s+", " ", str(txt)).strip()
            if len(txt) < 5:
                continue
            # 组合成一条记忆（带说话人）
            full = f"[{dt}] {spk}: {txt}" if dt else f"{spk}: {txt}"
            try:
                ep = st.add(full[:800], resolve_conflict=False)
                mark_of[ep.ep_id] = f"D{snum}:{ti}"
                n_mem += 1
            except Exception:
                pass

    print(f"  写入记忆: {n_mem} 条")

    # 评测
    from collections import defaultdict
    by_cat = defaultdict(lambda: [0, 0, 0])   # hit, total, evidence_found
    details = []

    print(f"\n{'#':<5}{'类别':<6}{'evidence':<16}{'召回?':<8}{'问题'}")
    print("-" * 94)

    for qi, q in enumerate(qa):
        question = q.get("question") or ""
        answer = q.get("answer") or ""
        ev = q.get("evidence") or []
        cat = str(q.get("category"))
        if not isinstance(ev, list):
            ev = [ev]
        ev = [str(e).strip() for e in ev if e]
        if not ev or not question:
            continue

        res = st.search(question, top=K)
        got_marks = []
        for tup in res:
            ep = tup[0]
            m = mark_of.get(ep.ep_id)
            if m:
                got_marks.append(m)

        # 命中判定：evidence 里任一条被召回
        hit = any(e in got_marks for e in ev)
        # 全命中：所有 evidence 都被召回
        all_hit = all(e in got_marks for e in ev)

        by_cat[cat][1] += 1
        by_cat[cat][0] += 1 if hit else 0
        by_cat[cat][2] += 1 if all_hit else 0

        if qi < 12:
            print(f"{qi:<5}{cat:<6}{','.join(ev)[:14]:<16}"
                  f"{'✅' if hit else '❌':<8}{question[:44]}")

        details.append({
            "i": qi, "cat": cat, "q": question, "ev": ev,
            "hit": hit, "all_hit": all_hit,
            "got_marks": got_marks[:K],
        })

    st.con.close()

    # ---------- 汇总 ----------
    print("\n" + "=" * 94)
    print("汇总")
    print("=" * 94)

    total_hit = sum(v[0] for v in by_cat.values())
    total_n = sum(v[1] for v in by_cat.values())
    total_all = sum(v[2] for v in by_cat.values())

    print(f"\n  总体召回@{K}: {total_hit}/{total_n} = "
          f"{total_hit/max(1,total_n):.4f}")
    print(f"  全证据召回@{K}: {total_all}/{total_n} = "
          f"{total_all/max(1,total_n):.4f}")

    cat_names = {"1": "单跳", "2": "时间", "3": "多跳",
                 "4": "常识", "5": "对抗"}
    print(f"\n  {'类别':<16}{'召回/总':<14}{'Recall':<12}{'全证据'}")
    print("  " + "-" * 66)
    for c in sorted(by_cat):
        h, n, a = by_cat[c]
        print(f"  {cat_names.get(c, c):<16}{f'{h}/{n}':<14}"
              f"{h/n:<12.4f}{a}")

    print(f"\n  说明：这是**检索召回**，不是答题准确率。")
    print(f"  evidence 未指定轮次被召回的比例。")

    out = {"experiment": "E-160", "sample_id": sid,
           "n_memories": n_mem, "k": K,
           "recall": round(total_hit / max(1, total_n), 4),
           "all_evidence": round(total_all / max(1, total_n), 4),
           "by_category": {c: {"hit": v[0], "total": v[1], "all": v[2]}
                           for c, v in by_cat.items()},
           "details": details}
    with open(os.path.join(RESULTS, "e160_locomo.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e160_locomo.json')}")


if __name__ == "__main__":
    main()
