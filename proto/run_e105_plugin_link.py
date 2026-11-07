"""
E-105 插件链路准确率基线（P0）

背景：
  之前 94% / 1.0000 这些数字都是直连 store_v2 测的。
  现在记忆系统装进了 DSH 插件，链路变成：
    DSH → 子进程 python cli_mcp.py → store_v2
  必须验证：经过这条链路后，结果是否仍然一致。

设计：
  直连 DB（e105_direct.db）与链路 DB（e105_link.db）各写同一批语料，
  然后同一批查询分别用两条路径执行，比对命中是否一致。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
RESULTS = os.path.join(_HERE, "results")
KERNEL = os.path.join(_HERE, "cosmos_mem", "cli_mcp.py")
PY = sys.executable

DIRECT_DB = os.path.join(_HERE, "cosmos_mem", "e105_direct.db")
LINK_DB = os.path.join(_HERE, "cosmos_mem", "e105_link.db")

CORPUS = [
    ("我电脑的登录密码是 8848", True),
    ("家里的门锁密码是 3721", True),
    ("下个月 15 号是项目的截止日期，逾期不候", False),
    ("这个千万别搞错，搞错了要出大事", False),
    ("小时候被邻居家的狗追着咬过，小腿上留了疤", False),
    ("爷爷去世了，那阵子我一直在医院和家之间来回跑", False),
    ("他说不行", False),
    ("我的车是白色的卡罗拉，车牌沪A·5X29K", True),
    ("钱包丢了，里面有身份证和所有的卡", False),
    ("今天开完会感觉整体方向还行，细节再讨论一下", False),
]

QUERIES = [
    ("电脑密码是多少", "我电脑的登录密码是 8848"),
    ("门锁密码", "家里的门锁密码是 3721"),
    ("什么时候截止", "下个月 15 号是项目的截止日期，逾期不候"),
    ("千万别搞错", "这个千万别搞错，搞错了要出大事"),
    ("被狗咬", "小时候被邻居家的狗追着咬过，小腿上留了疤"),
    ("爷爷怎么了", "爷爷去世了，那阵子我一直在医院和家之间来回跑"),
    ("我的车牌", "我的车是白色的卡罗拉，车牌沪A·5X29K"),
    ("钱包里有什么", "钱包丢了，里面有身份证和所有的卡"),
]


def fresh(db):
    for suf in ("", "-wal", "-shm"):
        p = db + suf
        if os.path.exists(p):
            os.remove(p)


def call_kernel(db, cmd, payload):
    """走子进程调用内核（模拟插件的真实行为）"""
    p = subprocess.run(
        [PY, KERNEL, "--db", db, "--cmd", cmd],
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8",
             "KMP_DUPLICATE_LIB_OK": "TRUE"})
    if p.returncode != 0:
        return {"ok": False, "error": (p.stderr or "")[-300:]}
    try:
        return json.loads(p.stdout)
    except Exception:
        return {"ok": False, "error": "non-JSON: " + (p.stdout or "")[:200]}


def main():
    from store_v2 import MemoryStore

    print("=" * 88)
    print("E-105 插件链路准确率基线")
    print("=" * 88)

    # ---------- 直连写入 ----------
    fresh(DIRECT_DB)
    st = MemoryStore(DIRECT_DB)
    for text, explicit in CORPUS:
        st.add(text, explicit=explicit)
    print(f"\n直连写入 {st.count()} 条")

    # ---------- 链路写入 ----------
    fresh(LINK_DB)
    for text, explicit in CORPUS:
        call_kernel(LINK_DB, "add", {"text": text, "explicit": explicit})
    st2 = MemoryStore(LINK_DB)
    print(f"链路写入 {st2.count()} 条")

    # ---------- 查询一致性 ----------
    print("\n" + "-" * 88)
    print(f"{'查询':<22}{'直连':<8}{'链路':<8}{'一致':<7}{'直连条数':<10}{'链路条数'}")
    print("-" * 88)

    agree = 0
    for q, expected in QUERIES:
        d_res = st.search(q, top=5)
        d_hit = any(ep.raw_text == expected for ep, *_ in d_res)

        l_ret = call_kernel(LINK_DB, "search", {"query": q, "top": 5,
                                                "scoped": False})
        l_items = l_ret.get("results") or []
        l_hit = any(it.get("text") == expected for it in l_items)

        same = (d_hit == l_hit)
        agree += 1 if same else 0
        print(f"{q[:20]:<22}{str(d_hit):<8}{str(l_hit):<8}"
              f"{'✓' if same else '✗':<7}{len(d_res):<10}{len(l_items)}")
    print("-" * 88)
    n = len(QUERIES)
    print(f"\n链路 vs 直连 命中一致: {agree}/{n} = {agree/n:.4f}")

    # ---------- scoped 维度路由一致性 ----------
    print("\n" + "-" * 88)
    print("scoped（维度路由）一致性")
    print("-" * 88)
    s_agree = 0
    s_total = 0
    for q, _ in QUERIES[:5]:
        _, d_dims, _ = st.search_scoped(q, top=3)
        l_ret = call_kernel(LINK_DB, "search", {"query": q, "top": 3,
                                                "scoped": True})
        l_dims = l_ret.get("dims") or []
        same = (list(d_dims) == list(l_dims))
        s_total += 1
        s_agree += 1 if same else 0
        print(f"{q[:20]:<22} 直连={d_dims} 链路={l_dims} "
              f"{'✓' if same else '✗'}")
    print("-" * 88)
    print(f"scoped 一致: {s_agree}/{s_total}")

    # ---------- 固定席位一致性（自动注入用的就是这个）----------
    print("\n" + "-" * 88)
    print("固定席位（cost 最高）一致性 —— 自动注入就用这个")
    print("-" * 88)
    # 注意：两个 DB 独立写入，ep_id 是 uuid，必然不同
    # 所以要比较「内容」而非「ep_id」
    d_slots = [r[0] for r in st.con.execute(
        "SELECT raw_text FROM episodes WHERE invalid_at IS NULL AND cost>=0.7 "
        "ORDER BY cost DESC LIMIT 4").fetchall()]
    l_ret = call_kernel(LINK_DB, "search",
                        {"query": "", "top": 4, "fixed_slots": 4,
                         "min_cost": 0.7})
    l_slots = [it["text"] for it in (l_ret.get("results") or [])]
    print(f"  直连席位内容:")
    for s in d_slots:
        print(f"    - {s}")
    print(f"  链路席位内容:")
    for s in l_slots:
        print(f"    - {s}")
    slot_agree = (d_slots == l_slots)
    print(f"  内容与顺序完全一致: {slot_agree}")

    # ---------- stats 一致性 ----------
    print("\n" + "-" * 88)
    print("stats 一致性")
    print("-" * 88)
    d_stats = st.stats()
    l_stats = call_kernel(LINK_DB, "stats", {})
    keys = ["total", "valid", "incomplete", "identity_can"]
    stat_agree = all(d_stats.get(k) == l_stats.get(k) for k in keys)
    for k in keys:
        print(f"  {k:<14} 直连={d_stats.get(k)} 链路={l_stats.get(k)}")
    print(f"  一致: {stat_agree}")

    # ---------- 汇总 ----------
    print("\n" + "=" * 88)
    print("结论")
    print("=" * 88)
    all_ok = (agree == n and s_agree == s_total and slot_agree and stat_agree)
    print(f"  查询命中一致率: {agree/n:.4f}")
    print(f"  scoped 一致率:  {s_agree/s_total:.4f}")
    print(f"  固定席位一致:   {slot_agree}")
    print(f"  stats 一致:     {stat_agree}")
    print(f"\n  → {'插件链路与直连完全等价' if all_ok else '存在差异，需定位'}")

    out = {"experiment": "E-105", "n_queries": n, "hit_agree": agree,
           "hit_agree_rate": round(agree / n, 4),
           "scoped_agree": s_agree, "scoped_total": s_total,
           "slot_agree": bool(slot_agree), "stats_agree": bool(stat_agree),
           "all_equivalent": bool(all_ok),
           "direct_count": st.count(), "link_count": st2.count()}
    with open(os.path.join(RESULTS, "e105_plugin_link.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e105_plugin_link.json')}")


if __name__ == "__main__":
    main()
