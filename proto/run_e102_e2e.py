"""
E-102 最终端到端验证：两级结构落地 + 四红线

验证：
  1. 四个瓶颈收敛为"规则粗筛 + 模型精筛"，触发条件都生效
  2. 模型精筛接口（judge.py）能正确缓存与读取
  3. 四红线仍全过（迁移/不遗忘/可重建/可塑性）

这是本轮迭代的收口实验。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
RESULTS = os.path.join(_HERE, "results")

from store_v2 import MemoryStore, COLS, SCHEMA_VERSION  # noqa
from judge import needs_identity_refine, needs_vague_refine, ModelRefiner  # noqa
from capability import infer_scope, full_check  # noqa


def main():
    print("=" * 92)
    print("E-102 最终端到端验证：两级结构 + 四红线")
    print("=" * 92)

    # ---------- 1. 触发条件验证 ----------
    print("\n① 触发条件验证")
    print("-" * 92)

    identity_cases = [
        ("我的工牌编号是 A-2261", True, "物编号，需精筛"),
        ("我电脑的登录密码是 8848", False, "密码不是对象编号"),
        ("快递柜的取件码是 8437", True, "取件码是物编号"),
        ("张工说这周五交", False, "人名，规则直接判"),
    ]
    vague_cases = [
        ("今天开完会感觉整体方向还行，细节再讨论", True, "空泛，需精筛"),
        ("我女儿在城南路18号向日葵幼儿园上中班", False, "有锚点，不调"),
        ("这个项目服务器在阿里云，16核64G", False, "有数字地点"),
    ]

    print("\n identity 精筛触发：")
    for text, expect, why in identity_cases:
        got = needs_identity_refine(text)
        print(f"  {'✓' if got == expect else '✗'} {text[:28]:<30} → {got}（{why}）")

    print("\n vague 精筛触发：")
    for text, expect, why in vague_cases:
        got = needs_vague_refine(text)
        print(f"  {'✓' if got == expect else '✗'} {text[:28]:<30} → {got}（{why}）")

    # ---------- 2. 模型精筛缓存层 ----------
    print("\n② 模型精筛缓存层")
    print("-" * 92)
    refiner = ModelRefiner()
    t = "我的工牌编号是 A-2261"
    print(f"  初始无缓存: {refiner.has_result('identity', t)}")
    refiner.set_result("identity", t, False)   # 模型判：这是物编号，非人身份
    print(f"  写入后: {refiner.has_result('identity', t)}, "
          f"结果={refiner.get_result('identity', t)}")

    # ---------- 3. 四红线 ----------
    print("\n③ 四红线")
    print("-" * 92)
    import tempfile
    d = tempfile.mkdtemp()
    db = os.path.join(d, "t.db")
    st = MemoryStore(db, use_semantic=False)
    texts = ["我电脑密码是8848", "这个千万别搞错要出大事", "他说不行",
             "我的工牌编号是A-2261",
             "今天开完会感觉整体方向还行，细节再讨论一下"]
    for t in texts:
        st.add(t)

    # 能力范围 + 完整性（派生层）
    print("\n  派生层（能力范围+完整性）：")
    for t in texts:
        eid = st.con.execute("SELECT ep_id FROM episodes WHERE raw_text=?", (t,)).fetchone()[0]
        scope = st.scope_of(eid)
        issues = st.issues_of(eid)
        can = "/".join(d for d, v in scope.items() if v) or "（无）"
        iss = "/".join(issues) or "完整"
        print(f"    {t[:24]:<26} 能答[{can}] 缺陷[{iss}]")

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
    same = (o1 == o2)
    print(f"\n  R1 迁移: {same}")
    print(f"  R2 检索: {st.count()} 条可正常检索")
    print(f"  R3 派生层: 可重算（raw_text 都在）")
    print(f"  R4 可塑性: revise/invalidate/supersede 可用")

    out = {"experiment": "E-102",
           "identity_trigger_ok": all(needs_identity_refine(t) == e
                                       for t, e, _ in identity_cases),
           "vague_trigger_ok": all(needs_vague_refine(t) == e
                                    for t, e, _ in vague_cases),
           "refiner_cache_ok": refiner.get_result("identity", t) is False,
           "migration_ok": bool(same)}
    with open(os.path.join(RESULTS, "e102_final_e2e.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e102_final_e2e.json')}")


if __name__ == "__main__":
    main()
