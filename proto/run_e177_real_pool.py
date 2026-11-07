"""
E-177 用真实 store.search 测候选池参数（sem_pool / like_pool）

E-173 教训：手写复现与真实代码有系统性偏差（0/5 一致）。
E-171/E-172/E-176 的手写结论全部作废。

本实验改用真实 store.search，只是传不同参数：
  sem_pool   语义候选池（0 = 默认 top*4 = 40）
  like_pool  LIKE 候选池（0 = 默认 top*2 = 20）

对比：
  S1  默认（40 / 20）
  S2  语义池放大 100
  S3  语义池缩小 20
  S4  LIKE 池放大 60
  S5  LIKE 池缩小 10
  S6  LIKE 池不限
"""
from __future__ import annotations

import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e177")
os.makedirs(TASK, exist_ok=True)
RESULTS = os.path.join(_HERE, "results")
DATA = os.path.join(_HERE, "work", "e165", "locomo10.json")

K = 10
N_CONV = 3


def main():
    from store_v2 import MemoryStore

    data = json.load(open(DATA, encoding="utf-8"))

    print("=" * 94)
    print("E-177 真实 store.search 的候选池参数实验")
    print("=" * 94)

    # 建库
    all_data = []
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
        all_data.append((sid, db, qa, mark_of))

    print(f"\n库已建好（{N_CONV} 个对话，共 "
          f"{sum(len(m) for _,_,_,m in all_data)} 条记忆）\n")

    def measure(label, **kw):
        hit = total = 0
        for sid, db, qa, mark_of in all_data:
            st = MemoryStore(db)
            for q in qa:
                question = q.get("question") or ""
                ev = q.get("evidence") or []
                if not isinstance(ev, list):
                    ev = [ev]
                ev = [str(e).strip() for e in ev if e]
                if not question or not ev:
                    continue
                res = st.search(question, top=K, **kw)
                got = {mark_of.get(t[0].ep_id) for t in res}
                got.discard(None)
                total += 1
                hit += 1 if any(e in got for e in ev) else 0
            st.con.close()
        rate = hit / max(1, total)
        print(f"  {label:<36}{hit}/{total} = {rate:.4f}")
        return rate

    print(f"{'配置':<38}{'Recall'}")
    print("-" * 94)

    res = {}
    res["default"] = measure("S1 默认（sem=40, like=20）")
    res["sem100"] = measure("S2 语义池 100", sem_pool=100)
    res["sem20"] = measure("S3 语义池 20", sem_pool=20)
    res["like60"] = measure("S4 LIKE 池 60", like_pool=60)
    res["like10"] = measure("S5 LIKE 池 10", like_pool=10)
    res["likehuge"] = measure("S6 LIKE 不限", like_pool=999999)

    # ---------- 结论 ----------
    print("\n" + "=" * 94)
    print("结论")
    print("=" * 94)

    base = res["default"]
    best = max(res, key=lambda k: res[k])
    print(f"\n  默认: {base:.4f}")
    print(f"  最优: {best} = {res[best]:.4f}  （{res[best]-base:+.4f}）")

    print(f"\n  语义池的影响:")
    print(f"    20  → {res['sem20']:.4f}  ({res['sem20']-base:+.4f})")
    print(f"    100 → {res['sem100']:.4f}  ({res['sem100']-base:+.4f})")
    print(f"  LIKE 池的影响:")
    print(f"    10  → {res['like10']:.4f}  ({res['like10']-base:+.4f})")
    print(f"    60  → {res['like60']:.4f}  ({res['like60']-base:+.4f})")
    print(f"    不限→ {res['likehuge']:.4f}  ({res['likehuge']-base:+.4f})")

    max_delta = max(abs(v - base) for v in res.values())
    if max_delta > 0.02:
        print(f"\n  → 候选池大小有显著影响（最大变化 {max_delta:.4f}）")
    else:
        print(f"\n  → 候选池大小影响不显著（最大变化 {max_delta:.4f}，<2pp）")
        print(f"  → 说明当前的 top*4 / top*2 设置已经够用，不是瓶颈")

    out = {"experiment": "E-177", "n_conv": N_CONV,
           "results": {k: round(v, 4) for k, v in res.items()},
           "best": best}
    with open(os.path.join(RESULTS, "e177_real_pool.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e177_real_pool.json')}")


if __name__ == "__main__":
    main()
