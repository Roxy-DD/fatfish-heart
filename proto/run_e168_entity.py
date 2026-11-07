"""
E-168 量化：补英文结构化抽取能带来多少提升

E-167 发现 extract_elements 在英文上失效，
而它是检索的结构化通道（命中 person/time/location 加 0.8 分）。

E-166 结果：多跳 0.3913 最低，时间 0.6075。

本实验先量化"如果英文也能抽到实体，能覆盖多少题"：
  ① LoCoMo 的 QA 里，多少题涉及人名？
  ② 多少题涉及时间表达？
  ③ 当前失败的题里，多少能靠实体匹配救回？

方法：
  用 LoCoMo 的真实数据，统计：
    · 问题里的人名（speaker_a/speaker_b 出现次数）
    · 问题里的时间词
    · 这些题当前的召回情况
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e168")
os.makedirs(TASK, exist_ok=True)
RESULTS = os.path.join(_HERE, "results")
DATA = os.path.join(_HERE, "work", "e165", "locomo10.json")

K = 10
CAT = {"1": "单跳", "2": "时间", "3": "多跳", "4": "常识", "5": "对抗"}

# 英文时间表达
EN_TIME = re.compile(
    r"\b(when|what time|which year|what date|how long|how many days|"
    r"before|after|during|ago|last|next|first|then)\b", re.I)
# 月份
MONTHS = re.compile(
    r"\b(january|february|march|april|may|june|july|august|"
    r"september|october|november|december|monday|tuesday|wednesday|"
    r"thursday|friday|saturday|sunday|yesterday|today|tomorrow)\b", re.I)


def main():
    from store_v2 import MemoryStore

    data = json.load(open(DATA, encoding="utf-8"))

    print("=" * 94)
    print("E-168 量化：英文结构化抽取的潜在收益")
    print("=" * 94)

    total_q = 0
    name_q = 0
    time_q = 0
    both_q = 0
    name_fail = 0
    time_fail = 0

    by_cat_name = Counter()
    by_cat_time = Counter()

    for ci, conv in enumerate(data[:3]):   # 先跑 3 个对话，够看趋势
        sid = conv.get("sample_id")
        qa = conv.get("qa") or []
        c = conv.get("conversation") or {}
        spk_a = c.get("speaker_a") or ""
        spk_b = c.get("speaker_b") or ""

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

        print(f"\n  [{ci}] {sid}  说话人: {spk_a} / {spk_b}")

        for q in qa:
            question = q.get("question") or ""
            cat = str(q.get("category"))
            ev = q.get("evidence") or []
            if not isinstance(ev, list):
                ev = [ev]
            ev = [str(e).strip() for e in ev if e]
            if not question or not ev:
                continue

            total_q += 1
            has_name = (spk_a and spk_a in question) or \
                       (spk_b and spk_b in question)
            has_time = bool(EN_TIME.search(question)) or \
                       bool(MONTHS.search(question))

            if has_name:
                name_q += 1
                by_cat_name[cat] += 1
            if has_time:
                time_q += 1
                by_cat_time[cat] += 1
            if has_name and has_time:
                both_q += 1

            # 当前召回状态
            res = st.search(question, top=K)
            got = {mark_of.get(t[0].ep_id) for t in res}
            got.discard(None)
            hit = any(e in got for e in ev)

            if has_name and not hit:
                name_fail += 1
            if has_time and not hit:
                time_fail += 1

        st.con.close()

    # ---------- 汇总 ----------
    print("\n" + "=" * 94)
    print(f"汇总（前 3 个对话，{total_q} 条 QA）")
    print("=" * 94)

    print(f"\n  含人名的问题: {name_q}/{total_q} = {name_q/max(1,total_q):.1%}")
    print(f"    其中当前失败: {name_fail}（{name_fail/max(1,name_q):.1%}）")
    print(f"  含时间词的问题: {time_q}/{total_q} = {time_q/max(1,total_q):.1%}")
    print(f"    其中当前失败: {time_fail}（{time_fail/max(1,time_q):.1%}）")
    print(f"  同时含人名+时间: {both_q}")

    print(f"\n  含人名的问题按类别：")
    for c in sorted(by_cat_name):
        print(f"    {CAT.get(c, c):<10}{by_cat_name[c]}")

    print(f"\n  含时间词的问题按类别：")
    for c in sorted(by_cat_time):
        print(f"    {CAT.get(c, c):<10}{by_cat_time[c]}")

    # ---------- 结论 ----------
    print("\n" + "=" * 94)
    print("结论")
    print("=" * 94)

    name_ratio = name_q / max(1, total_q)
    time_ratio = time_q / max(1, total_q)
    potential = (name_fail + time_fail) / max(1, total_q)

    print(f"\n  含人名比例: {name_ratio:.1%}")
    print(f"  含时间词比例: {time_ratio:.1%}")
    print(f"  失败但含人名或时间词: {name_fail + time_fail} 条 "
          f"（占全部 {potential:.1%}）")

    print(f"\n  判断：")
    if potential >= 0.15:
        print(f"    → ✅ 补英文结构化抽取有明确收益空间")
        print(f"      最多可能提升 {potential:.1%} 的召回（上限估计）")
        print(f"    → 值得修 extract_elements 的英文支持")
    else:
        print(f"    → 收益有限（{potential:.1%}），优先级不高")

    out = {"experiment": "E-168", "n_qa": total_q,
           "name_q": name_q, "name_fail": name_fail,
           "time_q": time_q, "time_fail": time_fail,
           "both_q": both_q,
           "by_cat_name": dict(by_cat_name),
           "by_cat_time": dict(by_cat_time),
           "potential_gain": round(potential, 4)}
    with open(os.path.join(RESULTS, "e168_entity_gain.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e168_entity_gain.json')}")


if __name__ == "__main__":
    main()
