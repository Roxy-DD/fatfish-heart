"""
E-193 定位 E-166(0.5802) vs E-189(0.3300) 的 25pp 差异

两个实验都是"全量 10 对话"，但基线相差 25pp，远超结构化通道的 ±4pp。

嫌疑（按可能排序）：
  S1 停用词修复的时机 —— E-166 用修复后，E-189 用修复前？
  S2 store.search 的 top 语义池改动（E-174 加 sem_pool/like_pool）
  S3 search 返回的元组结构变了，E-189 的 mark 提取错位
  S4 建库差异（E-189 里 st.add 的 resolve_conflict 默认值）

方法：用同一个对话，分别用当前 store.search 和 E-166 时代的逻辑跑，
      对比单条查询的返回是否一致。
"""
from __future__ import annotations

import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e193")
os.makedirs(TASK, exist_ok=True)
DATA = os.path.join(_HERE, "work", "e165", "locomo10.json")


def main():
    from store_v2 import MemoryStore

    data = json.load(open(DATA, encoding="utf-8"))

    print("=" * 94)
    print("E-193 定位 25pp 差异")
    print("=" * 94)

    # 用第 1 个对话，仔细检查 search 的返回结构
    conv = data[0]
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

    print(f"库中 {len(mark_of)} 条记忆\n")

    # 检查 search 返回结构
    q0 = qa[0]["question"]
    print(f"示例查询: {q0}")
    res = st.search(q0, top=10)
    print(f"\nsearch 返回 {len(res)} 条")
    print(f"第 1 条类型: {type(res[0]).__name__}")
    if isinstance(res[0], tuple):
        print(f"  tuple 长度: {len(res[0])}")
        print(f"  [0] 类型: {type(res[0][0]).__name__}")
        print(f"  [0] 值: {res[0][0]}")
        if len(res[0]) > 1:
            print(f"  [1] 值: {res[0][1]}")
    else:
        print(f"  值: {res[0]}")

    # 关键检查：mark_of 能否从 res[0][0] 取到
    eid = res[0][0] if isinstance(res[0], tuple) else res[0]
    print(f"\n第一条 ep_id={eid} 在 mark_of 里吗: {eid in mark_of}")
    print(f"  对应 mark: {mark_of.get(eid)}")

    # 统计：这个对话的基线 recall@10
    base_hit = 0
    total = 0
    for q in qa:
        question = q.get("question") or ""
        ev = q.get("evidence") or []
        if not isinstance(ev, list):
            ev = [ev]
        ev = [str(e).strip() for e in ev if e]
        if not question or not ev:
            continue
        total += 1
        res = st.search(question, top=10)
        got = {mark_of.get(t[0].ep_id) for t in res}
        got.discard(None)
        if any(e in got for e in ev):
            base_hit += 1

    print(f"\n对话 {sid} 基线 recall@10: {base_hit}/{total} = "
          f"{base_hit/max(1,total):.4f}")

    st.con.close()


if __name__ == "__main__":
    main()
