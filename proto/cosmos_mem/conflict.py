"""
conflict.py v2 —— 职责严格划分版（按用户 2026-11-02 原则）

原则：
  "阈值和规则应该靠 AI 自己分析……要判断的地方，应该让模型自己来判断；
   但是，不应该让模型判断的地方、不需要模型做选择决策的地方，
   最好全部让系统直接接管完成。"

职责边界（E-122 实测：G3 = 14/14，G1 词表 0.857，G2 阈值 0.929）：

  【系统接管】确定性操作，无判断空间
    · extract_value_types   值类型识别（date/perf/money/count/numeric）
    · extract_value_tokens  值 token 提取
    · prepare_candidates    余弦计算 + 候选筛选 + 打包
    · apply_verdict         按判定落盘（只执行，不决定）

  【模型判断】需要语义理解
    · 哪条候选是真正目标（余弦最高 ≠ 目标，E-120 已证）
    · 关系类型（replace / merge / conflict / none）
    · 是否作废旧值

  【已删除】硬编码判断依据
    · SIMILARITY_THRESHOLD 0.58   → 交给模型：看候选包自己判"像不像同一条"
    · REPLACE_WORDS 等词表        → 交给模型：理解"换成/延到/还要求"的语义
    · margin 0.06                 → 交给模型：自己判断目标是否明确
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict

# ============================================================
# 一、系统接管：值类型与值 token 提取（纯机械）
# ============================================================
DATE_PAT = re.compile(
    r"(\d{1,2}\s*[号日]|\d{1,2}\s*月|\d{1,2}月\d{1,2}|"
    r"下周[一二三四五六日]|这周|明天|后天|昨天|月底|月初|年底|周[一二三四五六日])")
PERF_PAT = re.compile(r"(\d+\.?\d*\s*(ms|毫秒|秒|s|核|G|GB|MB|TB|%|倍|Mbps))")
MONEY_PAT = re.compile(r"(\d+\.?\d*\s*(元|块|万|美元|RMB))")
COUNT_PAT = re.compile(r"(\d+\.?\d*\s*(个|件|条|台|人|次|天|周|月|年|小时))")
CODE_PAT = re.compile(r"(\d{3,}|[A-Za-z]{2,}\d+|[\d\.]{2,})")


def extract_value_types(text: str) -> list[str]:
    """系统机械提取：句中含有哪些类型的'值'。无判断成分。"""
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


def extract_value_tokens(text: str) -> list[str]:
    """系统机械提取：具体的值本身。无判断成分。"""
    t = text or ""
    vals = []
    for pat in (DATE_PAT, PERF_PAT, MONEY_PAT, COUNT_PAT, CODE_PAT):
        for m in pat.finditer(t):
            v = m.group(0).strip()
            if v and v not in vals:
                vals.append(v)
    return vals[:6]


# ============================================================
# 二、系统接管：准备候选包（算余弦 + 筛候选，不判断）
# ============================================================
@dataclass
class Candidate:
    ep_id: str
    text: str
    cos: float
    value_types: list[str] = field(default_factory=list)
    value_tokens: list[str] = field(default_factory=list)


@dataclass
class PreparePack:
    """交给模型的候选包"""
    new_text: str
    new_value_types: list[str]
    new_value_tokens: list[str]
    candidates: list[Candidate]
    # 系统只提供"是否还有更多候选"，不提供任何判断结论
    total_valid_memories: int = 0


def prepare_candidates(st, embedder, new_text: str, top_k: int = 10,
                        offset: int = 0) -> PreparePack:
    """
    系统准备候选包（支持迭代扩散）。

    只做三件事：算余弦、取第 offset~top_k 名、附加值类型。
    **不做任何关系判定**，连"像不像同一条"都不判断。

    性能（E-125 修正）：
      原实现每次给所有已有记忆重新 encode，O(N) 次模型推理，288ms/条，不可接受。
      改为复用 store 里已缓存的向量矩阵，一次矩阵乘法算完：288ms → 个位数 ms。

    top_k 默认 10 + 迭代扩散（E-131/E-132 实测标定，不是拍脑袋）：
      固定 top_k 在任何规模下都会失效：
        1010 条 → 需 k=10
        5010 条 → 需 k=50
        10010 条 → 需 k=100
      迭代扩散（10→20→40→80→160）实测：
        所有规模命中率 100%
        平均 1.0/1.5/1.9 轮，平均 10/30/60 条候选
        比固定 100 条省 90%/70%/40%

    offset：扩散时跳过已经看过的那部分，避免重复发给模型。
    """
    import numpy as np

    nv = embedder.encode(new_text)[0]
    nv_norm = float(np.linalg.norm(nv)) or 1.0

    # 复用已缓存的有效记忆向量矩阵（不含失效）
    st._load_vectors(include_invalid=False)
    if st._vec is None or len(st._vec) == 0:
        return PreparePack(
            new_text=new_text,
            new_value_types=extract_value_types(new_text),
            new_value_tokens=extract_value_tokens(new_text),
            candidates=[],
            total_valid_memories=0,
        )

    norms = np.linalg.norm(st._vec, axis=1)
    norms[norms == 0] = 1.0
    sims = (st._vec @ nv) / (norms * nv_norm)

    # 取第 offset..top_k 名（用 argpartition 避免全排序）
    k = min(top_k, len(sims))
    idx = np.argpartition(-sims, k - 1)[:k]
    idx = idx[np.argsort(-sims[idx])]
    idx = idx[offset:]

    if len(idx) == 0:
        return PreparePack(
            new_text=new_text,
            new_value_types=extract_value_types(new_text),
            new_value_tokens=extract_value_tokens(new_text),
            candidates=[],
            total_valid_memories=len(sims),
        )

    # 只对选中的候选查文本（一次 SQL，不逐条查询）
    cand_ids = [st._vec_ids[int(i)] for i in idx]
    rows = st.con.execute(
        "SELECT ep_id, raw_text FROM episodes WHERE ep_id IN (%s)"
        % ",".join("?" * len(cand_ids)), cand_ids).fetchall()
    text_of = {eid: txt for eid, txt in rows}

    cands = []
    for i in idx:
        eid = st._vec_ids[int(i)]
        txt = text_of.get(eid, "")
        if not txt or txt == new_text:
            continue
        cands.append(Candidate(
            ep_id=eid, text=txt, cos=round(float(sims[int(i)]), 4),
            value_types=extract_value_types(txt),
            value_tokens=extract_value_tokens(txt),
        ))

    return PreparePack(
        new_text=new_text,
        new_value_types=extract_value_types(new_text),
        new_value_tokens=extract_value_tokens(new_text),
        candidates=cands,
        total_valid_memories=len(sims),
    )


# 扩散序列（E-132 实测：指数增长足够覆盖到 1 万条规模）
EXPAND_LADDER = [10, 20, 40, 80, 160]


def should_expand(pack: PreparePack, floor: float = 0.50,
                  max_k: int = 160) -> bool:
    """
    系统判断"是否需要扩散"（供模型参考，决定权在模型）。

    两种情况不扩散：
      · 最高相似度已低于 floor —— 真的无关，扩散无用
      · 已经扩到上限

    注意：这个函数只给出"可以扩散"的建议，**不是决定**。
    真正的决定是模型说"这些候选里没有我要找的那条"。
    """
    if not pack.candidates:
        return False
    if len(pack.candidates) >= max_k:
        return False
    return pack.candidates[0].cos >= floor


def needs_model(pack: PreparePack, floor: float = 0.50) -> bool:
    """
    系统预筛：这条新记忆值不值得调模型判断。

    这是**两级架构的粗筛层**（E-95~E-102 已验证：规则粗筛 + 模型精筛）。
    原则：精确率优先、宁漏勿错 —— 门槛必须设低，宁可多调一次模型，
    也不能把真矛盾漏掉让系统自己瞎判。

    floor 默认 0.50：
      E-125 实测：相关组最低 0.6143，无关组最高 0.5607。
      取 0.50 留出安全余量（宁可多调，不可漏判）。

    返回 False 时系统可以直接判"无关"，不调模型。
    """
    if not pack.candidates:
        return False
    return pack.candidates[0].cos >= floor


def pack_to_prompt(pack: PreparePack, cid: str = "") -> dict:
    """把候选包转成给模型的结构（供插件层直接塞进 prompt）"""
    return {
        "new_memory": pack.new_text,
        "new_value_types": pack.new_value_types,
        "new_value_tokens": pack.new_value_tokens,
        "candidates": [
            {"text": c.text, "cos": c.cos,
             "value_types": c.value_types, "value_tokens": c.value_tokens}
            for c in pack.candidates
        ],
        "total_valid_memories": pack.total_valid_memories,
    }


def pack_to_dict(pack: PreparePack) -> dict:
    """候选包的完整序列化（含 ep_id，供落盘与后续异步判定）"""
    return {
        "new_text": pack.new_text,
        "new_value_types": pack.new_value_types,
        "new_value_tokens": pack.new_value_tokens,
        "total_valid_memories": pack.total_valid_memories,
        "candidates": [asdict(c) for c in pack.candidates],
    }


# ============================================================
# 三、模型判断：结果容器 + 解析（模型输出 → 结构化）
# ============================================================
# duplicate = 同义重复，零新信息（E-139：作为第五种关系，与矛盾判定共用同一候选包，
#             零边际成本 —— 不需要额外调一次模型）
VALID_RELATIONS = ("duplicate", "replace", "merge", "conflict", "none")


@dataclass
class Verdict:
    """模型判断结果"""
    relation: str = "none"          # duplicate / replace / merge / conflict / none
    target_ep_id: str = ""          # 模型选中的目标记忆 ep_id（空=无目标）
    target_text: str = ""
    reason: str = ""                # 模型给的理由
    confidence: float = 1.0         # 模型自评置信度
    need_expand: bool = False       # 模型认为"候选里没找到，可能还有更远的"
    merged_text: str = ""           # merge 时模型给的融合文本（含双方全部信息）


def parse_model_output(raw: str, pack: PreparePack) -> Verdict:
    """
    解析模型输出（系统机械解析，不做语义判断）。

    模型应返回 JSON：
      {"target": "<候选原文 或 null>", "relation": "...", "reason": "...", "confidence": 0-1}

    target 用原文匹配（因为模型看到的是文本，不是 ep_id）。
    """
    v = Verdict()
    if not raw:
        v.reason = "模型无输出"
        v.confidence = 0.0
        return v

    data = None
    m = re.search(r"\{[\s\S]*\}", raw)
    if m:
        try:
            data = json.loads(m[0])
        except Exception:
            data = None
    if not isinstance(data, dict):
        v.reason = "模型输出无法解析为 JSON"
        v.confidence = 0.0
        return v

    rel = str(data.get("relation") or "none").strip().lower()
    v.relation = rel if rel in VALID_RELATIONS else "none"

    tgt = data.get("target")
    if tgt is None or tgt == "" or (isinstance(tgt, str) and tgt.strip().lower() in ("null", "none")):
        v.target_ep_id, v.target_text = "", ""
    else:
        tgt_s = str(tgt).strip()
        # 用原文精确匹配回 ep_id（系统机械匹配）
        for c in pack.candidates:
            if c.text == tgt_s:
                v.target_ep_id, v.target_text = c.ep_id, c.text
                break
        else:
            # 容忍近似匹配（模型可能轻微改写）
            best, best_ratio = None, 0.0
            for c in pack.candidates:
                # 共同字符比例
                common = len(set(tgt_s) & set(c.text))
                ratio = common / max(len(set(c.text)), 1)
                if ratio > best_ratio:
                    best, best_ratio = c, ratio
            if best and best_ratio >= 0.7:
                v.target_ep_id, v.target_text = best.ep_id, best.text
            else:
                v.reason = f"模型给出的目标无法匹配到候选：{tgt_s[:40]}"

    v.reason = str(data.get("reason") or v.reason)[:200]
    try:
        v.confidence = float(data.get("confidence", 1.0))
    except Exception:
        v.confidence = 1.0

    # 扩散意图：模型说"候选里没有我要找的那条，可能还有更远的"
    # （E-132：这是模型的决定，系统只执行）
    v.need_expand = bool(data.get("need_expand"))

    # merge 时模型给的融合文本（含双方全部独有信息）
    mt = data.get("merged_text")
    if isinstance(mt, str) and mt.strip() and mt.strip().lower() not in ("null", "none"):
        v.merged_text = mt.strip()

    # 系统一致性检查（不涉及语义）：none 不应有目标；有目标才可能 replace
    if v.relation == "none":
        v.target_ep_id, v.target_text = "", ""
    elif v.relation in ("duplicate", "replace", "merge", "conflict") and not v.target_ep_id:
        v.reason = (v.reason + " ⚠ 判为非 none 但未给出可匹配目标").strip()

    # merged_text 只在 merge 时有效，其他关系清空（系统机械校验）
    if v.relation != "merge":
        v.merged_text = ""

    # 已给出可匹配目标时，不再扩散（已经找到了）
    if v.target_ep_id:
        v.need_expand = False

    return v


# ============================================================
# 四、系统接管：落盘（只执行，不决定）
# ============================================================
def apply_verdict(st, verdict: Verdict, new_ep_id: str) -> dict:
    """
    把模型判定落到存储上。

    严格遵守红线 R2：只打标记，永不物理删除。

    五种关系的动作：
      duplicate  新记忆标失效，指向旧记忆（保留旧的那条，新的是冗余）
                 —— 与 replace 方向相反：replace 旧作废，duplicate 新作废
      replace    旧记忆标失效，指向新记忆
      merge      模型给出融合文本时：旧记忆改写为融合文本（revision+1），
                 新记忆标失效指向旧记忆 —— 两条信息合成一条更完整的（E-138 实测）；
                 没给融合文本时退化为互链标记
      conflict   两条都保留，新值置信度降低
      none       无动作
    """
    actions = {"relation": verdict.relation}

    # E-250 修：防止"静默失败"。
    # 原来若 relation 是 replace/merge/duplicate/conflict 但 target_ep_id 为空
    # （模型给了 target_ep_id 而非 target、或给 null），
    # 所有 elif 分支都不成立，函数静默返回 {"relation": ...}，
    # 调用方以为成功，实际什么都没做。
    #
    # 现在：这类情况显式标记 warned / no_action，让调用方能察觉。
    needs_target = verdict.relation in ("duplicate", "replace", "merge", "conflict")
    if needs_target and not verdict.target_ep_id:
        actions["no_action"] = True
        actions["warn"] = (
            f"relation={verdict.relation} 但未解析出目标记忆"
            f"（模型需返回 target 字段=候选原文；当前返回: "
            f"{'target=null' if not verdict.target_text else '目标无法匹配'}），"
            f"本次未做任何改动")
        return actions

    if verdict.relation == "duplicate" and verdict.target_ep_id:
        # 新写入的是冗余 → 新记忆作废，指向已有的那条
        st.invalidate(new_ep_id, superseded_by=verdict.target_ep_id)
        actions["invalidated"] = new_ep_id
        actions["duplicate_of"] = verdict.target_ep_id

    elif verdict.relation == "replace" and verdict.target_ep_id:
        st.invalidate(verdict.target_ep_id, superseded_by=new_ep_id)
        actions["invalidated"] = verdict.target_ep_id
        actions["superseded_by"] = new_ep_id

    elif verdict.relation == "merge" and verdict.target_ep_id:
        if verdict.merged_text:
            # 融合：旧记忆（主记录）改写为融合文本；新记忆作废指向主记录
            # 旧文本会保留在 revise 的 revision 历史与 merge 审计里（R2）
            old_ep = st.get(verdict.target_ep_id)
            old_text = old_ep.raw_text if old_ep else ""
            new_ep = st.get(new_ep_id)
            new_text = new_ep.raw_text if new_ep else ""

            ok = st.revise(verdict.target_ep_id, new_text=verdict.merged_text)
            if ok:
                st.invalidate(new_ep_id, superseded_by=verdict.target_ep_id)
                actions["merged_into"] = verdict.target_ep_id
                actions["invalidated"] = new_ep_id
                actions["merged_text"] = verdict.merged_text
                # 审计：融合前的两条原文全部保留
                try:
                    rec = {
                        "keep_ep_id": verdict.target_ep_id,
                        "merge_ep_ids": [new_ep_id],
                        "merged_text": verdict.merged_text,
                        "before_texts": {
                            verdict.target_ep_id: old_text,
                            new_ep_id: new_text,
                        },
                        "reason": verdict.reason,
                        "mode": "live-merge",
                    }
                    st.con.execute(
                        "INSERT OR REPLACE INTO meta VALUES (?,?)",
                        (f"merge:{verdict.target_ep_id}",
                         json.dumps(rec, ensure_ascii=False)))
                    st.con.commit()
                except Exception:
                    pass
            else:
                actions["linked_to"] = verdict.target_ep_id
        else:
            actions["linked_to"] = verdict.target_ep_id

    elif verdict.relation == "conflict" and verdict.target_ep_id:
        # 冲突：降低新值置信度，保留两条
        try:
            st.con.execute(
                "UPDATE episodes SET confidence=? WHERE ep_id=?",
                (0.5, new_ep_id))
            st.con.commit()
        except Exception:
            pass
        actions["conflicted_with"] = verdict.target_ep_id

    return actions


def log_verdict(st, new_ep_id: str, verdict: Verdict, actions: dict,
                pack: PreparePack | None = None) -> None:
    """系统记录判定过程，便于审计与 UI 展示"""
    rec = {
        "ep_id": new_ep_id,
        "relation": verdict.relation,
        "target_ep_id": verdict.target_ep_id,
        "target_text": verdict.target_text[:80],
        "reason": verdict.reason,
        "confidence": verdict.confidence,
        "actions": actions,
    }
    if pack:
        rec["candidates"] = [
            {"text": c.text, "cos": c.cos} for c in pack.candidates
        ]
    try:
        st.con.execute("INSERT OR REPLACE INTO meta VALUES (?,?)",
                       (f"conflict:{new_ep_id}",
                        json.dumps(rec, ensure_ascii=False)))
        st.con.commit()
    except Exception:
        pass
