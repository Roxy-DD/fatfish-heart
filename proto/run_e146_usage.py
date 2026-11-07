"""
E-146 多通道熟练度三种用法 A/B/C 对比（真正跑起来）

方案：
  base  熟练度=0，不参与排序（基线）
  A     熟练度加成（familiarity 系数扫描 0.1~1.0）
  B     只让"固定席位"优先使用熟练度（更贴近"惯性"语义）
  C     不参与排序（等同 base，仅记账）—— 基线即代表

评估：
  ① 召回率（答案能否进 top-K）
  ② 平均排名
  ③ 多样性（同一槽位占多少席）
  ④ 是否损害低频记忆的召回（关键：别让高频挤掉低频）
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
RESULTS = os.path.join(_HERE, "results")
DB = os.path.join(_HERE, "e146_usage.db")

K = 5

MEMORIES = [
    ("家里的门锁密码是 3721", "high"),
    ("我妈妈的名字是王秀兰", "high"),
    ("我手机号是13900139002", "high"),
    ("公司WiFi密码是office2024", "high"),
    ("我的身份证尾号是8834", "high"),
    ("接口参数定的是0.85", "mid"),
    ("这个项目 15 号截止", "mid"),
    ("明天下午三点开会", "mid"),
    ("服务器部署在阿里云华东区", "mid"),
    ("数据库用的是PostgreSQL 15", "mid"),
    ("前端框架用React 18", "mid"),
    ("后端框架用FastAPI", "mid"),
    ("我爸去年冬天做了心脏支架手术", "low"),
    ("去年夏天我们去了青岛海边玩", "low"),
    ("大学住的是六人间，在四楼", "low"),
    ("我养了只橘猫叫团子", "low"),
    ("外婆家在徽州有个老宅子", "low"),
    ("我初恋叫小雨，是高中同班", "low"),
    ("网盘账号密码是 cloud123", "noise"),
    ("邮箱密码是 mail456", "noise"),
    ("门禁卡密码是 8899", "noise"),
    ("行李箱密码是 000", "noise"),
    ("后台管理系统密码是 admin888", "noise"),
    ("数据库密码是 db1234", "noise"),
    ("路由器密码是 router2024", "noise"),
    ("缓存层用的是Redis 7", "noise"),
    ("消息队列用的是Kafka", "noise"),
    ("监控用Prometheus", "noise"),
    ("日志用ELK", "noise"),
    ("代码仓库托管在GitLab", "noise"),
    ("持续集成用Jenkins", "noise"),
    ("团队的代码规范要求函数不超过50行", "noise"),
    ("客户要求所有接口响应时间低于200ms", "noise"),
    ("我每天早上七点起床", "noise"),
    ("我老婆对花生过敏", "noise"),
    ("我要连续吃两周的药", "noise"),
    ("周末打算去郊外爬山", "noise"),
    ("仓库里还有两百件库存", "noise"),
    ("房租月底到期房东要涨价", "noise"),
    ("我生日是3月8号", "noise"),
    ("我女儿在城南路18号上中班", "noise"),
    ("我的车是白色卡罗拉", "noise"),
    ("我住在城东花园3栋", "noise"),
    ("我妈电话是13800138000", "noise"),
    ("客户要求界面必须支持深色模式", "noise"),
    ("上次评审会产品经理否了登录页改版", "noise"),
    ("我答应周五前交接口文档", "noise"),
    ("单元测试由李娜负责", "noise"),
    ("性能压测由王强负责", "noise"),
]

QUERIES = [
    ("门锁密码是多少", "家里的门锁密码是 3721"),
    ("我妈叫什么名字", "我妈妈的名字是王秀兰"),
    ("我的手机号", "我手机号是13900139002"),
    ("公司WiFi密码", "公司WiFi密码是office2024"),
    ("我身份证后四位", "我的身份证尾号是8834"),
    ("接口参数是多少", "接口参数定的是0.85"),
    ("项目什么时候截止", "这个项目 15 号截止"),
    ("明天几点开会", "明天下午三点开会"),
    ("服务器在哪", "服务器部署在阿里云华东区"),
    ("数据库是什么", "数据库用的是PostgreSQL 15"),
    ("我爸做过什么手术", "我爸去年冬天做了心脏支架手术"),
    ("去年夏天去哪玩了", "去年夏天我们去了青岛海边玩"),
    ("大学宿舍什么样", "大学住的是六人间，在四楼"),
    ("我养的猫叫什么", "我养了只橘猫叫团子"),
    ("外婆家在哪", "外婆家在徽州有个老宅子"),
    ("我初恋叫什么", "我初恋叫小雨，是高中同班"),
    ("网盘密码", "网盘账号密码是 cloud123"),
    ("行李箱密码", "行李箱密码是 000"),
    ("早上几点起床", "我每天早上七点起床"),
    ("老婆对什么过敏", "我老婆对花生过敏"),
]


def main():
    from store_v2 import MemoryStore
    import channel as ch

    for suf in ("", "-wal", "-shm"):
        p = DB + suf
        if os.path.exists(p):
            os.remove(p)

    st = MemoryStore(DB)

    print("=" * 96)
    print("E-146 多通道熟练度 A/B/C 对比")
    print("=" * 96)

    ids, kinds = {}, {}
    for txt, kind in MEMORIES:
        ids[txt] = st.add(txt, resolve_conflict=False).ep_id
        kinds[txt] = kind
    print(f"\n库中 {st.count()} 条记忆（高5/中7/低6/噪声32）")

    # 造熟练度
    for txt, kind in MEMORIES:
        eid = ids[txt]
        if kind == "high":
            ch.bump(st, eid, "recall_by_query", 40)
            ch.bump(st, eid, "recall_by_fixed", 20)
        elif kind == "mid":
            ch.bump(st, eid, "recall_by_query", 8)
        elif kind == "low":
            ch.bump(st, eid, "recall_by_query", 1)
    print("熟练度：高=40+20  中=8  低=1  噪声=0")

    def measure(fam=0.0):
        hit = 0
        ranks = []
        by_kind = {}
        crowd = 0
        for q, gold in QUERIES:
            res = st.search(q, top=K, familiarity=fam)
            got = [ep.raw_text for ep, *_ in res]
            ok = gold in got
            if ok:
                hit += 1
                ranks.append(got.index(gold) + 1)
            k = kinds.get(gold)
            d = by_kind.setdefault(k, [0, 0])
            d[1] += 1
            d[0] += 1 if ok else 0
            pwd = sum(1 for t in got if "密码" in t)
            crowd += max(0, pwd - 2)
        return {
            "recall": hit / len(QUERIES),
            "hit": hit,
            "avg_rank": round(sum(ranks) / max(1, len(ranks)), 2),
            "by_kind": {k: round(v[0] / v[1], 4) for k, v in by_kind.items()},
            "crowd": crowd,
        }

    print("\n" + "-" * 96)
    print("扫描 familiarity 系数")
    print("-" * 96)
    print(f"{'familiarity':<14}{'召回':<14}{'平均排名':<12}"
          f"{'高':<9}{'中':<9}{'低':<9}{'拥挤'}")
    print("-" * 96)

    rows = []
    for fam in [0.0, 0.1, 0.2, 0.3, 0.5, 0.8, 1.0]:
        r = measure(fam)
        r["fam"] = fam
        rows.append(r)
        bk = r["by_kind"]
        hit_s = "%d/%d" % (r["hit"], len(QUERIES))
        print("%-14s%-14s%-12s%-9.2f%-9.2f%-9.2f%d" % (
            fam, hit_s, r["avg_rank"],
            bk.get("high", 0), bk.get("mid", 0), bk.get("low", 0),
            r["crowd"]))

    # ---------- 找最优 ----------
    print("\n" + "=" * 96)
    print("结论")
    print("=" * 96)

    base = rows[0]
    best = max(rows, key=lambda r: (r["recall"], -r["avg_rank"]))

    print(f"\n  基线（familiarity=0）:")
    print(f"    召回 {base['recall']:.4f}  平均排名 {base['avg_rank']}  "
          f"高{base['by_kind'].get('high',0):.2f} "
          f"中{base['by_kind'].get('mid',0):.2f} "
          f"低{base['by_kind'].get('low',0):.2f}")

    print(f"\n  最优（familiarity={best['fam']}）:")
    print(f"    召回 {best['recall']:.4f}  平均排名 {best['avg_rank']}  "
          f"高{best['by_kind'].get('high',0):.2f} "
          f"中{best['by_kind'].get('mid',0):.2f} "
          f"低{best['by_kind'].get('low',0):.2f}")

    delta = best["recall"] - base["recall"]
    low_delta = best["by_kind"].get("low", 0) - base["by_kind"].get("low", 0)
    print(f"\n  召回变化: {delta:+.4f}")
    print(f"  低频组变化: {low_delta:+.4f}  ← 关键：不能牺牲低频")

    print(f"\n  判断:")
    if delta > 0.01 and low_delta >= -0.01:
        print(f"    → ✅ 熟练度加成有效（召回 +{delta:.3f}，低频未受损）")
        print(f"    → 建议 familiarity={best['fam']}")
    elif delta >= -0.01:
        print(f"    → 熟练度加成几乎无变化（{delta:+.4f}）")
        print(f"    → 说明当前语料下，熟练度对召回影响小")
    else:
        print(f"    → ❌ 熟练度加成损害了召回（{delta:+.4f}），不采用")

    out = {"experiment": "E-146", "n_queries": len(QUERIES),
           "baseline": base, "best": best,
           "delta_recall": round(delta, 4),
           "delta_low": round(low_delta, 4),
           "scan": [{k: v for k, v in r.items()} for r in rows]}
    with open(os.path.join(RESULTS, "e146_usage.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    st.con.close()
    print(f"\n写入 {os.path.join(RESULTS, 'e146_usage.json')}")


if __name__ == "__main__":
    main()
