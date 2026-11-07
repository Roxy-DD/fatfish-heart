"""
E-118 隔离测试：单条矛盾能否被正确处理（排除写入顺序干扰）

E-117b 最后一条失败，诊断显示：
  判定层算出正确目标 [9564865b]「这个项目 15 号截止」
  但实际被标失效的是 [cadd81d9]「客户的验收标准是延迟低于5ms」

怀疑：多条矛盾记忆依次写入时，后写入的会干扰前一次的目标选择。

本实验：
  每条矛盾单独建一个干净数据库，只写「1条基础 + 1条矛盾」，
  测判定是否准确、旧值是否被正确标失效。
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
RESULTS = os.path.join(_HERE, "results")

CASES = [
    ("我电脑的登录密码是 8848", "我把电脑密码改成了9988", "replace", "密码修改"),
    ("家里的门锁密码是 3721", "门锁密码换成了9512", "replace", "门锁更换"),
    ("这个项目 15 号截止", "客户同意把截止延到20号", "replace", "延期"),
    ("我的车是白色卡罗拉", "我换车了现在是黑色大众", "replace", "换车"),
    ("我住在城东花园3栋", "我搬家到城西新村5号", "replace", "搬家"),
    ("接口参数定的是0.85", "接口参数改成了1.2", "replace", "参数调整"),
    ("明天下午三点开会", "会议改到明天上午十点", "replace", "改期"),
    ("客户要求界面支持深色模式", "客户还要求界面支持大字体", "merge", "追加需求"),
    ("老王说门锁密码是3721", "小李说门锁密码是9512", "conflict", "两来源"),
]


def main():
    from store_v2 import MemoryStore

    print("=" * 88)
    print("E-118 隔离测试：单条矛盾的处理正确性")
    print("=" * 88)
    print(f"\n{len(CASES)} 组，每组独立数据库\n")

    print(f"{'说明':<12}{'期望':<12}{'实判':<12}{'对?':<5}{'旧值被标失效':<14}{'选了哪条'}")
    print("-" * 88)

    rel_ok = 0
    inv_ok = 0
    results = []

    for i, (old_text, new_text, expect, note) in enumerate(CASES):
        db = os.path.join(_HERE, f"e118_{i}.db")
        for suf in ("", "-wal", "-shm"):
            p = db + suf
            if os.path.exists(p):
                os.remove(p)

        st = MemoryStore(db)
        ep_old = st.add(old_text)
        ep_new, v = st.add(new_text, return_verdict=True)

        got = v.relation if v else "none"
        old_ep = st.get(ep_old.ep_id)
        invalidated = old_ep.invalid_at is not None

        r_ok = (got == expect)
        # 期望：replace 应标失效；merge/conflict 不应标失效
        if expect == "replace":
            i_ok = invalidated
        else:
            i_ok = not invalidated

        rel_ok += 1 if r_ok else 0
        inv_ok += 1 if i_ok else 0

        chosen = v.old_text[:20] if v and v.old_text else "-"
        correct_target = (v.old_ep_id == ep_old.ep_id) if v else False

        print(f"{note:<12}{expect:<12}{got:<12}{'✓' if r_ok else '✗':<5}"
              f"{'是' if invalidated else '否':<14}{chosen}")

        results.append({
            "note": note, "old": old_text, "new": new_text,
            "expect": expect, "got": got,
            "similarity": v.similarity if v else 0,
            "rel_ok": r_ok, "invalidated": invalidated, "inv_ok": i_ok,
            "correct_target": correct_target,
            "reason": v.reason if v else "",
        })

    print("-" * 88)
    n = len(CASES)
    print(f"\n  判定准确: {rel_ok}/{n} = {rel_ok/n:.4f}")
    print(f"  失效正确: {inv_ok}/{n} = {inv_ok/n:.4f}")
    print(f"  目标选对: {sum(1 for r in results if r['correct_target'])}/{n}")

    fails = [r for r in results if not r["rel_ok"] or not r["inv_ok"]]
    if fails:
        print("\n失败案例：")
        for r in fails:
            print(f"  [{r['note']}] 期望={r['expect']} 实判={r['got']} "
                  f"sim={r['similarity']:.3f} 标失效={r['invalidated']}")
            print(f"    {r['reason'][:76]}")
    else:
        print("\n  全部正确")

    out = {"experiment": "E-118", "n": n,
           "relation_accuracy": round(rel_ok / n, 4),
           "invalidate_accuracy": round(inv_ok / n, 4),
           "cases": results}
    with open(os.path.join(RESULTS, "e118_isolated.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e118_isolated.json')}")


if __name__ == "__main__":
    main()
