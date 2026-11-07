"""
E-169 验证：结构化通道的固定加分 0.8 是否是噪声源

E-168 加了英文实体抽取后，LoCoMo 从 0.5827 掉到 0.5505（-3.2pp）。

假设：
  结构化通道固定加 0.8 分，但 LoCoMo 里 97.2% 的问题含人名
  （Caroline / Melanie），几乎每条记忆也含这两人之一。
  → 加分变成"只要提到主角就加"，对所有记忆近似等值，失去区分度
  → 反而稀释了语义通道的排序信号

验证方法：
  ① 统计：人名在 LoCoMo 语料里的出现频率（如果 >50%，就是高频词）
  ② A/B：把结构化通道关掉（改代码或传参），看指标是否回升
  ③ 如果回升 → 证明固定加分有害；如果持平 → 说明只是中性

本实验先做 ① 和 ②（② 通过 monkey-patch 实现，不改生产代码）
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e169")
os.makedirs(TASK, exist_ok=True)
RESULTS = os.path.join(_HERE, "results")
DATA = os.path.join(_HERE, "work", "e165", "locomo10.json")

K = 10
CAT = {"1": "单跳", "2": "时间", "3": "多跳", "4": "常识", "5": "对抗"}


def main():
    from store_v2 import MemoryStore
    import store_v1

    data = json.load(open(DATA, encoding="utf-8"))

    print("=" * 94)
    print("E-169 验证：结构化通道固定加分是否有害")
    print("=" * 94)

    # ---------- ① 实体频率统计 ----------
    print("\n① 说话人名字在语料里的出现频率")
    print("-" * 94)

    freq_rows = []
    for conv in data:
        c = conv.get("conversation") or {}
        spk_a = c.get("speaker_a") or ""
        spk_b = c.get("speaker_b") or ""

        # 统计所有轮次
        j = 1
        n_turn = 0
        n_with_a = 0
        n_with_b = 0
        n_with_either = 0
        while f"session_{j}" in c:
            for turn in c[f"session_{j}"] or []:
                txt = (turn.get("text") or turn.get("content") or "")
                spk = turn.get("speaker") or ""
                n_turn += 1
                full = f"{spk}: {txt}"
                if spk_a and spk_a in full:
                    n_with_a += 1
                if spk_b and spk_b in full:
                    n_with_b += 1
                if (spk_a and spk_a in full) or (spk_b and spk_b in full):
                    n_with_either += 1
            j += 1

        if n_turn:
            rate = n_with_either / n_turn
            freq_rows.append({
                "sample_id": conv.get("sample_id"),
                "speakers": f"{spk_a}/{spk_b}",
                "n_turn": n_turn,
                "rate": round(rate, 4),
            })
            print(f"  {conv.get('sample_id'):<10} {spk_a}/{spk_b:<12} "
                  f"轮次 {n_turn:<5} 含主角 {rate:.1%}")

    if freq_rows:
        import statistics
        avg = statistics.mean(r["rate"] for r in freq_rows)
        print(f"\n  平均：{avg:.1%} 的记忆含至少一个说话人名")
        high = avg > 0.5
        print(f"  → {'❌ 高频，固定加分无区分度' if high else '✅ 有区分度'}")

    # ---------- ② A/B：关掉结构化通道 ----------
    print("\n② A/B：结构化通道开启 vs 关闭")
    print("-" * 94)

    orig_extract = store_v1.extract_elements

    def measure(with_struct, n_convs=3):
        """跑前 n 个对话"""
        hit = total = 0
        for conv in data[:n_convs]:
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

            db = os.path.join(TASK, f"{sid}_{'on' if with_struct else 'off'}.db")
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

            for q in qa:
                question = q.get("question") or ""
                ev = q.get("evidence") or []
                if not isinstance(ev, list):
                    ev = [ev]
                ev = [str(e).strip() for e in ev if e]
                if not question or not ev:
                    continue
                res = st.search(question, top=K)
                got = {mark_of.get(t[0].ep_id) for t in res}
                got.discard(None)
                total += 1
                hit += 1 if any(e in got for e in ev) else 0

            st.con.close()
        return hit, total

    # 关掉结构化：让 extract_elements 永远返回空
    def empty_extract(text):
        return {"person": [], "time": [], "location": [], "topic": []}

    print("  跑 结构化 ON（3 个对话）…")
    on_hit, on_total = measure(True, 3)
    print(f"    召回 {on_hit}/{on_total} = {on_hit/max(1,on_total):.4f}")

    # 关掉
    store_v1.extract_elements = empty_extract
    print("  跑 结构化 OFF（3 个对话）…")
    off_hit, off_total = measure(False, 3)
    print(f"    召回 {off_hit}/{off_total} = {off_hit/max(1,off_total):.4f}")

    # 恢复
    store_v1.extract_elements = orig_extract

    # ---------- 结论 ----------
    print("\n" + "=" * 94)
    print("结论")
    print("=" * 94)

    on_r = on_hit / max(1, on_total)
    off_r = off_hit / max(1, off_total)
    delta = on_r - off_r

    print(f"\n  结构化 ON:  {on_r:.4f}（{on_hit}/{on_total}）")
    print(f"  结构化 OFF: {off_r:.4f}（{off_hit}/{off_total}）")
    print(f"  差值: {delta:+.4f}")

    print(f"\n  判断：")
    if delta < -0.01:
        print(f"    → ❌ 结构化通道**有害**（开启后指标下降 {abs(delta):.4f}）")
        print(f"    → 固定加分 0.8 在实体高频语料上是噪声源")
        print(f"    → 应该关掉，或改用 IDF 加权")
    elif abs(delta) <= 0.01:
        print(f"    → 结构化通道近似中性（{delta:+.4f}）")
        print(f"    → 加分被高频实体稀释，等于没起作用")
        print(f"    → 应该改用 IDF 加权，让低频实体才有加成")
    else:
        print(f"    → ✅ 结构化通道有效（+{delta:.4f}）")
        print(f"    → 保留")

    out = {"experiment": "E-169",
           "speaker_freq": freq_rows,
           "struct_on": {"hit": on_hit, "total": on_total,
                         "rate": round(on_r, 4)},
           "struct_off": {"hit": off_hit, "total": off_total,
                          "rate": round(off_r, 4)},
           "delta": round(delta, 4)}
    with open(os.path.join(RESULTS, "e169_struct_ab.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e169_struct_ab.json')}")


if __name__ == "__main__":
    main()
