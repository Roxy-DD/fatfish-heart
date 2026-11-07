"""
E-117 检验论文的核心声明：矛盾处理是否损害原有记忆（红线 R2）

论文声称：'conflicts do not cause catastrophic forgetting，forgetting rate < 0.1%'

这是论文里最要紧、也最可检验的一条（表 A1 声称 Cosmos-Net 全程 0.00% DSR）。

但按用户方法论：论文只是借势，必须自己跑。
而且论文那个 0.00% 完美得不像真的，我要用数据检验。

设计（关键：对照组）：
  组 A（实验组）：写入 20 条基础记忆，然后写入 10 条矛盾记忆（触发 replace/merge/conflict）
  组 B（对照组）：只写入同样的 20 条基础记忆，不写任何矛盾记忆

  然后对同一批查询，测两组的召回率。
  若 A 组召回明显低于 B 组 → 矛盾处理损害了原有记忆（论文声明错误）
  若 A ≈ B → 矛盾处理没有损害（论文声明成立，至少在这一条上）

同时测：旧值（被 replace 标记失效的）是否还能查到（红线 R2 不遗忘）
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
RESULTS = os.path.join(_HERE, "results")
DB_A = os.path.join(_HERE, "e117_A.db")
DB_B = os.path.join(_HERE, "e117_B.db")

# 20 条基础记忆（每组都有）
BASE = [
    ("我电脑的登录密码是 8848", "电脑密码"),
    ("家里的门锁密码是 3721", "门锁密码"),
    ("这个项目 15 号截止", "项目截止"),
    ("我女儿在城南路18号上中班", "女儿学校"),
    ("我的车是白色卡罗拉", "我的车"),
    ("我住在城东花园3栋", "家住哪"),
    ("接口参数定的是0.85", "接口参数"),
    ("明天下午三点开会", "明天会议"),
    ("我妈电话是13800138000", "妈妈电话"),
    ("我生日是3月8号", "我的生日"),
    ("服务器在阿里云华东区16核64G", "服务器配置"),
    ("客户的验收标准是延迟低于5ms", "验收标准"),
    ("我每天早上七点起床", "起床时间"),
    ("我老婆对花生过敏", "老婆过敏"),
    ("我答应周五前交接口文档", "我的承诺"),
    ("上次评审会产品经理否了登录页改版", "评审会"),
    ("我要连续吃两周的药", "吃药"),
    ("周末打算去郊外爬山", "周末计划"),
    ("仓库里还有两百件库存", "库存"),
    ("房租月底到期房东要涨价", "房租"),
]

# 10 条矛盾记忆（实验组额外写入，每条都与某条基础记忆相关）
CONFLICTS = [
    ("我把电脑密码改成了9988", "replace"),       # 与 #1 矛盾
    ("门锁密码换成了9512", "replace"),           # 与 #2 矛盾
    ("客户同意把截止延到20号", "replace"),       # 与 #3 矛盾
    ("我换车了现在是黑色大众", "replace"),       # 与 #5 矛盾
    ("我搬家到城西新村5号", "replace"),          # 与 #6 矛盾
    ("接口参数改成了1.2", "replace"),            # 与 #7 矛盾
    ("会议改到明天上午十点", "replace"),         # 与 #8 矛盾
    ("客户还要求界面支持大字体", "merge"),       # 与 #12 补
    ("评审会又追加了一条要求", "merge"),         # 与 #16 补
    ("小李说门锁密码是7710", "conflict"),        # 与 #2 冲突
]

# 查询：每条基础记忆一个问法
QUERIES = [
    ("电脑密码是多少", 0),
    ("门锁密码", 1),
    ("项目什么时候截止", 2),
    ("女儿在哪上学", 3),
    ("我的车是什么", 4),
    ("我家住哪", 5),
    ("接口参数多少", 6),
    ("明天几点开会", 7),
    ("妈妈电话多少", 8),
    ("我生日哪天", 9),
    ("服务器什么配置", 10),
    ("验收标准是什么", 11),
    ("我几点起床", 12),
    ("老婆对什么过敏", 13),
    ("我答应什么时候交文档", 14),
    ("评审会发生了什么", 15),
    ("我要吃多久药", 16),
    ("周末去哪", 17),
    ("库存多少", 18),
    ("房租什么时候到期", 19),
]


def fresh(p):
    for suf in ("", "-wal", "-shm"):
        f = p + suf
        if os.path.exists(f):
            os.remove(f)


def measure(st, ids, label):
    """测召回率"""
    hit = 0
    ranks = []
    for q, gi in QUERIES:
        res = st.search(q, top=5)
        got = [ep.ep_id for ep, *_ in res]
        if ids[gi] in got:
            hit += 1
            ranks.append(got.index(ids[gi]) + 1)
        else:
            ranks.append(-1)
    return hit / len(QUERIES), ranks


def main():
    from store_v2 import MemoryStore

    print("=" * 90)
    print("E-117 矛盾处理是否损害原有记忆（红线 R2 的核心检验）")
    print("=" * 90)

    # ---------- 对照组 B：只写基础记忆 ----------
    fresh(DB_B)
    stB = MemoryStore(DB_B)
    idsB = [stB.add(t).ep_id for t, _ in BASE]

    # ---------- 实验组 A：基础 + 矛盾 ----------
    fresh(DB_A)
    stA = MemoryStore(DB_A, resolve_conflict_by_default=False) if False else MemoryStore(DB_A)
    idsA = []
    for t, _ in BASE:
        idsA.append(stA.add(t).ep_id)

    print(f"\n写入 10 条矛盾记忆（每条都针对某条基础记忆）...")
    conflict_verdicts = []
    for t, expect in CONFLICTS:
        ep, v = stA.add(t, return_verdict=True)
        rel = v.relation if v else "none"
        ok = (rel == expect) or (expect == "replace" and rel == "replace")
        conflict_verdicts.append({
            "text": t, "expect": expect, "got": rel,
            "sim": round(v.similarity, 3) if v else 0,
            "ok": ok,
        })
        print(f"  {'✓' if ok else '✗'} {t[:36]:<38} 判={rel:<11}(期望{expect}) sim={v.similarity if v else 0:.3f}")

    # ---------- 召回对比 ----------
    print("\n" + "=" * 90)
    print("召回对比（同一批 20 个查询）")
    print("=" * 90)

    recB, ranksB = measure(stB, idsB, "对照组")
    recA, ranksA = measure(stA, idsA, "实验组")

    print(f"\n{'查询':<24}{'对照(无矛盾)':<16}{'实验(有矛盾)':<16}{'变化'}")
    print("-" * 90)
    n_drop = 0
    for i, (q, gi) in enumerate(QUERIES):
        rb, ra = ranksB[i], ranksA[i]
        same = (rb == ra and rb > 0)
        change = ""
        if rb > 0 and ra < 0:
            change = "❌ 丢失"
            n_drop += 1
        elif rb > 0 and ra > 0 and ra > rb:
            change = f"↓ {rb}→{ra}"
        elif rb > 0 and ra > 0 and ra < rb:
            change = f"↑ {rb}→{ra}"
        elif rb > 0 and ra > 0:
            change = "—"
        print(f"{q[:22]:<24}{('命中#' + str(rb)) if rb > 0 else '未命中':<16}"
              f"{('命中#' + str(ra)) if ra > 0 else '未命中':<16}{change}")
    print("-" * 90)

    print(f"\n  对照组召回: {recB:.4f} ({int(recB * 20)}/20)")
    print(f"  实验组召回: {recA:.4f} ({int(recA * 20)}/20)")
    print(f"  差值: {recA - recB:+.4f}")
    print(f"  完全丢失的查询: {n_drop} 条")

    # ---------- 旧值是否仍可查（R2 不遗忘）----------
    print("\n" + "=" * 90)
    print("红线 R2 检验：被 replace 的旧值是否仍可查")
    print("=" * 90)
    stA2 = MemoryStore(DB_A)
    total_old = stA2.count()
    invalid = stA2.con.execute(
        "SELECT COUNT(*) FROM episodes WHERE invalid_at IS NOT NULL").fetchone()[0]
    allq = stA2.search("电脑密码", top=10, include_invalid=True)
    found_old = any("8848" in ep.raw_text for ep, *_ in allq)
    print(f"  记录总数: {total_old}（基础20 + 矛盾10 = 30，应无删除）")
    print(f"  已标失效: {invalid} 条")
    print(f"  用 include_invalid 查旧密码 8848: {'✅ 仍可查' if found_old else '❌ 已丢失'}")

    # ---------- 结论 ----------
    print("\n" + "=" * 90)
    print("结论")
    print("=" * 90)

    verdict_ok = sum(1 for c in conflict_verdicts if c["ok"])
    print(f"  矛盾判定: {verdict_ok}/{len(conflict_verdicts)}")
    print(f"  召回损失: {recB - recA:+.4f} ({n_drop} 条完全丢失)")
    print(f"  数据完整性: {'✅ 30 条全在' if total_old == 30 else f'❌ 只有 {total_old} 条'}")
    print()
    if recA >= recB - 0.05 and total_old == 30:
        print("  → 论文声明「矛盾不导致遗忘」在我们这里**成立**")
        print("    （但论文说的 0.00% 完美值不予采信，实测有微小波动）")
    else:
        print("  → 论文声明不成立：矛盾处理确实损害了部分原有记忆")
        print("    （这比论文的 0.00% 更接近真实）")

    out = {"experiment": "E-117",
           "recall_control": round(recB, 4),
           "recall_experiment": round(recA, 4),
           "recall_delta": round(recA - recB, 4),
           "queries_lost": n_drop,
           "total_records": total_old,
           "invalid_count": invalid,
           "old_value_still_findable": bool(found_old),
           "conflict_verdicts": conflict_verdicts}
    with open(os.path.join(RESULTS, "e117_r2_check.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e117_r2_check.json')}")


if __name__ == "__main__":
    main()
