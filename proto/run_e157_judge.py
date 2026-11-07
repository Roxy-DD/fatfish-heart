"""
E-157 修正评测判定：让随机下界接近 0

E-156 的致命问题：
  随机检索的"答案可及率"高达 0.5273
  → 说明 answer_in_text 判定太宽松
  → 所有数字（0.60 / 0.6545 / 0.4643）都没有意义

根因：
  answer_in_text 只要求 60% 关键词命中，且单词长度 >=3
  而这些会话里 User/Assistant 反复讨论同一主题，
  答案词（如 "cake"、"cat"、"book"）本来就高频出现

修正方案（三档严紧度）：
  S1 原方案：60% 关键词命中（当前，太宽松）
  S2 严格：答案完整短语出现（归一化后子串匹配）
  S3 最严：答案完整短语出现 + 答案长度 >=5 且不是通用词

评估标准：
  **一个合格的判定，随机基线应该 < 0.10**
  否则指标失真，无法用于对比

本实验用同一批数据测三档判定，看哪档能让随机下界降下来。
"""
from __future__ import annotations

import json
import os
import random
import re
import sys
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e157")
os.makedirs(TASK, exist_ok=True)
RESULTS = os.path.join(_HERE, "results")

K = 10

# 通用词（答案若是这些，判定不可靠）
GENERIC = {"the", "and", "for", "with", "that", "this", "from", "have",
           "was", "were", "are", "you", "your", "our", "their", "about",
           "some", "many", "much", "more", "most", "two", "three", "four",
           "five", "six", "seven", "eight", "nine", "ten"}


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


def norm(s):
    """归一化：小写、标点换空格、压缩空白"""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (s or "").lower())).strip()


def judge_s1(answer, text):
    """S1 原方案：60% 关键词命中"""
    a, t = norm(answer), norm(text)
    if not a or not t:
        return False
    words = [w for w in a.split() if len(w) >= 3]
    if not words:
        return a in t
    return sum(1 for w in words if w in t) / len(words) >= 0.6


def judge_s2(answer, text):
    """S2 严格：答案完整短语作为子串出现"""
    a, t = norm(answer), norm(text)
    if not a or not t:
        return False
    # 去掉答案里的填充词
    core = " ".join(w for w in a.split() if w not in GENERIC)
    if not core:
        return False
    return core in t


def judge_s3(answer, text):
    """S3 最严：核心短语完整出现 + 长度 >= 5"""
    a, t = norm(answer), norm(text)
    if not a or not t:
        return False
    core = " ".join(w for w in a.split() if w not in GENERIC)
    if len(core) < 5:
        return False
    return core in t


JUDGES = [("S1 60%关键词", judge_s1),
          ("S2 完整短语", judge_s2),
          ("S3 短语+长度", judge_s3)]


def main():
    from store_v2 import MemoryStore

    print("=" * 94)
    print("E-157 修正评测判定（目标：随机下界 < 0.10）")
    print("=" * 94)

    print("\n拉取数据…")
    try:
        rows = fetch_rows(0, 40)
    except Exception as e:
        print(f"❌ {e}")
        return
    tests = [r for r in rows if not r.get("is_abstention")]
    print(f"  非 abstention: {len(tests)} 题")

    random.seed(42)

    # 收集每题的数据
    per_q = []
    for i, row in enumerate(tests):
        q = row.get("question") or ""
        ans = row.get("answer") or ""
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

        items = []
        for doc in docs:
            for date, role, txt in split_turns(doc):
                items.append(txt)
                try:
                    st.add(txt, resolve_conflict=False)
                except Exception:
                    pass

        if not items:
            st.con.close()
            continue

        res = st.search(q, top=K)
        combo_texts = [ep.raw_text for ep, *_ in res]
        rnd_texts = random.sample(items, min(K, len(items)))

        per_q.append({
            "i": i, "q": q, "ans": ans,
            "n": len(items),
            "combo": combo_texts,
            "random": rnd_texts,
        })
        st.con.close()

    print(f"  有效题目 {len(per_q)}")

    # ---------- 三档判定对比 ----------
    print("\n" + "-" * 94)
    print("三档判定下的随机基线 vs 我们的检索")
    print("-" * 94)
    print(f"{'判定方式':<20}{'随机':<12}{'组合':<12}{'差值':<12}{'随机是否可接受'}")
    print("-" * 94)

    summary = {}
    for name, fn in JUDGES:
        rnd_hit = sum(1 for p in per_q
                      if any(fn(p["ans"], t) for t in p["random"]))
        combo_hit = sum(1 for p in per_q
                        if any(fn(p["ans"], t) for t in p["combo"]))
        n = len(per_q)
        rnd_r = rnd_hit / n
        combo_r = combo_hit / n
        ok = rnd_r < 0.10
        summary[name] = {"random": round(rnd_r, 4),
                         "combo": round(combo_r, 4),
                         "acceptable": ok}
        print(f"{name:<20}{rnd_r:<12.4f}{combo_r:<12.4f}"
              f"{combo_r - rnd_r:<+12.4f}"
              f"{'✅' if ok else '❌ 太高'}")

    # ---------- 选最合适的一档 ----------
    print("\n" + "=" * 94)
    print("结论")
    print("=" * 94)

    usable = [(name, s) for name, s in summary.items() if s["acceptable"]]
    if usable:
        # 选随机最低、但组合仍有区分度的
        best = max(usable, key=lambda kv: kv[1]["combo"] - kv[1]["random"])
        print(f"\n  可用判定: {best[0]}")
        print(f"    随机基线 {best[1]['random']:.4f}（可接受）")
        print(f"    我们的检索 {best[1]['combo']:.4f}")
        print(f"    区分度 {best[1]['combo'] - best[1]['random']:+.4f}")
    else:
        print(f"\n  ❌ 三档判定的随机基线都 >= 0.10")
        print(f"  → 说明这批数据的答案词在会话中普遍高频")
        print(f"  → 需要考虑：")
        print(f"     · 换更严的判定（要求答案唯一对应）")
        print(f"     · 或用 LLM 作答（与论文协议对齐）")
        print(f"     · 或只评测题型明确、答案唯一的题目")

    out = {"experiment": "E-157", "n": len(per_q),
           "judges": summary}
    with open(os.path.join(RESULTS, "e157_judge.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e157_judge.json')}")


if __name__ == "__main__":
    main()
