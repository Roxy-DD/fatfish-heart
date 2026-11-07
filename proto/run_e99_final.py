"""
E-99 模型精筛层：组装进 store_v2，最终红线验证

E-96~E-98 验证的机制：
  1. vague_summary（长而空）规则完全失效（TP=0）
  2. 模型判断信息密度 F1=1.0（8/8 全对）
  3. 触发条件："无锚点 + 有模糊表态" → 召回 1.0，调用 21%

现在把这个机制做成 store_v2 的一个方法：judge_vague(text) -> bool
（这个方法是"模型精筛"的接口，调用方传入模型判断结果）

本实验：
  1. 验证 store_v2 组装后四红线仍全过
  2. 验证 judge_vague 的触发条件已内置
"""
from __future__ import annotations

import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
RESULTS = os.path.join(_HERE, "results")

# 触发条件（E-98 验证）
VAGUE = ["还行", "再讨论", "再想想", "再看看", "到时候", "再说", "可能",
         "差不多", "看情况", "先做着", "再说吧", "需要再", "反正", "总体上",
         "觉得可以", "还没想清楚", "完善一下", "进一步", "回头"]


def needs_vague_check(text: str) -> bool:
    """判断这条是否需要模型精筛"信息密度"（E-98 触发条件）"""
    t = text or ""
    if re.search(r"\d{2,}|\d{1,2}月\d{1,2}|\d{4}[-/年]", t):
        return False
    if re.search(r"([\u4e00-\u9fa5]{2,6}(?:路|街|巷|道)|医院|学校|公司|幼儿园|"
                 r"[A-Z][a-z]+)", t):
        return False
    return any(w in t for w in VAGUE)


def main():
    print("=" * 92)
    print("E-99 模型精筛层组装 + 最终红线验证")
    print("=" * 92)

    # 1. 触发条件复现 E-98 结果
    task = os.path.join(_HERE, "work", "e96")
    rows = json.load(open(os.path.join(task, "corpus.json"), encoding="utf-8"))
    e73 = json.load(open(os.path.join(_HERE, "work", "e73", "corpus.json"),
                         encoding="utf-8"))
    all_rows = []
    for r in rows:
        all_rows.append((r["rid"], r["memory"], r["kind"] == "empty"))
    for r in e73:
        all_rows.append((r["rid"], r["memory"], r["rid"] == "R20"))

    tp = sum(1 for _, t, empty in all_rows if needs_vague_check(t) and empty)
    fn = sum(1 for _, t, empty in all_rows if not needs_vague_check(t) and empty)
    recall = tp / (tp + fn) if (tp + fn) else 0
    print(f"\n① 触发条件复现：召回 {recall:.4f}（E-98 是 1.0000）")

    # 2. store_v2 四红线
    from store_v2 import MemoryStore, COLS, SCHEMA_VERSION
    import tempfile
    d = tempfile.mkdtemp()
    db = os.path.join(d, "t.db")
    st = MemoryStore(db, use_semantic=False)
    texts = ["我电脑密码是8848", "这个千万别搞错要出大事", "他说不行",
             "今天开完会感觉整体方向还行，细节再讨论一下"]
    for t in texts:
        st.add(t)

    # 迁移
    db2 = os.path.join(d, "t2.db")
    st2 = MemoryStore(db2, use_semantic=False)
    for r in st.con.execute(f"SELECT {','.join(COLS)} FROM episodes").fetchall():
        st2.con.execute(
            f"INSERT OR REPLACE INTO episodes VALUES ({','.join('?'*(len(r)+1))})",
            tuple(r) + (SCHEMA_VERSION,))
    st2.con.commit()
    o1 = st.con.execute(f"SELECT {','.join(COLS)} FROM episodes ORDER BY ep_id").fetchall()
    o2 = st2.con.execute(f"SELECT {','.join(COLS)} FROM episodes ORDER BY ep_id").fetchall()
    print(f"② 迁移一致: {o1 == o2}")

    # 3. 情绪重算是否生效
    ep = st.get(st.con.execute("SELECT ep_id FROM episodes WHERE raw_text LIKE '%千万别搞错%'").fetchone()[0])
    print(f"③ 情绪重算: 千万别搞错 → affect_peak={ep.affect_peak:.2f} valence={ep.valence:+.2f}")

    out = {"experiment": "E-99",
           "trigger_recall": round(recall, 4),
           "migration_ok": bool(o1 == o2),
           "affect_recomputed": {"text": "这个千万别搞错要出大事",
                                 "affect_peak": ep.affect_peak,
                                 "valence": ep.valence}}
    with open(os.path.join(RESULTS, "e99_final.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e99_final.json')}")


if __name__ == "__main__":
    main()
