"""
E-122 职责划分实验：系统做机械提取，模型做语义判断

用户的原则（2026-11-02）：
  "阈值和规则应该靠 AI 自己分析……要判断的地方，应该让模型自己来判断；
   但是，不应该让模型判断的地方、不需要模型做选择决策的地方，
   最好全部让系统直接接管完成。"

本实验的划分：
  系统接管（确定性，无判断空间）：
    · 余弦相似度计算
    · 值类型提取（日期/性能/金额/数量/编号）
    · 候选筛选（相似度 top-K）
    · 数据落盘
  模型判断（需要语义理解）：
    · 这条新记忆与候选旧记忆是什么关系（replace/merge/conflict/none）
    · 是否该作废旧值

对比三组：
  G1 硬编码词表（我之前的做法：REPLACE_WORDS 等）
  G2 纯余弦阈值（0.58 硬编码）
  G3 系统提取 + 模型判断（用户的原则）

生成给模型的任务包，让模型判断关系，再与 gold 比对。
"""
from __future__ import annotations

import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
RESULTS = os.path.join(_HERE, "results")
TASK = os.path.join(_HERE, "work", "e122")
os.makedirs(TASK, exist_ok=True)

# ============================================================
# 系统接管部分：值类型提取（纯机械，无判断）
# ============================================================
DATE_PAT = re.compile(
    r"(\d{1,2}\s*[号日]|\d{1,2}\s*月|\d{1,2}月\d{1,2}|"
    r"下周[一二三四五六日]|这周|明天|后天|月底|月初|年底|周[一二三四五六日])")
PERF_PAT = re.compile(r"(\d+\.?\d*\s*(ms|毫秒|秒|s|核|G|GB|MB|TB|%|倍|Mbps))")
MONEY_PAT = re.compile(r"(\d+\.?\d*\s*(元|块|万|美元|RMB))")
COUNT_PAT = re.compile(r"(\d+\.?\d*\s*(个|件|条|台|人|次|天|周|月|年|小时))")
CODE_PAT = re.compile(r"(\d{3,}|[A-Za-z]{2,}\d+|[\d\.]{2,})")


def extract_value_types(text):
    """系统机械提取：句中含有哪些类型的'值'"""
    t = text or ""
    types = []
    if DATE_PAT.search(t):
        types.append("date")
    if PERF_PAT.search(t):
        types.append("perf")
    if MONEY_PAT.search(t):
        types.append("money")
    if COUNT_PAT.search(t):
        types.append("count")
    if CODE_PAT.search(t) and not types:
        types.append("numeric")
    if not types:
        types.append("no_value")
    return types


def extract_value_tokens(text):
    """系统机械提取：具体的值本身（供模型参考）"""
    t = text or ""
    vals = []
    for pat in (DATE_PAT, PERF_PAT, MONEY_PAT, COUNT_PAT, CODE_PAT):
        for m in pat.finditer(t):
            v = m.group(0).strip()
            if v and v not in vals:
                vals.append(v)
    return vals[:6]


# ============================================================
# 测试集：新记忆 + 候选旧记忆（两条：正确目标 / 干扰项）
# ============================================================
CASES = [
    {
        "new": "客户同意把截止延到20号",
        "target": "这个项目 15 号截止",
        "distractor": "客户的验收标准是延迟低于5ms",
        "gold": "replace",
    },
    {
        "new": "门锁密码换成了9512",
        "target": "家里的门锁密码是 3721",
        "distractor": "我电脑的登录密码是 8848",
        "gold": "replace",
    },
    {
        "new": "接口参数改成了1.2",
        "target": "接口参数定的是0.85",
        "distractor": "这个项目 15 号截止",
        "gold": "replace",
    },
    {
        "new": "明天上午十点开会",
        "target": "明天下午三点开会",
        "distractor": "我要连续吃两周的药",
        "gold": "replace",
    },
    {
        "new": "我换车了现在是黑色大众",
        "target": "我的车是白色卡罗拉",
        "distractor": "我住在城东花园3栋",
        "gold": "replace",
    },
    {
        "new": "我搬家到城西新村5号",
        "target": "我住在城东花园3栋",
        "distractor": "我女儿在城南路18号上中班",
        "gold": "replace",
    },
    {
        "new": "客户还要求界面必须支持大字体",
        "target": "客户要求界面必须支持深色模式",
        "distractor": "客户的验收标准是延迟低于5ms",
        "gold": "merge",
    },
    {
        "new": "另外我晚上也要吃一次药",
        "target": "我要连续吃两周的药",
        "distractor": "我每天早上七点起床",
        "gold": "merge",
    },
    {
        "new": "小李说门锁密码是9512",
        "target": "老王说门锁密码是 3721",
        "distractor": "家里的门锁密码是 3721",
        "gold": "conflict",
    },
    {
        "new": "我记得好像是18号截止",
        "target": "这个项目 15 号截止",
        "distractor": "客户的验收标准是延迟低于5ms",
        "gold": "conflict",
    },
    {
        "new": "这个bug反复出现我快崩溃了",
        "target": "我电脑的登录密码是 8848",
        "distractor": "我生日是3月8号",
        "gold": "none",
    },
    {
        "new": "上周二午饭吃的是番茄炒蛋",
        "target": "我的车是白色卡罗拉",
        "distractor": "仓库里还有两百件库存",
        "gold": "none",
    },
    {
        "new": "我妈打电话说家里都好",
        "target": "这个项目 15 号截止",
        "distractor": "我女儿在城南路18号上中班",
        "gold": "none",
    },
    {
        "new": "孩子第一次叫妈妈我眼泪都出来了",
        "target": "我生日是3月8号",
        "distractor": "我老婆对花生过敏",
        "gold": "none",
    },
]


def main():
    from store_v2 import MemoryStore
    from store_v1 import Embedder
    import conflict

    DB = os.path.join(TASK, "e122.db")
    for suf in ("", "-wal", "-shm"):
        p = DB + suf
        if os.path.exists(p):
            os.remove(p)

    st = MemoryStore(DB)
    emb = Embedder.get()

    print("=" * 92)
    print("E-122 职责划分：系统提取 + 模型判断")
    print("=" * 92)
    print(f"\n{len(CASES)} 组测试\n")

    # 把 target 和 distractor 都写进库（不触发判定）
    all_texts = set()
    for c in CASES:
        all_texts.add(c["target"])
        all_texts.add(c["distractor"])
    ids = {}
    for t in sorted(all_texts):
        ep = st.add(t, resolve_conflict=False)
        ids[t] = ep.ep_id

    print(f"库中 {len(ids)} 条候选\n")

    # ---------- 系统接管部分：为每组准备候选包 ----------
    pack = []
    sys_meta = []

    for i, c in enumerate(CASES):
        new = c["new"]
        nv = emb.encode(new)[0]

        # 系统：算余弦、提值类型
        cands = []
        for txt, eid in ids.items():
            if txt == new:
                continue
            ov = emb.encode(txt)[0]
            cos = float(nv @ ov / ((nv @ nv) ** 0.5 * (ov @ ov) ** 0.5))
            cands.append({
                "text": txt,
                "cos": round(cos, 4),
                "value_types": extract_value_types(txt),
                "value_tokens": extract_value_tokens(txt),
            })
        cands.sort(key=lambda x: -x["cos"])
        top = cands[:3]

        pack.append({
            "cid": f"S{i:02d}",
            "new_memory": new,
            "new_value_types": extract_value_types(new),
            "new_value_tokens": extract_value_tokens(new),
            "candidates": top,
        })
        sys_meta.append({"cid": f"C{i:02d}", "gold": c["gold"],
                         "target": c["target"]})

    with open(os.path.join(TASK, "pack.json"), "w", encoding="utf-8") as f:
        json.dump(pack, f, ensure_ascii=False, indent=1)
    with open(os.path.join(TASK, "gold.json"), "w", encoding="utf-8") as f:
        json.dump(sys_meta, f, ensure_ascii=False, indent=1)

    # ---------- G1 硬编码词表 ----------
    print("-" * 92)
    print("G1 硬编码词表（我之前的做法）")
    print("-" * 92)
    g1_hit = 0
    for i, c in enumerate(CASES):
        rel, why = conflict.classify_relation(c["new"])
        # 词表法不看候选，只看新句
        pred = rel if rel != "none" else "none"
        ok = (pred == c["gold"])
        g1_hit += 1 if ok else 0
        print(f"  {'✓' if ok else '✗'} [{c['gold']:<10}] {c['new'][:30]:<32}→ {pred}")
    print("-" * 92)
    print(f"  G1 准确率: {g1_hit}/{len(CASES)} = {g1_hit/len(CASES):.4f}")

    # ---------- G2 纯余弦阈值 ----------
    print("\n" + "-" * 92)
    print("G2 纯余弦 + 硬阈值 0.58")
    print("-" * 92)
    g2_hit = 0
    for i, c in enumerate(CASES):
        new = c["new"]
        nv = emb.encode(new)[0]
        best_txt, best_cos = "", 0
        for txt, eid in ids.items():
            if txt == new:
                continue
            ov = emb.encode(txt)[0]
            cos = float(nv @ ov)
            if cos > best_cos:
                best_txt, best_cos = txt, cos
        if best_cos < 0.58:
            pred = "none"
        else:
            rel, _ = conflict.classify_relation(new)
            pred = rel if rel in ("replace", "merge", "conflict") else "conflict"
        ok = (pred == c["gold"])
        g2_hit += 1 if ok else 0
        print(f"  {'✓' if ok else '✗'} [{c['gold']:<10}] {new[:30]:<32}→ {pred} (cos={best_cos:.3f})")
    print("-" * 92)
    print(f"  G2 准确率: {g2_hit}/{len(CASES)} = {g2_hit/len(CASES):.4f}")

    print("\n" + "=" * 92)
    print("已生成任务包，供 G3（模型判断）使用")
    print("=" * 92)
    print(f"\n  {TASK}/pack.json")
    print(f"  每组包含：新记忆 + 值类型 + 3 个候选（含余弦与值类型）")
    print(f"\n  对比基线：G1 词表 {g1_hit}/{len(CASES)}  G2 纯余弦 {g2_hit}/{len(CASES)}")


if __name__ == "__main__":
    main()
