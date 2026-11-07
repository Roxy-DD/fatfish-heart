"""
E-123 重构后的端到端验证

重构内容（conflict.py v2）：
  系统接管：值类型提取 / 余弦计算 / 候选打包 / 落盘
  模型判断：目标选择 / 关系类型（通过 judge_fn 回调注入）

验证两件事：
  ① 用 E-122 的模型判断结果回放，确认重构后仍 14/14
  ② 无 judge_fn 时的降级行为（只存包、不误判）
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
RESULTS = os.path.join(_HERE, "results")
TASK = os.path.join(_HERE, "work", "e122")

CASES = [
    ("客户同意把截止延到20号", "这个项目 15 号截止", "replace"),
    ("门锁密码换成了9512", "家里的门锁密码是 3721", "replace"),
    ("接口参数改成了1.2", "接口参数定的是0.85", "replace"),
    ("明天上午十点开会", "明天下午三点开会", "replace"),
    ("我换车了现在是黑色大众", "我的车是白色卡罗拉", "replace"),
    ("我搬家到城西新村5号", "我住在城东花园3栋", "replace"),
    ("客户还要求界面必须支持大字体", "客户要求界面必须支持深色模式", "merge"),
    ("另外我晚上也要吃一次药", "我要连续吃两周的药", "merge"),
    ("小李说门锁密码是9512", "老王说门锁密码是 3721", "conflict"),
    ("我记得好像是18号截止", "这个项目 15 号截止", "conflict"),
    ("这个bug反复出现我快崩溃了", None, "none"),
    ("上周二午饭吃的是番茄炒蛋", None, "none"),
]

BASE = [
    "我电脑的登录密码是 8848",
    "家里的门锁密码是 3721",
    "这个项目 15 号截止",
    "我女儿在城南路18号上中班",
    "我的车是白色卡罗拉",
    "我住在城东花园3栋",
    "接口参数定的是0.85",
    "明天下午三点开会",
    "我生日是3月8号",
    "客户的验收标准是延迟低于5ms",
    "我要连续吃两周的药",
    "客户要求界面必须支持深色模式",
    "老王说门锁密码是 3721",
    "仓库里还有两百件库存",
]


def make_gold_judge(gold_map):
    """
    构造一个"理想模型"judge_fn：按 gold 答案返回。
    用于验证系统链路是否正确（而非验证模型能力，那已在 E-122 验过）。
    """
    def judge_fn(pack):
        new = pack["new_memory"]
        gold = gold_map.get(new)
        if gold is None:
            return json.dumps({"target": None, "relation": "none",
                               "reason": "gold 未覆盖", "confidence": 1.0}, ensure_ascii=False)
        target, relation = gold
        if target is None:
            return json.dumps({"target": None, "relation": "none",
                               "reason": "无关", "confidence": 1.0}, ensure_ascii=False)
        # 目标必须出现在候选里，否则说明系统给的候选包不对
        if not any(c["text"] == target for c in pack["candidates"]):
            return json.dumps({"target": None, "relation": "error",
                               "reason": f"gold 目标不在候选包中: {target}"},
                              ensure_ascii=False)
        return json.dumps({"target": target, "relation": relation,
                           "reason": "gold 回放", "confidence": 1.0}, ensure_ascii=False)
    return judge_fn


def main():
    from store_v2 import MemoryStore

    print("=" * 92)
    print("E-123 重构后端到端验证（系统提取 + 模型判断）")
    print("=" * 92)

    # ---------- ① 有 judge_fn：完整链路 ----------
    # E-123 修正：原设计所有用例共用一个库，前面的 replace 会把后面的目标标失效，
    # 造成状态依赖（「18号截止」的目标已被「延到20号」那条办掉了）。
    # 改为每个用例独立库，避免用例间互相干扰。
    print(f"\n{'新记忆':<26}{'gold':<11}{'实判':<11}{'对?':<6}{'目标对?':<8}{'旧值已失效?'}")
    print("-" * 92)

    gold_map = {}
    for new_, target_, rel_ in CASES:
        gold_map[new_] = (target_, rel_)

    rel_hit = 0
    tgt_hit = 0
    inv_hit = 0
    n = len(CASES)
    n_replace = sum(1 for _, t, r in CASES if r == "replace")
    details = []

    for idx, (new, target, gold_rel) in enumerate(CASES):
        DB = os.path.join(_HERE, f"e123_case_{idx}.db")
        for suf in ("", "-wal", "-shm"):
            p = DB + suf
            if os.path.exists(p):
                os.remove(p)

        st = MemoryStore(DB)
        ids = {}
        for t in BASE:
            ep = st.add(t, resolve_conflict=False)
            ids[t] = ep.ep_id

        judge_fn = make_gold_judge({new: gold_map[new]})
        ep, v = st.add(new, judge_fn=judge_fn, return_verdict=True)
        got_rel = v.relation if v else "none"
        rel_ok = (got_rel == gold_rel)
        rel_hit += 1 if rel_ok else 0

        if target is None:
            tgt_ok = not (v and v.target_ep_id)
        else:
            tgt_ok = bool(v and v.target_ep_id == ids.get(target))
        tgt_hit += 1 if tgt_ok else 0

        if gold_rel == "replace" and target:
            ep_t = st.get(ids[target])
            inv_ok = ep_t.invalid_at is not None
        else:
            inv_ok = None
        inv_hit += 1 if inv_ok else 0

        tgt_show = (v.target_text[:16] if v and v.target_text else "（无）")
        inv_show = ("是" if inv_ok else ("否" if inv_ok is False else "—"))
        print(f"{new[:24]:<26}{gold_rel:<11}{got_rel:<11}"
              f"{'✓' if rel_ok else '✗':<6}{'✓' if tgt_ok else '✗':<8}{inv_show}")
        details.append({"new": new, "gold": gold_rel, "got": got_rel,
                        "target": tgt_show, "rel_ok": rel_ok, "tgt_ok": tgt_ok})
        st.con.close()

    print("-" * 92)
    print(f"\n  关系判定: {rel_hit}/{n} = {rel_hit/n:.4f}")
    print(f"  目标选择: {tgt_hit}/{n} = {tgt_hit/n:.4f}")
    n_replace = sum(1 for _, t, r in CASES if r == "replace")
    print(f"  replace 旧值失效: {inv_hit}/{n_replace}")

    # ---------- ② 无 judge_fn：降级行为 ----------
    print("\n" + "=" * 92)
    print("② 无 judge_fn 的降级行为（只存候选包，不误判）")
    print("=" * 92)

    DB2 = os.path.join(_HERE, "e123_pending.db")
    for suf in ("", "-wal", "-shm"):
        p = DB2 + suf
        if os.path.exists(p):
            os.remove(p)

    st2 = MemoryStore(DB2)
    for t in BASE:
        st2.add(t, resolve_conflict=False)

    for new, target, gold_rel in CASES:
        st2.add(new, resolve_conflict=True, judge_fn=None)

    pending = st2.pending_packs()
    n_invalid = st2.con.execute(
        "SELECT COUNT(*) FROM episodes WHERE invalid_at IS NOT NULL").fetchone()[0]

    print(f"\n  写入 {len(CASES)} 条后：")
    print(f"  待判定候选包: {len(pending)} 条")
    print(f"  被误标失效:   {n_invalid} 条  {'✅ 无（安全降级）' if n_invalid == 0 else '❌ 有误判'}")
    if pending:
        p0 = pending[0]
        print(f"\n  示例候选包:")
        print(f"    新记忆: {p0['new_text']}")
        print(f"    值类型: {p0['new_value_types']}")
        print(f"    候选数: {len(p0['candidates'])}")
        for c in p0["candidates"][:2]:
            print(f"      cos={c['cos']:.3f} 类型={c['value_types']}  {c['text'][:34]}")

    # ---------- 结论 ----------
    print("\n" + "=" * 92)
    print("结论")
    print("=" * 92)
    print(f"  ① 有模型判断: 关系 {rel_hit}/{n}，目标 {tgt_hit}/{n}，"
          f"失效 {inv_hit}/{n_replace}")
    print(f"  ② 无模型判断: 误标失效 {n_invalid} 条（应为 0）")
    print()
    if rel_hit == n and tgt_hit == n and n_invalid == 0:
        print("  → 重构成功：系统只管机械提取，判断权交给模型，无模型时不误判")
    else:
        print("  → 仍有问题，需排查")

    out = {"experiment": "E-123", "n": n,
           "relation_accuracy": round(rel_hit / n, 4),
           "target_accuracy": round(tgt_hit / n, 4),
           "invalidate_accuracy": round(inv_hit / n_replace, 4) if n_replace else None,
           "degraded_false_invalidate": n_invalid,
           "pending_packs": len(pending),
           "details": details}
    with open(os.path.join(RESULTS, "e123_refactor.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e123_refactor.json')}")


if __name__ == "__main__":
    main()
