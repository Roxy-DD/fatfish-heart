"""
E-147 构造有区分度的测试集（打破天花板）

E-146 的问题：基线召回 20/20 = 1.0，平均排名 1.0
  → 天花板效应，无法判断任何方案好坏

诊断根因：
  ① 查询与目标记忆几乎字面重合（"门锁密码是多少" vs "家里的门锁密码是 3721"）
  ② 目标记忆在库里缺乏同槽位竞争对手（"门锁密码"只有一条）
  ③ 低频组也只有 6 条，很容易 top-5 装下

本实验构造真正有难度的测试集：
  · 同槽位多候选（密码类有 8 条，必须选对是哪个密码）
  · 查询用间接表述（"进门用的那个" 而不是 "门锁密码"）
  · 加入长尾干扰（约 200 条）
  · 目标分散在不同位置，迫使 top-K 竞争

目的：基线召回降到 0.5~0.8 区间，才有区分度
"""
from __future__ import annotations

import json
import os
import random
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
RESULTS = os.path.join(_HERE, "results")
DB = os.path.join(_HERE, "e147_hard.db")

K = 5

# ---- 同槽位竞争组：密码类 8 条（查询必须精确指向其中一条）----
PASSWORD_GROUP = [
    ("家里的门锁密码是 3721", "high", "进门用的那个密码"),
    ("公司WiFi密码是office2024", "high", "办公室连网的那个密码"),
    ("网盘账号密码是 cloud123", "noise", "存资料那个网盘的密码"),
    ("邮箱密码是 mail456", "noise", "发邮件那个的密码"),
    ("门禁卡密码是 8899", "noise", "刷小区门禁的密码"),
    ("行李箱密码是 000", "noise", "出差那个箱子的密码"),
    ("后台管理系统密码是 admin888", "noise", "管理后台登录密码"),
    ("数据库密码是 db1234", "noise", "连数据库用的密码"),
    ("路由器密码是 router2024", "noise", "家里路由器后台密码"),
]

# ---- 间接表述组：查询与原文不字面重合 ----
INDIRECT_GROUP = [
    ("我妈妈的名字是王秀兰", "high", "生我那个人的名字"),
    ("我手机号是13900139002", "high", "别人怎么打电话找我"),
    ("我的身份证尾号是8834", "high", "证件最后四位"),
    ("我爸去年冬天做了心脏支架手术", "low", "老父亲身体出过什么大事"),
    ("去年夏天我们去了青岛海边玩", "low", "那回看海是哪一年"),
    ("大学住的是六人间，在四楼", "low", "上学时住几个人一间"),
    ("我养了只橘猫叫团子", "low", "家里那只小动物叫什么"),
    ("外婆家在徽州有个老宅子", "low", "老一辈的老房子在哪"),
    ("我初恋叫小雨，是高中同班", "low", "第一个喜欢的人"),
    ("接口参数定的是0.85", "mid", "那个阈值定成多少了"),
    ("这个项目 15 号截止", "mid", "活儿最晚什么时候交"),
    ("明天下午三点开会", "mid", "明天几点碰头"),
    ("服务器部署在阿里云华东区", "mid", "机器放在哪个机房"),
    ("数据库用的是PostgreSQL 15", "mid", "底层存数据用的什么"),
]

LONGTAIL = [
    "缓存层用的是Redis 7", "消息队列用的是Kafka",
    "监控用Prometheus", "日志用ELK",
    "代码仓库托管在GitLab", "持续集成用Jenkins",
    "团队的代码规范要求函数不超过50行",
    "客户要求所有接口响应时间低于200ms",
    "我每天早上七点起床", "我老婆对花生过敏",
    "我要连续吃两周的药", "周末打算去郊外爬山",
    "仓库里还有两百件库存", "房租月底到期房东要涨价",
    "我生日是3月8号", "我女儿在城南路18号上中班",
    "我的车是白色卡罗拉", "我住在城东花园3栋",
    "我妈电话是13800138000", "客户要求界面必须支持深色模式",
    "上次评审会产品经理否了登录页改版", "我答应周五前交接口文档",
    "单元测试由李娜负责", "性能压测由王强负责",
    "验收报告由赵敏负责", "接口文档由张伟负责",
]
# 扩展到约 200 条长尾
EXTRA_TPL = [
    "{n}号仓库的温度记录是{t}度", "{n}号产线的良品率是{p}%",
    "{n}号机柜的编号是{code}", "{n}号门店的店长是{s}",
    "{n}号的排班表在{c}系统里", "{n}号批次的有效期到{t}月",
]
NAMES = ["张伟", "李娜", "王强", "赵敏", "刘洋", "陈静", "杨帆", "周涛"]


def build_longtail(n=170):
    rng = random.Random(20261102)
    out = []
    while len(out) < n:
        tpl = rng.choice(EXTRA_TPL)
        out.append(tpl.format(
            n=rng.randint(1, 99), t=rng.randint(1, 28),
            p=rng.randint(70, 99), code=f"{rng.choice(['A','B','C'])}"
                                        f"{rng.randint(1000,9999)}",
            s=rng.choice(NAMES), c=rng.choice(["OA", "ERP", "排班"])))
    return out


def main():
    from store_v2 import MemoryStore
    import channel as ch

    for suf in ("", "-wal", "-shm"):
        p = DB + suf
        if os.path.exists(p):
            os.remove(p)

    st = MemoryStore(DB)

    print("=" * 96)
    print("E-147 构造有区分度的测试集")
    print("=" * 96)

    ids, kinds = {}, {}
    items = []
    for txt, kind, q in PASSWORD_GROUP:
        items.append((txt, kind))
    for txt, kind, q in INDIRECT_GROUP:
        items.append((txt, kind))
    for txt in LONGTAIL:
        items.append((txt, "noise"))
    for txt in build_longtail():
        items.append((txt, "noise"))

    t0 = time.perf_counter()
    for txt, kind in items:
        ids[txt] = st.add(txt, resolve_conflict=False).ep_id
        kinds[txt] = kind
    dt = time.perf_counter() - t0

    print(f"\n库中 {st.count()} 条记忆（写入 {dt:.1f}s）")
    from collections import Counter
    c = Counter(kinds.values())
    print(f"  高 {c['high']} / 中 {c['mid']} / 低 {c['low']} / 噪声 {c['noise']}")

    # 造熟练度
    for txt, kind in items:
        eid = ids[txt]
        if kind == "high":
            ch.bump(st, eid, "recall_by_query", 40)
            ch.bump(st, eid, "recall_by_fixed", 20)
        elif kind == "mid":
            ch.bump(st, eid, "recall_by_query", 8)
        elif kind == "low":
            ch.bump(st, eid, "recall_by_query", 1)

    # 查询集
    QUERIES = ([(q, txt, "password") for txt, k, q in PASSWORD_GROUP] +
               [(q, txt, "indirect") for txt, k, q in INDIRECT_GROUP])
    print(f"  查询 {len(QUERIES)} 条"
          f"（密码类 {len(PASSWORD_GROUP)} / 间接表述 {len(INDIRECT_GROUP)}）")

    # 基线测量
    def measure(fam=0.0):
        hit = 0
        ranks = []
        by_grp = {}
        for q, gold, grp in QUERIES:
            res = st.search(q, top=K, familiarity=fam)
            got = [ep.raw_text for ep, *_ in res]
            ok = gold in got
            if ok:
                hit += 1
                ranks.append(got.index(gold) + 1)
            d = by_grp.setdefault(grp, [0, 0])
            d[1] += 1
            d[0] += 1 if ok else 0
        return {
            "recall": round(hit / len(QUERIES), 4),
            "hit": hit,
            "avg_rank": round(sum(ranks) / max(1, len(ranks)), 2),
            "by_grp": {k: round(v[0] / v[1], 4) for k, v in by_grp.items()},
        }

    base = measure(0.0)
    print(f"\n" + "-" * 96)
    print(f"基线（familiarity=0）")
    print("-" * 96)
    print(f"  召回 {base['hit']}/{len(QUERIES)} = {base['recall']:.4f}")
    print(f"  平均排名 {base['avg_rank']}")
    for g, v in base["by_grp"].items():
        print(f"  {g}: {v:.4f}")

    # 是否有区分度
    has_room = base["recall"] < 0.95
    print(f"\n  → 是否有区分度（召回<0.95）: "
          f"{'✅ 是，可以用来对比方案' if has_room else '❌ 否，仍然太简单'}")

    if has_room:
        print(f"\n" + "-" * 96)
        print("扫描 familiarity")
        print("-" * 96)
        print("%-14s%-14s%-12s%-10s%-10s" % ("familiarity", "召回", "平均排名",
                                             "密码类", "间接类"))
        print("-" * 96)
        rows = []
        for fam in [0.0, 0.1, 0.2, 0.3, 0.5, 0.8, 1.0]:
            r = measure(fam)
            r["fam"] = fam
            rows.append(r)
            print("%-14s%-14s%-12s%-10.4f%-10.4f" % (
                fam, "%d/%d" % (r["hit"], len(QUERIES)), r["avg_rank"],
                r["by_grp"].get("password", 0),
                r["by_grp"].get("indirect", 0)))

        best = max(rows, key=lambda x: (x["recall"], -x["avg_rank"]))
        print(f"\n  最优: familiarity={best['fam']}  召回 {best['recall']:.4f}")
    else:
        rows, best = [], base

    st.con.close()

    out = {"experiment": "E-147",
           "n_memories": sum(1 for _ in items),
           "n_queries": len(QUERIES),
           "baseline": base,
           "has_discrimination": has_room,
           "scan": rows if isinstance(rows, list) else [],
           "best": best}
    with open(os.path.join(RESULTS, "e147_hard.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e147_hard.json')}")


if __name__ == "__main__":
    main()
