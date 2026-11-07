"""
E-117（修正版）矛盾处理后：问同一个问题，能否拿到"正确的当前值"

上一版（run_e117_r2_check.py）的方法学错误：
  它测的是「旧值还在不在检索结果里」。
  但旧值被 replace 标记失效后不再返回 —— 这正是设计意图。
  把"正确行为"误判成了"记忆损害"，结论错误，作废。

正确设计：
  对每个被 replace 的问题，检查系统是否返回了「新的正确值」。
  例：问"电脑密码是多少"，正确答案是 9988（新），不是 8848（旧）。
     · 返回新值   → 正确
     · 返回旧值   → 严重错误（过时信息污染）
     · 两者都返回 → 错误（矛盾未消解）
     · 都不返回   → 丢失

同时保留真正的 R2 检查：数据是否还在（含失效的），用 include_invalid 查。
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
RESULTS = os.path.join(_HERE, "results")
DB = os.path.join(_HERE, "e117b.db")

# (查询, 应当返回的新值关键词, 不应当返回的旧值关键词, 说明)
REPLACE_CASES = [
    ("电脑密码是多少", "9988", "8848", "密码修改"),
    ("门锁密码", "9512", "3721", "门锁更换"),
    ("项目什么时候截止", "20", "15", "延期"),
    ("我的车是什么", "黑色大众", "白色卡罗拉", "换车"),
    ("我家住哪", "城西新村", "城东花园", "搬家"),
    ("接口参数多少", "1.2", "0.85", "参数调整"),
    ("明天几点开会", "十点", "三点", "会议改期"),
]

# 不受矛盾影响的基础记忆（对照：应保持原样）
CONTROL_CASES = [
    ("女儿在哪上学", "城南路"),
    ("妈妈电话多少", "13800138000"),
    ("我生日哪天", "3月8"),
    ("服务器什么配置", "阿里云"),
    ("我几点起床", "七点"),
    ("老婆对什么过敏", "花生"),
    ("我答应什么时候交文档", "周五"),
    ("我要吃多久药", "两周"),
    ("周末去哪", "爬山"),
    ("库存多少", "两百"),
    ("房租什么时候到期", "月底"),
]

BASE = [
    ("我电脑的登录密码是 8848",),
    ("家里的门锁密码是 3721",),
    ("这个项目 15 号截止",),
    ("我女儿在城南路18号上中班",),
    ("我的车是白色卡罗拉",),
    ("我住在城东花园3栋",),
    ("接口参数定的是0.85",),
    ("明天下午三点开会",),
    ("我妈电话是13800138000",),
    ("我生日是3月8号",),
    ("服务器在阿里云华东区16核64G",),
    ("客户的验收标准是延迟低于5ms",),
    ("我每天早上七点起床",),
    ("我老婆对花生过敏",),
    ("我答应周五前交接口文档",),
    ("上次评审会产品经理否了登录页改版",),
    ("我要连续吃两周的药",),
    ("周末打算去郊外爬山",),
    ("仓库里还有两百件库存",),
    ("房租月底到期房东要涨价",),
]

CONFLICTS = [
    ("我把电脑密码改成了9988", "replace"),
    ("门锁密码换成了9512", "replace"),
    ("客户同意把截止延到20号", "replace"),
    ("我换车了现在是黑色大众", "replace"),
    ("我搬家到城西新村5号", "replace"),
    ("接口参数改成了1.2", "replace"),
    ("会议改到明天上午十点", "replace"),
    ("客户还要求界面支持大字体", "merge"),
    ("评审会又追加了一条要求", "merge"),
    ("小李说门锁密码是7710", "conflict"),
]


def main():
    from store_v2 import MemoryStore

    for suf in ("", "-wal", "-shm"):
        p = DB + suf
        if os.path.exists(p):
            os.remove(p)

    st = MemoryStore(DB)
    print("=" * 90)
    print("E-117b 矛盾处理后：能否拿到正确的当前值")
    print("=" * 90)

    # 写入
    print(f"\n写入 {len(BASE)} 条基础记忆 + {len(CONFLICTS)} 条矛盾记忆")
    for (t,) in BASE:
        st.add(t)
    for t, _ in CONFLICTS:
        st.add(t)

    # ---------- ① replace 类：应返回新值，不应返回旧值 ----------
    print("\n" + "=" * 90)
    print("① 被 replace 的问题：应拿到新值")
    print("=" * 90)
    print(f"{'查询':<22}{'期望新值':<14}{'返回内容':<30}{'判定'}")
    print("-" * 90)

    ok_new = 0
    bad_old = 0
    both = 0
    none_ = 0
    details = []

    for q, new_kw, old_kw, note in REPLACE_CASES:
        res = st.search(q, top=5)
        texts = [ep.raw_text for ep, *_ in res]
        has_new = any(new_kw in t for t in texts[:3])
        has_old = any(old_kw in t for t in texts[:3])

        if has_new and has_old:
            verdict, kind = "❌ 新旧都返回", "both"
            both += 1
        elif has_new:
            verdict, kind = "✅ 拿到新值", "new"
            ok_new += 1
        elif has_old:
            verdict, kind = "❌ 返回旧值", "old"
            bad_old += 1
        else:
            verdict, kind = "⚠ 都没返回", "none"
            none_ += 1

        top1 = texts[0][:28] if texts else "（空）"
        print(f"{q[:20]:<22}{new_kw:<14}{top1:<30}{verdict}")
        details.append({"q": q, "expect_new": new_kw, "expect_not": old_kw,
                        "kind": kind, "top1": top1,
                        "has_new": has_new, "has_old": has_old})

    print("-" * 90)
    print(f"  拿到新值: {ok_new}/{len(REPLACE_CASES)}")
    print(f"  返回旧值(过时污染): {bad_old}")
    print(f"  新旧都返回(矛盾未消解): {both}")
    print(f"  都没返回(丢失): {none_}")

    # ---------- ② 对照：不受矛盾影响的记忆应保持 ----------
    print("\n" + "=" * 90)
    print("② 对照：不受矛盾影响的基础记忆应保持")
    print("=" * 90)
    hit_ctrl = 0
    for q, kw in CONTROL_CASES:
        res = st.search(q, top=5)
        texts = [ep.raw_text for ep, *_ in res]
        ok = any(kw in t for t in texts[:3])
        hit_ctrl += 1 if ok else 0
        print(f"  {'✓' if ok else '✗'} {q[:20]:<22}{kw:<16}{(texts[0][:30] if texts else '（空）')}")
    print("-" * 90)
    print(f"  对照召回: {hit_ctrl}/{len(CONTROL_CASES)} = {hit_ctrl/len(CONTROL_CASES):.4f}")

    # ---------- ③ R2 真检验：数据是否全在（含失效）----------
    print("\n" + "=" * 90)
    print("③ 红线 R2：数据是否全在（不删除）")
    print("=" * 90)
    total = st.count()
    invalid = st.con.execute(
        "SELECT COUNT(*) FROM episodes WHERE invalid_at IS NOT NULL").fetchone()[0]
    valid = total - invalid
    old_findable = False
    try:
        r = st.search("8848", top=10, include_invalid=True)
        old_findable = any("8848" in ep.raw_text for ep, *_ in r)
    except Exception:
        pass

    print(f"  总记录: {total}（应为 {len(BASE) + len(CONFLICTS)}）")
    print(f"  有效: {valid}  已失效: {invalid}")
    print(f"  旧密码 8848 用 include_invalid 可查: {'✅' if old_findable else '❌'}")
    print(f"  数据完整: {'✅' if total == len(BASE) + len(CONFLICTS) else '❌'}")

    # ---------- 结论 ----------
    print("\n" + "=" * 90)
    print("结论")
    print("=" * 90)
    n = len(REPLACE_CASES)
    print(f"  被 replace 的问题拿到正确新值: {ok_new}/{n} = {ok_new/n:.4f}")
    print(f"  过时信息污染（返回旧值）:      {bad_old}/{n}")
    print(f"  对照记忆保持:                  {hit_ctrl}/{len(CONTROL_CASES)}")
    print(f"  数据完整性(R2):                {'通过' if total == len(BASE) + len(CONFLICTS) and old_findable else '失败'}")
    print()
    if ok_new == n and bad_old == 0 and hit_ctrl == len(CONTROL_CASES):
        print("  → 矛盾处理正确：问旧问题拿到新值，旧值不污染，其他记忆无损")
    elif bad_old > 0:
        print(f"  → 有 {bad_old} 条仍在返回过时旧值，矛盾未真正消解")
    else:
        print("  → 部分问题拿不到正确值，需排查检索")

    out = {"experiment": "E-117b", "n_replace": n,
           "got_new_value": ok_new, "returned_stale": bad_old,
           "returned_both": both, "returned_none": none_,
           "control_recall": hit_ctrl, "control_total": len(CONTROL_CASES),
           "total_records": total, "invalid_count": invalid,
           "old_value_findable": bool(old_findable),
           "details": details}
    with open(os.path.join(RESULTS, "e117b_correctness.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e117b_correctness.json')}")


if __name__ == "__main__":
    main()
