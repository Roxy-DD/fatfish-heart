"""
E-158 LongMemEval 正式协议：检索 + LLM 作答 + 判定

E-155~E-157 的失败教训：
  · 用"关键词出现"当答案可及 → 随机基线 0.49~0.53，指标失真
  · 收紧到完整短语 → 随机仍有 0.19~0.35
  · 根因：LongMemEval 的会话里同一实体反复提及，字符串匹配天然高频命中

论文的正式协议：
  ① 检索 top-K 记忆
  ② 交给 LLM 作答
  ③ 与标准答案比对

本实验：
  阶段一（本脚本）：检索 + 生成答题任务包
  阶段二（子代理）：LLM 逐题作答
  阶段三：判定 + 汇总
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e158")
os.makedirs(TASK, exist_ok=True)
RESULTS = os.path.join(_HERE, "results")

K = 10


def fetch_rows(offset=0, length=40):
    url = ("https://datasets-server.huggingface.co/rows"
           f"?dataset=MemoryAsModality%2FLongMemEval&config=default"
           f"&split=train&offset={offset}&length={length}")
    req = urllib.request.Request(url, headers={"User-Agent": "research"})
    with urllib.request.urlopen(req, timeout=90) as r:
        d = json.loads(r.read().decode("utf-8"))
    return [row["row"] for row in d.get("rows", [])]


def split_turns(doc):
    out = []
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


def main():
    from store_v2 import MemoryStore

    print("=" * 94)
    print("E-158 LongMemEval 正式协议（阶段一：检索 + 建任务包）")
    print("=" * 94)

    print("\n拉取数据…")
    try:
        rows = fetch_rows(0, 40)
    except Exception as e:
        print(f"❌ {e}")
        return
    tests = [r for r in rows if not r.get("is_abstention")]
    print(f"  非 abstention: {len(tests)} 题")

    tasks = []
    for i, row in enumerate(tests):
        q = row.get("question") or ""
        ans = row.get("answer") or ""
        qtype = row.get("question_type") or ""
        qdate = row.get("question_date") or ""
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

        n_mem = 0
        for doc in docs:
            for date, role, txt in split_turns(doc):
                try:
                    st.add(txt, resolve_conflict=False)
                    n_mem += 1
                except Exception:
                    pass

        if n_mem == 0:
            st.con.close()
            continue

        res = st.search(q, top=K)
        ctx = []
        for ep, sc, *rest in res:
            ctx.append(ep.raw_text)

        tasks.append({
            "cid": f"Q{i:02d}",
            "question": q,
            "question_type": qtype,
            "question_date": qdate,
            "gold_answer": ans,
            "n_memories": n_mem,
            "context": ctx,
        })
        st.con.close()

    with open(os.path.join(TASK, "tasks.json"), "w", encoding="utf-8") as f:
        json.dump(tasks, f, ensure_ascii=False, indent=1)

    print(f"\n  已生成 {len(tasks)} 个答题任务")
    print(f"  → {os.path.join(TASK, 'tasks.json')}")

    from collections import Counter
    c = Counter(t["question_type"] for t in tasks)
    print(f"\n  题型分布:")
    for k, v in c.most_common():
        print(f"    {k:<28}{v}")

    print(f"\n  每题上下文 {K} 条记忆，平均 "
          f"{sum(len(t['context']) for t in tasks) / len(tasks):.0f} 字符")

    out = {"experiment": "E-158", "n": len(tasks),
           "by_type": dict(c), "k": K}
    with open(os.path.join(RESULTS, "e158_prep.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
