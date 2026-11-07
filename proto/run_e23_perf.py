"""E-23 性能基准测试：大规模数据下的检索/写入延迟

目标：确认系统在 100 / 1000 / 5000 条记忆规模下的性能表现，
找出瓶颈点，为后续优化提供依据。

测试项：
  ① 写入延迟（单条 add）
  ② 检索延迟（search，含/不含 rerank）
  ③ 审计日志写入开销
  ④ 睡眠 prepare 延迟（扫描最近 N 条 + 两两相似）

红线约束：
  · 不测 rerank=True（需要 GPU 模型，单独测）
  · 只测正确性和延迟，不改数据
"""
import sys
import os
import time
import random

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))

from store_v2 import MemoryStore
import cli_mcp

# 中文语料池（模拟真实记忆）
TOPICS = [
    "登录密码", "测试服务器", "数据库地址", "接口超时", "部署脚本",
    "用户偏好", "项目截止日期", "会议时间", "代码规范", "性能指标",
    "报销流程", "客户名称", "产品版本", "配置文件", "日志路径",
]
VERBS = ["是", "改成", "我记得", "要求", "约定", "需要注意", "忘了告诉"]
VALUES = ["8848", "5678", "200ms", "下周五", "production", "v1.2.3",
          "10 个并发", "根目录", "北京", "每周一次"]
TAILS = ["", "，别忘了", "，重要", "，请确认", "，谢谢"]

random.seed(42)


def gen_mem(i, scale):
    """生成第 i 条记忆"""
    t = random.choice(TOPICS)
    v = random.choice(VERBS)
    val = random.choice(VALUES)
    tail = random.choice(TAILS)
    # 加入序号保证唯一（避免去重）
    return f"{t} {v} {val}（记录 {i}）{tail}"


def bench(scale):
    """在指定规模下测性能"""
    db = os.path.join(_HERE, "work", f"e23_{scale}.db")
    os.makedirs(os.path.join(_HERE, "work"), exist_ok=True)
    for suf in ("", "-wal", "-shm"):
        p = db + suf
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass

    st = MemoryStore(db)

    # ---- 写入基准 ----
    t0 = time.perf_counter()
    for i in range(scale):
        st.add(gen_mem(i, scale), resolve_conflict=False)
    write_total = time.perf_counter() - t0

    # ---- 检索基准 ----
    queries = [gen_mem(random.randint(0, scale - 1), scale) for _ in range(20)]
    t0 = time.perf_counter()
    for q in queries:
        st.search(q, top=10)
    search_total = time.perf_counter() - t0

    # ---- 审计日志开销 ----
    import audit as _audit_mod
    t0 = time.perf_counter()
    for i in range(100):
        _audit_mod.log(st, "warn", msg=f"bench {i}")
    audit_total = time.perf_counter() - t0

    # ---- 睡眠 prepare 延迟 ----
    from store_v1 import Embedder as _Emb
    import consolidate as _con
    t0 = time.perf_counter()
    pack = _con.prepare_sleep_pack(st, _Emb.get(), limit=20, recent_n=50)
    sleep_total = time.perf_counter() - t0

    n = st.count()
    st.con.close()

    return {
        "scale": scale,
        "n_memories": n,
        "write_ms_per": round(write_total / scale * 1000, 2),
        "write_total_s": round(write_total, 2),
        "search_ms_per": round(search_total / len(queries) * 1000, 2),
        "audit_ms_per": round(audit_total / 100 * 1000, 2),
        "sleep_prepare_ms": round(sleep_total * 1000, 1),
        "sleep_pairs": len(pack.pairs),
    }


def main():
    print("=" * 82)
    print("E-23 性能基准测试")
    print("=" * 82)

    results = []
    for scale in (100, 500, 2000):
        print(f"\n【规模 {scale}】写入中…")
        r = bench(scale)
        results.append(r)
        print(f"  记忆数: {r['n_memories']}")
        print(f"  写入: {r['write_ms_per']} ms/条（总计 {r['write_total_s']}s）")
        print(f"  检索: {r['search_ms_per']} ms/次")
        print(f"  审计: {r['audit_ms_per']} ms/条")
        print(f"  睡眠prepare: {r['sleep_prepare_ms']} ms（{r['sleep_pairs']} 对）")

    # ---- 汇总 ----
    print("\n" + "=" * 82)
    print("汇总")
    print("=" * 82)
    print(f"\n{'规模':<8}{'写入ms/条':<12}{'检索ms/次':<12}{'审计ms/条':<12}{'睡眠ms'}")
    print("-" * 60)
    for r in results:
        print(f"{r['scale']:<8}{r['write_ms_per']:<12}{r['search_ms_per']:<12}"
              f"{r['audit_ms_per']:<12}{r['sleep_prepare_ms']}")

    # 结论
    print("\n" + "=" * 82)
    print("结论")
    print("=" * 82)
    s100 = results[0]
    s2000 = results[-1]
    write_growth = s2000["write_ms_per"] / max(0.01, s100["write_ms_per"])
    search_growth = s2000["search_ms_per"] / max(0.01, s100["search_ms_per"])
    print(f"\n  写入延迟增长: {write_growth:.1f}x（100→2000 条，20倍规模）")
    print(f"  检索延迟增长: {search_growth:.1f}x")
    print(f"\n  判断：")
    if search_growth < 5:
        print(f"    → ✅ 检索延迟增长远小于数据增长，索引有效")
    else:
        print(f"    → ⚠ 检索延迟增长 {search_growth:.1f}x，需要优化")
    if s2000["search_ms_per"] < 200:
        print(f"    → ✅ 2000 条规模下检索 {s2000['search_ms_per']}ms，可用")
    else:
        print(f"    → ⚠ 2000 条规模下检索 {s2000['search_ms_per']}ms，偏慢")

    import json
    os.makedirs(os.path.join(_HERE, "results"), exist_ok=True)
    with open(os.path.join(_HERE, "results", "e23_perf.json"), "w",
              encoding="utf-8") as f:
        json.dump({"experiment": "E-23", "results": results}, f,
                  ensure_ascii=False, indent=1)
    print(f"\n写入 results/e23_perf.json")


if __name__ == "__main__":
    main()
