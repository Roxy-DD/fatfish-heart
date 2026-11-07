"""
chain.py —— 链式展开（按"系统给候选 + 模型判链条"重做）

E-141~E-143 的实验结论：

  现有 store_v1._expand 的三条通路全部失效：
    ① TRIGGER_SCENE 只有 13 个硬编码场景 → 真实文本几乎命中不了 → triggers 恒空
    ② 展开阈值 DECAY=0.75 太高 → 链式目标余弦只有 0.42~0.52，永远过不了
    ③ 情绪共振要求 affect≥0.15 且 valence≠0 → 中性叙事恒为 0

  而 E-143 实测：链式目标在 seed 的 top-20 候选池里 **7/7 全部命中，平均排名第 2**。

  → 结论：系统侧的候选生成完全够用，失败纯粹是规则阈值掐掉的。
  → 按用户的职责划分原则：阈值和"是否同链"的判断交给模型，系统只做候选生成。

本模块职责：
  系统：取 seed 的 top-N 候选（不做阈值判断）→ 打包
  模型：判断哪些候选属于同一情景链 → 返回原文列表
  系统：把模型选中的候选追加到检索结果
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class ChainPack:
    """交给模型的链式候选包"""
    seed_text: str
    query: str = ""
    candidates: list = field(default_factory=list)   # [{text, cos}]
    total_memories: int = 0


def prepare_chain_candidates(st, embedder, seed_text: str, query: str = "",
                              top_n: int = 20) -> ChainPack:
    """
    系统准备链式候选（纯机械，不判断"是否同链"）。

    E-143 实测：链式目标在 top-20 里 7/7 命中，平均排名第 2。
    所以 top_n=20 足够；不设余弦阈值 —— 阈值留给模型判断。
    """
    import numpy as np

    st._load_vectors(include_invalid=False)
    if st._vec is None or len(st._vec) == 0:
        return ChainPack(seed_text=seed_text, query=query, candidates=[])

    sv = embedder.encode(seed_text)[0]
    norms = np.linalg.norm(st._vec, axis=1)
    norms[norms == 0] = 1.0
    sims = (st._vec @ sv) / (norms * (float(np.linalg.norm(sv)) or 1.0))

    k = min(top_n, len(sims))
    idx = np.argpartition(-sims, k - 1)[:k]
    idx = idx[np.argsort(-sims[idx])]

    rows = st.con.execute(
        "SELECT ep_id, raw_text FROM episodes WHERE ep_id IN (%s)"
        % ",".join("?" * len(idx)),
        [st._vec_ids[int(i)] for i in idx]).fetchall()
    text_of = {e: t for e, t in rows}

    cands = []
    for i in idx:
        eid = st._vec_ids[int(i)]
        txt = text_of.get(eid, "")
        if not txt or txt == seed_text:
            continue
        cands.append({"text": txt, "cos": round(float(sims[int(i)]), 4)})

    return ChainPack(seed_text=seed_text, query=query,
                     candidates=cands, total_memories=len(sims))


def chain_to_prompt(pack: ChainPack) -> dict:
    """转成给模型的结构"""
    return {
        "query": pack.query,
        "seed": pack.seed_text,
        "candidates": pack.candidates,
        "total_memories": pack.total_memories,
    }


def parse_chain_output(raw: str, pack: ChainPack) -> list[str]:
    """
    解析模型输出（机械解析，不做语义判断）。

    模型应返回：{"chain": ["<候选原文>", ...], "reason": "...", "confidence": 0-1}
    """
    import re

    if not raw:
        return []
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return []
    try:
        d = json.loads(m[0])
    except Exception:
        return []
    if not isinstance(d, dict):
        return []

    chain = d.get("chain")
    if not isinstance(chain, list):
        return []

    # 机械校验：只接受候选池里真实存在的原文
    valid_texts = {c["text"] for c in pack.candidates}
    out = []
    for item in chain:
        s = str(item).strip()
        if s in valid_texts and s not in out:
            out.append(s)
    return out


def apply_chain(st, base_results: list, chain_texts: list[str]) -> list:
    """
    把链式召回的条目追加到检索结果（系统机械执行）。

    base_results 是 search() 的原返回格式 [(ep, score, flag), ...]
    追加项的 score 标为 0（表示不是直接命中，是链式到达），
    flag 标为 2（区分：0=直接, 1=固定席位, 2=链式）
    """
    if not chain_texts:
        return base_results

    seen = {ep.ep_id for ep, _, _ in base_results}
    out = list(base_results)

    rows = st.con.execute(
        "SELECT ep_id, raw_text FROM episodes "
        "WHERE invalid_at IS NULL AND raw_text IN (%s)"
        % ",".join("?" * len(chain_texts)), chain_texts).fetchall()

    for eid, _txt in rows:
        if eid in seen:
            continue
        ep = st.get(eid)
        if ep:
            seen.add(eid)
            out.append((ep, 0.0, 2))   # flag=2 表示链式到达

    return out


def chain_of(st, ep_id: str) -> dict:
    """读取某次链式展开的记录（UI / 审计用）"""
    row = st.con.execute("SELECT val FROM meta WHERE key=?",
                         (f"chain:{ep_id}",)).fetchone()
    if not row:
        return {}
    try:
        return json.loads(row[0])
    except Exception:
        return {}


def log_chain(st, seed_ep_id: str, pack: ChainPack,
              chain_texts: list[str], reason: str = "") -> None:
    """记录链式展开的审计"""
    rec = {
        "seed_ep_id": seed_ep_id,
        "seed_text": pack.seed_text,
        "query": pack.query,
        "n_candidates": len(pack.candidates),
        "chain": chain_texts,
        "reason": reason,
    }
    try:
        st.con.execute("INSERT OR REPLACE INTO meta VALUES (?,?)",
                       (f"chain:{seed_ep_id}",
                        json.dumps(rec, ensure_ascii=False)))
        st.con.commit()
    except Exception:
        pass
