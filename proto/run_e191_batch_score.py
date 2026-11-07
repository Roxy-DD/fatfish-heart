"""
E-191 评分：批量精排命中率

这次评分用「question -> evidence 文本」映射，不依赖重建库的 ep_id。
  ① 从原始 locomo 数据建 mark -> text（按对话隔离，注意跨对话不覆盖）
  ② 从 batch_tasks.json 里读 question 和 candidates
  ③ 用 picks.json 的 ep_id 找到候选文本，与 evidence 文本比对

关键修正（吸取 E-186b 的教训）：
  · 不重建库（避免 ep_id 变化）
  · mark -> text 按「对话」隔离，用 question 作为跨文件对齐的键
"""
from __future__ import annotations

import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
TASK = os.path.join(_HERE, "work", "e191")
RESULTS = os.path.join(_HERE, "results")
DATA = os.path.join(_HERE, "work", "e165", "locomo10.json")


def norm(s):
    return re.sub(r"\s+", "", (s or "").lower())


def main():
    tasks = json.load(open(os.path.join(TASK, "batch_tasks.json"),
                           encoding="utf-8"))
    pp = os.path.join(TASK, "batch_picks.json")
    if not os.path.exists(pp):
        print("缺少 batch_picks.json")
        return
    picks = {r["cid"]: r for r in json.load(open(pp, encoding="utf-8"))}

    data = json.load(open(DATA, encoding="utf-8"))

    # 建 question -> evidence 文本 映射（按对话，跨对话 question 唯一）
    q_to_ev_text = {}
    for conv in data:
        c = conv.get("conversation") or {}
        mark_text = {}
        j = 1
        while f"session_{j}" in c:
            for ti, turn in enumerate(c[f"session_{j}"] or [], start=1):
                spk = turn.get("speaker") or ""
                txt = turn.get("text") or turn.get("content") or ""
                txt = re.sub(r"\s+", " ", str(txt)).strip()
                if len(txt) >= 5:
                    mark_text[f"D{j}:{ti}"] = f"{spk}: {txt}"
            j += 1
        for q in conv.get("qa") or []:
            question = q.get("question") or ""
            ev = q.get("evidence") or []
            if not isinstance(ev, list):
                ev = [ev]
            ev = [str(e).strip() for e in ev if e]
            ev_texts = [mark_text.get(e, "") for e in ev]
            q_to_ev_text[question] = ev_texts

    print("=" * 94)
    print("E-191 批量精排命中率")
    print("=" * 94)

    hit = 0
    n = 0
    details = []

    for t in tasks:
        cid = t["cid"]
        q = t["question"]
        ev_texts = q_to_ev_text.get(q, [])
        if not ev_texts:
            continue

        pk = picks.get(cid, {}).get("picks") or []
        ep_to_text = {c["ep_id"]: c["text"] for c in t["candidates"]}
        pick_texts = [ep_to_text.get(p, "") for p in pk if ep_to_text.get(p)]

        n += 1
        ok = False
        for pt in pick_texts:
            ptn = norm(pt)
            for et in ev_texts:
                etn = norm(et)
                if not ptn or not etn:
                    continue
                if ptn == etn:
                    ok = True
                    break
                # 子串：比较去时间戳后的尾段（evidence 文本带时间戳前缀，候选文本也带）
                if len(ptn) > 40 and len(etn) > 40:
                    if ptn[-60:] == etn[-60:]:
                        ok = True
                        break
            if ok:
                break

        hit += 1 if ok else 0
        if not ok and len(details) < 8:
            details.append({
                "cid": cid, "q": q[:50],
                "ev_text": (ev_texts[0] or "")[-55:],
                "pick_text": (pick_texts[0] or "")[-55:] if pick_texts else "",
            })

    rate = hit / max(1, n)
    print(f"\n  批量精排命中: {hit}/{n} = {rate:.4f}")

    # 对比 E-186b 逐条精排 90.7%
    print(f"\n  对比：E-186b 逐条精排 = 0.9074")
    print(f"        本实验批量精排 = {rate:.4f}")

    print(f"\n  未命中案例（前 8）:")
    for d in details:
        print(f"\n    [{d['cid']}] {d['q']}")
        print(f"      ev 尾:  ...{d['ev_text']}")
        print(f"      pick尾: ...{d['pick_text']}")

    out = {"experiment": "E-191", "n": n, "hit": hit,
           "rate": round(rate, 4),
           "per_item_rate": 0.9074,
           "details": details}
    with open(os.path.join(RESULTS, "e191_batch.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e191_batch.json')}")


if __name__ == "__main__":
    main()
