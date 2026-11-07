"""
E-186b 真正正确的评分

之前所有失败都源于"重新建库导致 ep_id 变化"。
正确做法：
  ① 直接用 E-186b 的 recoverable.json（ep_id 与 picks.json 配套）
  ② 从原始 locomo 数据重建 mark -> 文本（按对话隔离）
  ③ 判断：picks 的 ep_id 对应的 candidate text，是否与 evidence 的文本一致
"""
from __future__ import annotations

import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
TASK = os.path.join(_HERE, "work", "e186b")
RESULTS = os.path.join(_HERE, "results")
DATA = os.path.join(_HERE, "work", "e165", "locomo10.json")


def norm(s):
    return re.sub(r"\s+", "", (s or "").lower())


def main():
    # 直接用 E-186b 的文件（ep_id 配套）
    recoverable = json.load(open(os.path.join(TASK, "recoverable.json"),
                                 encoding="utf-8"))
    picks = json.load(open(os.path.join(TASK, "picks.json"),
                           encoding="utf-8"))
    data = json.load(open(DATA, encoding="utf-8"))

    print("=" * 94)
    print("E-186b 真正正确评分（直接用配套 ep_id）")
    print("=" * 94)

    # 先看 recoverable 里有没有 mark 信息
    r0 = recoverable[0]
    print(f"\nrecoverable[0] 字段: {list(r0.keys())}")
    print(f"  candidates[0] 字段: {list(r0['candidates'][0].keys())}")

    # recoverable 里没存 mark，只有 text 和 evidence(mark 格式)
    # 需要从原始数据找 evidence mark 的文本
    # 但 recoverable 没存 sid，无法知道是哪个对话

    # 关键判断：evidence 的文本，一定在 candidates 里（因为"可救回"=证据在pool里）
    # 但 recoverable 没存 mark，所以无法直接对应

    # 换个思路：直接用 question 文本反查原始数据
    # 从原始数据建立 question -> evidence文本 的映射
    q_to_ev_text = {}
    for conv in data[:2]:
        c = conv.get("conversation") or {}
        # 建 mark -> text
        mark_text = {}
        j = 1
        while f"session_{j}" in c:
            for ti, turn in enumerate(c[f"session_{j}"] or [], start=1):
                spk = turn.get("speaker") or ""
                txt = turn.get("text") or turn.get("content") or ""
                txt = re.sub(r"\s+", " ", str(txt)).strip()
                if len(txt) >= 5:
                    full = f"{spk}: {txt}"
                    mark_text[f"D{j}:{ti}"] = full
            j += 1

        for q in conv.get("qa") or []:
            question = q.get("question") or ""
            ev = q.get("evidence") or []
            if not isinstance(ev, list):
                ev = [ev]
            ev = [str(e).strip() for e in ev if e]
            ev_texts = [mark_text.get(e, "") for e in ev]
            q_to_ev_text[question] = ev_texts

    # 评分：recoverable 里的 question -> evidence 文本
    # picks 的 ep_id -> candidate text（在 recoverable 里配套）
    hit = 0
    n = 0
    details = []

    for i, rec in enumerate(recoverable):
        cid = f"Q{i:02d}"
        q = rec["question"]
        pk = picks[i].get("picks") if i < len(picks) else []

        ev_texts = q_to_ev_text.get(q, [])
        if not ev_texts:
            continue

        ep_to_text = {c["ep_id"]: c["text"] for c in rec["candidates"]}
        pick_texts = [ep_to_text.get(p, "") for p in pk]

        n += 1
        ok = False
        for pt in pick_texts:
            pt_n = norm(pt)
            for et in ev_texts:
                et_n = norm(et)
                if not pt_n or not et_n:
                    continue
                if pt_n == et_n:
                    ok = True
                    break
                # 子串（去时间戳，比较后 60 字符）
                if len(pt_n) > 50 and len(et_n) > 50:
                    if pt_n[-60:] == et_n[-60:]:
                        ok = True
                        break
            if ok:
                break

        hit += 1 if ok else 0
        if not ok and len(details) < 8:
            details.append({
                "cid": cid, "q": q[:50],
                "ev_text": (ev_texts[0] or "")[-60:],
                "pick_text": (pick_texts[0] or "")[-60:] if pick_texts else "",
            })

    rate = hit / max(1, n)
    print(f"\n  精排命中: {hit}/{n} = {rate:.4f}")

    # 基线（E-186b prep 已算）: 前2对话 114/302
    base = 114
    total = 302
    print(f"\n  基线: {base}/{total} = {base/total:.4f}")
    print(f"  精排后: ({base}+{hit})/{total} = {(base+hit)/total:.4f}  "
          f"(+{hit/total:.4f})")

    print(f"\n  未命中案例:")
    for d in details:
        print(f"\n    [{d['cid']}] {d['q']}")
        print(f"      ev 尾:  ...{d['ev_text']}")
        print(f"      pick尾: ...{d['pick_text']}")

    out = {"experiment": "E-186b-truth", "n": n, "hit": hit,
           "rate": round(rate, 4),
           "baseline": round(base / total, 4),
           "overall_gain": round(hit / total, 4),
           "details": details}
    with open(os.path.join(RESULTS, "e186b_rerank_truth.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e186b_rerank_truth.json')}")


if __name__ == "__main__":
    main()
