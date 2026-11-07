"""
E-155 LongMemEval 适配层：端到端评测（不改数据集）

数据集：MemoryAsModality/LongMemEval（HF）
字段：question_id / question / answer / question_type / is_abstention / documents
  documents = 每个是一条完整会话文本，含 [Date: ...] 和 User/Assistant 轮次

适配策略（**只改自己，不改数据集**）：
  ① 从 documents 里切出"用户说过的话"（User: 之后的内容）
  ② 每条用户话语作为一个 episode 写入记忆库
  ③ 用 question 检索
  ④ 评测指标（不依赖 LLM 作答，纯客观）：
     · 检索召回：answer 的关键词是否出现在检索到的记忆里
     · 这是最保守的指标——检索到了才有机会答对

为什么不用 answer_session_ids：
  HF 这个版本没有该字段（GitHub 文档里有，实际数据没有）
  → 无法做 session 级召回
  → 改用"答案文本是否可及"作为代理指标

评测规模：先跑 30 题（含 knowledge-update 类），确认可行再扩。
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e155")
os.makedirs(TASK, exist_ok=True)
RESULTS = os.path.join(_HERE, "results")
DB = os.path.join(TASK, "e155.db")

K = 10   # 检索条数


def fetch_rows(offset=0, length=30):
    """从 HF datasets-server 拉数据"""
    url = ("https://datasets-server.huggingface.co/rows"
           f"?dataset=MemoryAsModality%2FLongMemEval&config=default"
           f"&split=train&offset={offset}&length={length}")
    req = urllib.request.Request(url, headers={"User-Agent": "research"})
    with urllib.request.urlopen(req, timeout=60) as r:
        d = json.loads(r.read().decode("utf-8"))
    return [row["row"] for row in d.get("rows", [])]


def split_user_turns(doc):
    """
    从一条会话文本里切出用户话语。

    格式： [Date: 2022/03/21 (Mon) 14:57]\nUser: ...\nAssistant: ...\n\nUser: ...
    返回 [(date_str, user_text), ...]
    """
    out = []
    cur_date = ""
    # 按 User: 切分
    parts = re.split(r"\n(?=User:)", doc)
    for p in parts:
        dm = re.search(r"\[Date:\s*([^\]]+)\]", p)
        if dm:
            cur_date = dm.group(1).strip()
        um = re.search(r"User:\s*(.*?)(?=\nAssistant:|\Z)", p, re.S)
        if um:
            txt = um.group(1).strip()
            # 清理：去多余空白，截断过长
            txt = re.sub(r"\s+", " ", txt)
            if len(txt) >= 15:
                out.append((cur_date, txt[:600]))
    return out


def answer_in_text(answer, text):
    """
    系统机械判定：答案是否出现在文本里。

    纯字符串匹配（小写、去标点），不做语义判断。
    """
    if not answer or not text:
        return False
    a = re.sub(r"[^\w\s]", " ", answer.lower())
    t = re.sub(r"[^\w\s]", " ", text.lower())
    # 答案可能是短语，抽关键词
    words = [w for w in a.split() if len(w) >= 3]
    if not words:
        return a.strip() in t
    # 至少 60% 的关键词命中
    hit = sum(1 for w in words if w in t)
    return hit / len(words) >= 0.6


def main():
    from store_v2 import MemoryStore

    print("=" * 94)
    print("E-155 LongMemEval 端到端适配评测")
    print("=" * 94)

    print("\n拉取数据…")
    try:
        rows = fetch_rows(0, 30)
    except Exception as e:
        print(f"❌ 拉取失败: {e}")
        return
    print(f"  拿到 {len(rows)} 题")

    from collections import Counter
    types = Counter(r.get("question_type") for r in rows)
    print(f"  题型分布: {dict(types)}")

    # 建库
    for suf in ("", "-wal", "-shm"):
        p = DB + suf
        if os.path.exists(p):
            os.remove(p)
    st = MemoryStore(DB)

    print("\n" + "-" * 94)
    print("逐题处理")
    print("-" * 94)
    print(f"{'#':<4}{'题型':<24}{'记忆数':<10}{'召回?':<8}{'问题'}")
    print("-" * 94)

    details = []
    hit = 0
    total = 0

    for i, row in enumerate(rows):
        q = row.get("question") or ""
        ans = row.get("answer") or ""
        qtype = row.get("question_type") or ""
        abst = bool(row.get("is_abstention"))
        docs = row.get("documents") or []

        # 每题独立库文件（避免 Windows 文件占用 + 题目间污染）
        db_i = os.path.join(TASK, f"q{i:03d}.db")
        for suf in ("", "-wal", "-shm"):
            p = db_i + suf
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass
        st2 = MemoryStore(db_i)

        n_mem = 0
        for doc in docs:
            for date, utxt in split_user_turns(doc):
                try:
                    st2.add(utxt, resolve_conflict=False)
                    n_mem += 1
                except Exception:
                    pass

        # abstention 题不该有答案，跳过评测
        if abst:
            print(f"{i:<4}{qtype[:22]:<24}{n_mem:<10}{'跳过':<8}{q[:34]}")
            st2.con.close()
            continue

        total += 1
        res = st2.search(q, top=K)
        got_texts = [ep.raw_text for ep, *_ in res]

        # 答案是否可及
        reachable = any(answer_in_text(ans, t) for t in got_texts)
        hit += 1 if reachable else 0

        print(f"{i:<4}{qtype[:22]:<24}{n_mem:<10}"
              f"{'✅' if reachable else '❌':<8}{q[:34]}")

        details.append({
            "i": i, "qtype": qtype, "question": q,
            "answer": ans, "n_memories": n_mem,
            "reachable": reachable,
            "top3": got_texts[:3],
        })
        st2.con.close()

    # ---------- 汇总 ----------
    print("\n" + "=" * 94)
    print("汇总")
    print("=" * 94)

    recall = hit / max(1, total)
    print(f"\n  答案可及率（检索 @{K}）: {hit}/{total} = {recall:.4f}")

    # 分题型
    from collections import defaultdict
    by = defaultdict(lambda: [0, 0])
    for d in details:
        by[d["qtype"]][1] += 1
        by[d["qtype"]][0] += 1 if d["reachable"] else 0
    print(f"\n  {'题型':<26}{'可及/总':<12}{'可及率'}")
    print("  " + "-" * 60)
    for t in sorted(by, key=lambda x: -by[x][1]):
        h, n = by[t]
        print(f"  {t[:24]:<26}{f'{h}/{n}':<12}{h/n:.4f}")

    # knowledge-update 专项
    ku = [d for d in details if d["qtype"] == "knowledge-update"]
    if ku:
        ku_hit = sum(1 for d in ku if d["reachable"])
        print(f"\n  ★ knowledge-update（对应我们的矛盾处理）: "
              f"{ku_hit}/{len(ku)} = {ku_hit/len(ku):.4f}")

    st.con.close()

    print(f"\n  说明：这是**答案可及率**，不是答题准确率。")
    print(f"  它衡量'检索到的记忆里有没有答案'，是答题的必要条件。")

    out = {"experiment": "E-155", "n": total, "k": K,
           "reachable": hit, "recall": round(recall, 4),
           "by_type": {t: {"hit": v[0], "total": v[1]}
                       for t, v in by.items()},
           "details": details}
    with open(os.path.join(RESULTS, "e155_longmemeval.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e155_longmemeval.json')}")


if __name__ == "__main__":
    main()
