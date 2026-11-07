"""
E-87 组装后完整验证（store_v2）

用户要求：组装起来的效果不一定和实验时一样，必须重跑。

验证三件事：
  A. 四条红线（迁移 100% / 能力不降 / 索引可重建 / 不丢数据）
  B. 组装后的能力是否还在（能力范围路由 / 完整性闸 / 身份判定）
  C. 组装是否引入了新问题（性能、正确性）
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
RESULTS = os.path.join(_HERE, "results")
WORK = os.path.join(_HERE, "work", "e87")
os.makedirs(WORK, exist_ok=True)
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

from store_v2 import MemoryStore, COLS, SCHEMA_VERSION  # noqa
from capability import infer_scope, detect_question_dims, full_check  # noqa

CORPUS = [
    "我电脑的登录密码是 8848",
    "家里的门锁密码是 3721",
    "下个月 15 号是项目的截止日期，逾期不候",
    "客户明确说这条需求不能改",
    "这个千万别搞错，搞错了要出大事",
    "小时候被邻居家的狗追着咬过，小腿上留了疤",
    "小时候在老家院子里看夕阳，天是橘红色的",
    "爷爷去世了，那阵子我一直在医院和家之间来回跑",
    "钱包丢了，里面有身份证和所有的卡",
    "12月3日下午两点，我和张工在7楼会议室对齐了接口协议",
    "他说不行",
    "上周二午饭吃的是番茄炒蛋",
]

QUERIES = [
    ("电脑密码是多少", 0), ("门锁密码", 1), ("什么时候截止", 2),
    ("客户有什么不能改的", 3), ("千万别搞错", 4), ("被狗咬", 5),
    ("小时候的夕阳", 6), ("爷爷怎么了", 7), ("钱包里有什么", 8),
    ("什么时候和张工对的接口", 9), ("他说了什么", 10), ("午饭吃了什么", 11),
]


def main():
    print("=" * 92)
    print("E-87 组装后完整验证（store_v2）")
    print("=" * 92)

    db = os.path.join(WORK, "v2.db")
    for suf in ("", "-wal", "-shm"):
        p = db + suf
        if os.path.exists(p):
            os.remove(p)

    st = MemoryStore(db)
    print("\n写入语料...")
    ids = []
    for text in CORPUS:
        ep = st.add(text)
        ids.append(ep.ep_id)

    # ---------- B. 组装后的能力 ----------
    print("\n" + "=" * 92)
    print("B. 组装后的能力是否还在")
    print("=" * 92)

    # 能力范围
    print("\n能力范围（6 维度，抽样）：")
    for i in [4, 6, 7, 9, 10]:
        s = st.scope_of(ids[i])
        mark = "/".join(d for d, v in s.items() if v) or "（无）"
        print(f"  [{i}] {CORPUS[i][:26]:<28} → {mark}")

    # 完整性闸
    print("\n完整性闸（缺陷检测）：")
    n_inc = 0
    for i in range(len(CORPUS)):
        issues = st.issues_of(ids[i])
        if issues:
            n_inc += 1
            print(f"  [{i}] {'/'.join(issues):<24} {CORPUS[i][:30]}")
    print(f"  共标记 {n_inc} 条不完整")

    # 能力范围路由
    print("\n能力范围路由（该答的答，不该答的老实说）：")
    route_tests = [
        ("爷爷怎么了", 7, True),
        ("爷爷是哪一年去世的", 7, False),
        ("什么时候和张工对的接口", 9, True),
        ("钱包是什么时候丢的", 8, False),
        ("电脑密码是多少", 0, True),
    ]
    route_hit = 0
    for q, gi, should in route_tests:
        out, qdims, stats = st.search_scoped(q, top=3)
        got = [ep.ep_id for ep, *_ in out]
        in_top = ids[gi] in got
        # 对"不该答"的，检查是否带了 note（诚实边界）
        note = out[0][2] if out else None
        ok = in_top == should or (not should and note is not None)
        route_hit += 1 if ok else 0
        print(f"  {q:<24} 目标在top3={in_top}  应{'该' if should else '不该'}答  "
              f"{'✓' if ok else '✗'}  note={note}")
    print(f"  路由一致 {route_hit}/{len(route_tests)}")

    # ---------- 检索召回（能力不降 R2） ----------
    print("\n" + "=" * 92)
    print("A. 四条红线")
    print("=" * 92)
    hit = 0
    for q, gi in QUERIES:
        res = st.search(q, top=5)
        got = [ep.ep_id for ep, *_ in res]
        if ids[gi] in got:
            hit += 1
    rec = hit / len(QUERIES)
    print(f"\n  R2 检索召回: {hit}/{len(QUERIES)} = {rec:.4f}")

    # 迁移（正确方式：原样复制行，保持 ep_id 不变，而非重新 add）
    db2 = os.path.join(WORK, "v2_mig.db")
    for suf in ("", "-wal", "-shm"):
        p = db2 + suf
        if os.path.exists(p):
            os.remove(p)
    st2 = MemoryStore(db2, use_semantic=False)
    rows = st.con.execute(f"SELECT {','.join(COLS)} FROM episodes").fetchall()
    for r in rows:
        st2.con.execute(
            f"INSERT OR REPLACE INTO episodes VALUES ({','.join('?'*(len(r)+1))})",
            tuple(r) + (SCHEMA_VERSION,))
    for eid, dim, vec in st.con.execute("SELECT ep_id, dim, vec FROM vectors").fetchall():
        st2.con.execute("INSERT OR REPLACE INTO vectors VALUES (?,?,?)", (eid, dim, vec))
    st2.con.execute("INSERT INTO ep_fts(ep_fts) VALUES('rebuild')")
    st2.con.commit()

    # 逐字段比对
    o1 = st.con.execute(f"SELECT {','.join(COLS)} FROM episodes ORDER BY ep_id").fetchall()
    o2 = st2.con.execute(f"SELECT {','.join(COLS)} FROM episodes ORDER BY ep_id").fetchall()
    same_rows = (o1 == o2)
    v1 = st.con.execute("SELECT ep_id, vec FROM vectors ORDER BY ep_id").fetchall()
    v2 = st2.con.execute("SELECT ep_id, vec FROM vectors ORDER BY ep_id").fetchall()
    same_vec = (v1 == v2)
    print(f"  R1 迁移: 行一致={same_rows} 向量一致={same_vec}")

    # 索引可重建
    st.con.execute("DELETE FROM vectors"); st.con.commit(); st._vec = None
    st.rebuild_vectors()
    print(f"  R3 索引可重建: {st.con.execute('SELECT COUNT(*) FROM vectors').fetchone()[0]} 条")

    # 不丢数据
    n_before = st.count()
    st.supersede(ids[1], "门锁密码换成了 9988")
    n_after = st.count()
    old = st.get(ids[1])
    print(f"  R4 不丢数据: {n_before}→{n_after}，旧存在={old is not None}，"
          f"失效={old.invalid_at is not None}")

    # 派生层一致性（能力范围在 revise 后重算）
    st.revise(ids[1], "门锁密码换成了 9988")
    s_after = st.scope_of(ids[1])
    print(f"  派生层重算: 门锁密码 revise 后 value={s_after['value']}")

    checks = [
        ("R1 迁移100%", same_rows and same_vec),
        ("R2 能力不降", rec >= 0.90),
        ("R3 索引可重建", st.con.execute("SELECT COUNT(*) FROM vectors").fetchone()[0] == n_after),
        ("R4 不丢数据", n_after == n_before + 1 and old is not None),
    ]
    print("\n  " + "=" * 88)
    print("  红线汇总")
    print("  " + "=" * 88)
    all_ok = True
    for name, ok in checks:
        all_ok = all_ok and ok
        print(f"    {name:<16}{'✅' if ok else '❌'}")
    print(f"\n  → {'组装成功，红线全过' if all_ok else '组装有问题，需修'}")

    out = {"experiment": "E-87", "recall": rec, "route_hit": route_hit,
           "redline": {n: bool(v) for n, v in checks}, "all_pass": all_ok}
    with open(os.path.join(RESULTS, "e87_assembly.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e87_assembly.json')}")


if __name__ == "__main__":
    main()
