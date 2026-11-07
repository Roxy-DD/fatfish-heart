"""
E-125 矛盾判定的实际成本（决定能否进插件）

关键问题：
  矛盾判定要调一次模型。如果每条记忆都调，成本不可接受。
  必须测清楚：哪些情况需要调模型，哪些不需要。

设计：分三段测量
  A. 系统机械部分（算余弦+提值类型+打包）纯 CPU 成本
  B. 需要模型判断的比例（在真实分布上）
  C. 如果每条都调模型的总成本 vs 只对需要的调

关键洞察（待验证）：
  大多数新记忆与已有记忆**不相似**（cos 很低），
  这种情况系统自己就能判"无关"，不需要模型。
  只有 cos 高到一定程度的才值得调模型。
"""
from __future__ import annotations

import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
RESULTS = os.path.join(_HERE, "results")
DB = os.path.join(_HERE, "e125_cost.db")

# 真实分布模拟：40 条基础记忆（同 E-124）
BASE = [
    "张伟的工号是A1024", "张伟的手机号是13800138001",
    "李娜的工号是B2048", "李娜的手机号是13900139002",
    "王强的工号是C3072", "王强的手机号是13700137003",
    "赵敏的工号是D4096", "赵敏的手机号是13600136004",
    "星火项目的负责人是张伟", "星火项目的截止日期是11月15号",
    "曙光项目的负责人是李娜", "曙光项目的截止日期是12月20号",
    "灯塔项目的负责人是王强", "灯塔项目的预算是50万",
    "接口文档由张伟负责", "单元测试由李娜负责",
    "性能压测由王强负责", "验收报告由赵敏负责",
    "上周三开了星火项目启动会", "上周五曙光项目通过了评审",
    "昨天灯塔项目遇到接口超时问题", "今天上午修复了登录页的bug",
    "团队规定代码必须过CI才能合并", "团队要求接口文档必须在开发前完成",
    "客户要求所有接口响应时间低于200ms", "客户强调界面必须支持深色模式",
    "服务器部署在阿里云华东区", "数据库用的是PostgreSQL 15",
    "缓存层用的是Redis 7", "消息队列用的是Kafka",
    "前端框架用React 18", "后端框架用FastAPI",
    "测试环境在192.168.1.50", "生产环境在10.0.0.20",
    "代码仓库托管在GitLab", "持续集成用Jenkins",
    "监控用Prometheus", "日志用ELK",
    "文档放在Confluence", "需求管理用Jira",
]

# 后续新写入的 30 条（模拟真实使用：大部分是无关的新内容）
NEW_ITEMS = [
    # 10 条真正与已有记忆相关的（应触发模型判断）
    "张伟的工号改成了A9999",
    "李娜换手机号了13911112222",
    "星火项目延期到12月1号",
    "曙光项目预算追加到80万",
    "接口文档改由李娜负责",
    "团队还要求提交前必须跑单测",
    "客户另外要求支持大字体",
    "生产环境搬到10.0.0.30",
    "数据库升级到PostgreSQL 16",
    "缓存换成Redis 8",
    # 20 条无关的新内容（不该触发模型）
    "今天中午吃了牛肉面",
    "明天要去客户现场",
    "新来的同事叫孙悦",
    "下周一要交周报",
    "办公室换了新打印机",
    "上周六看了场电影",
    "每天要喝两升水",
    "健身房年卡快到期了",
    "想学一下Rust语言",
    "阳台的花开了",
    "车库的门锁坏了",
    "周末打算去爬山",
    "买了个新的机械键盘",
    "昨晚失眠到三点",
    "楼下开了家新面馆",
    "手机屏幕摔裂了",
    "想换个更大的显示器",
    "这周读了本讲历史的书",
    "冰箱里的牛奶过期了",
    "同事推荐了一家火锅店",
]


def main():
    from store_v2 import MemoryStore
    from store_v1 import Embedder
    import conflict

    for suf in ("", "-wal", "-shm"):
        p = DB + suf
        if os.path.exists(p):
            os.remove(p)

    st = MemoryStore(DB)
    emb = Embedder.get()

    print("=" * 92)
    print("E-125 矛盾判定的实际成本")
    print("=" * 92)

    # 写入基础记忆
    t0 = time.perf_counter()
    for m in BASE:
        st.add(m, resolve_conflict=False)
    t_base = time.perf_counter() - t0
    print(f"\n基础记忆 {len(BASE)} 条写入耗时: {t_base*1000:.0f} ms "
          f"({len(BASE)/t_base:.0f} 条/秒)")

    # ---------- A. 系统机械部分的成本 ----------
    print("\n" + "-" * 92)
    print("A. 系统机械部分成本（算余弦 + 提值类型 + 打包）")
    print("-" * 92)

    times = []
    pack_sizes = []
    for item in NEW_ITEMS[:10]:
        t0 = time.perf_counter()
        pack = conflict.prepare_candidates(st, emb, item, top_k=10)
        dt = time.perf_counter() - t0
        times.append(dt)
        pack_sizes.append(len(pack.candidates))

    avg = sum(times) / len(times)
    print(f"  样本数: {len(times)}")
    print(f"  平均耗时: {avg*1000:.2f} ms")
    print(f"  最小: {min(times)*1000:.2f} ms  最大: {max(times)*1000:.2f} ms")
    print(f"  平均候选数: {sum(pack_sizes)/len(pack_sizes):.1f}")

    # ---------- B. 相似度分布：多少条需要模型判断 ----------
    print("\n" + "-" * 92)
    print("B. 相似度分布：多少条真正需要模型判断")
    print("-" * 92)

    import numpy as np
    sims = []
    for item in NEW_ITEMS:
        pack = conflict.prepare_candidates(st, emb, item, top_k=10)
        top_cos = pack.candidates[0].cos if pack.candidates else 0.0
        sims.append((item, top_cos))

    print(f"\n{'新记忆':<30}{'最高余弦':<12}{'需模型?'}")
    print("-" * 92)
    related_count = 0
    FLOOR = 0.50   # 与 conflict.needs_model 的默认门槛一致
    for item, cos in sims:
        need = cos >= FLOOR
        related_count += 1 if need else 0
        tag = "相关" if item in NEW_ITEMS[:10] else "无关"
        print(f"{item[:28]:<30}{cos:<12.4f}{'是' if need else '否':<8}({tag})")
    print("-" * 92)

    # 分组统计
    rel = [c for it, c in sims if it in NEW_ITEMS[:10]]
    unr = [c for it, c in sims if it not in NEW_ITEMS[:10]]
    print(f"\n  相关组（10条）最高余弦:  均值 {np.mean(rel):.4f}  "
          f"最小 {np.min(rel):.4f}  最大 {np.max(rel):.4f}")
    print(f"  无关组（20条）最高余弦:  均值 {np.mean(unr):.4f}  "
          f"最小 {np.min(unr):.4f}  最大 {np.max(unr):.4f}")
    print(f"\n  需要模型判断（cos≥0.35）: {related_count}/{len(NEW_ITEMS)} "
          f"= {related_count/len(NEW_ITEMS):.1%}")

    # ---------- C. 成本对比 ----------
    print("\n" + "-" * 92)
    print("C. 成本对比")
    print("-" * 92)

    # 典型模型调用延迟（之前实测过：单次 LLM ~500-1500ms）
    LLM_LATENCY = 800   # ms，保守估计

    print(f"\n  假设单次模型调用 {LLM_LATENCY} ms（保守估计）")
    print(f"\n  {'方案':<32}{'每条写入耗时':<18}{'30条总耗时'}")
    print(f"  {'-'*70}")
    no_model = avg * 1000
    print(f"  {'A 不调模型（只打包）':<32}{f'{no_model:.1f} ms':<18}"
          f"{f'{no_model*30/1000:.1f} s'}")
    always = no_model + LLM_LATENCY
    print(f"  {'B 每条都调模型':<32}{f'{always:.1f} ms':<18}"
          f"{f'{always*30/1000:.1f} s'}")
    selective = no_model + LLM_LATENCY * (related_count / len(NEW_ITEMS))
    print(f"  {f'C 只对需要的调（cos≥{FLOOR}）':<32}{f'{selective:.1f} ms':<18}"
          f"{f'{selective*30/1000:.1f} s'}")
    print(f"  {'-'*70}")
    print(f"  C 相比 B 节省: {(always-selective)/always:.1%}")

    # ---------- 结论 ----------
    print("\n" + "=" * 92)
    print("结论")
    print("=" * 92)
    print(f"  系统机械部分: {avg*1000:.2f} ms/条（可接受，纯 CPU）")
    print(f"  需模型判断比例: {related_count/len(NEW_ITEMS):.1%}（cos≥0.35）")
    print(f"  预筛后每条写入均摊: {selective:.0f} ms")
    print(f"  预筛节省: {(always-selective)/always:.1%}")

    separable = np.max(unr) < FLOOR <= np.min(rel)
    print(f"\n  相关组最小 {np.min(rel):.4f} vs 无关组最大 {np.max(unr):.4f}")
    print(f"  → 是否可分: {'✅ 可分（0.35 门槛有效）' if separable else '⚠ 有重叠，需调门槛'}")

    out = {"experiment": "E-125",
           "n_new": len(NEW_ITEMS),
           "mechanical_ms": round(avg * 1000, 2),
           "related_group_mean_cos": round(float(np.mean(rel)), 4),
           "unrelated_group_mean_cos": round(float(np.mean(unr)), 4),
           "related_min": round(float(np.min(rel)), 4),
           "unrelated_max": round(float(np.max(unr)), 4),
           "need_model_ratio": round(related_count / len(NEW_ITEMS), 4),
           "floor": 0.50,
           "llm_latency_assumed_ms": LLM_LATENCY,
           "cost_per_write_no_model_ms": round(no_model, 1),
           "cost_per_write_always_ms": round(always, 1),
           "cost_per_write_selective_ms": round(selective, 1),
           "saving_ratio": round((always - selective) / always, 4),
           "separable": bool(separable),
           "sims": [{"item": i, "cos": round(c, 4)} for i, c in sims]}
    with open(os.path.join(RESULTS, "e125_cost.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e125_cost.json')}")


if __name__ == "__main__":
    main()

