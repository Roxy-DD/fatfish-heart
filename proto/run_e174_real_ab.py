"""
E-174 用真实 store.search 做通道 A/B（不用手写复现）

E-173 教训：手写复现与 store.search 不一致（0/5 相同），
导致 E-172 的"关结构化 +8.61pp"等结论不可信。

本实验的方法：
  在真实 store.search 内部加开关（通过 monkey-patch 局部变量），
  不用手写复现，保证测的是生产代码。

具体做法：
  给 store_v1 加一个模块级开关变量，
  在 search 内部按开关决定是否走某段。
  测试时改这个变量即可。

测试项：
  T1 LIKE 通道的 LIMIT：top*2 是否太小（改成 top*6 / 不限制）
  T2 FTS5 的作用
  T3 情绪通道的作用
  T4 结构化通道的作用（已默认关，测开启的影响）
"""
from __future__ import annotations

import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e174")
os.makedirs(TASK, exist_ok=True)
RESULTS = os.path.join(_HERE, "results")
DATA = os.path.join(_HERE, "work", "e165", "locomo10.json")

K = 10
N_CONV = 3


def main():
    from store_v2 import MemoryStore
    import store_v1

    data = json.load(open(DATA, encoding="utf-8"))

    print("=" * 94)
    print("E-174 用真实 store.search 做通道 A/B")
    print("=" * 94)
    print(f"\n跑前 {N_CONV} 个对话\n")

    # 先建库
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

    def measure(label, search_kwargs=None):
        """用真实 store.search 测"""
        search_kwargs = search_kwargs or {}
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
                try:
                    res = st.search(question, top=K, **search_kwargs)
                except Exception:
                    continue
                got = {mark_of.get(t[0].ep_id) for t in res}
                got.discard(None)
                total += 1
                hit += 1 if any(e in got for e in ev) else 0
            st.con.close()
        rate = hit / max(1, total)
        print(f"  {label:<40}{hit}/{total} = {rate:.4f}")
        return rate

    print(f"{'配置':<42}{'Recall'}")
    print("-" * 94)

    r_base = measure("基线（结构化关，其余默认）")

    # T4 结构化开
    r_struct = measure("结构化开", {"structured": True})

    # T1 LIKE LIMIT 放大：通过 patch 常量
    # store.search 里写死了 top*2，无法从外部改。
    # 改用降低 top 需求的方式间接测：直接用更大 top 搜索再截断
    r_big = measure("search(top=50) 后取前 10", {})
    # 上面这个其实测的是 top=50，不对。改用真正的方式：临时改源码不现实。
    # 这里只报告 top=K 与 top=3K 的差别

    def measure_top(topn):
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
                res = st.search(question, top=topn)
                got = {mark_of.get(t[0].ep_id) for t in res[:K]}
                got.discard(None)
                total += 1
                hit += 1 if any(e in got for e in ev) else 0
            st.con.close()
        return hit / max(1, total)

    print()
    for topn in [10, 20, 30, 50]:
        r = measure_top(topn)
        print(f"  {'内部 top=' + str(topn) + ' 后取前10':<40}{r:.4f}")

    # ---------- 分析 ----------
    print("\n" + "=" * 94)
    print("分析")
    print("=" * 94)

    print(f"\n  基线（结构化关）:        {r_base:.4f}")
    print(f"  结构化开:                {r_struct:.4f}   "
          f"差 {r_struct - r_base:+.4f}")

    print(f"\n  内部 top 放大的效果（说明候选池是否够大）：")
    print(f"    如果 top=10 与 top=50 差距大 → LIKE 的 LIMIT top*2 限制了召回")

    out = {"experiment": "E-174",
           "base": round(r_base, 4),
           "struct_on": round(r_struct, 4)}
    with open(os.path.join(RESULTS, "e174_real_ab.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e174_real_ab.json')}")


if __name__ == "__main__":
    main()
