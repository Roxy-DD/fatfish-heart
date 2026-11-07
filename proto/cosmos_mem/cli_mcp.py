"""
cli_mcp.py —— DSH 插件调用的内核接口

设计：
  · 从 stdin 读 JSON 参数，向 stdout 写 JSON 结果（一行一个对象）
  · 命令由 --cmd 指定，避免每次启动加载全部模型
  · 只依赖 store_v2 与 capability，不关心 DSH 侧细节

支持命令：
  add         写入记忆
  search      检索（可选按问题维度路由）
  revise      修订
  invalidate  标记失效
  stats       统计
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)


def _load_store(db: str):
    from store_v2 import MemoryStore
    return MemoryStore(db, use_semantic=True)


def _audit(st, kind, msg="", sid="", ep_id="", extra=None):
    """统一审计日志（失败不抛，不阻塞主流程）"""
    try:
        import audit as _audit_mod
        _audit_mod.log(st, kind, msg=msg, sid=sid, ep_id=ep_id, extra=extra)
    except Exception:
        pass


def _read_payload() -> dict:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    return json.loads(raw)


def cmd_add(st, payload) -> dict:
    text = payload.get("text") or ""
    if not text:
        return {"ok": False, "error": "text 不能为空"}

    # 写入去重（防模型重复记同一件事）
    # 用归一化文本的 md5 比对已有记忆
    import hashlib
    import re as _re
    norm = _re.sub(r"\s+", "", text)
    h = hashlib.md5(norm.encode()).hexdigest()
    dup = st.con.execute(
        "SELECT ep_id, raw_text FROM episodes WHERE text_hash=? AND invalid_at IS NULL",
        (h,)).fetchone()
    if dup:
        return {"ok": True, "ep_id": dup[0], "text": dup[1],
                "scope": [], "issues": [], "deduped": True,
                "note": "已存在相同记忆，未重复写入"}

    # 矛盾处理：系统先打包候选，判断交给模型（插件层回调）
    # 插件可以传 judge=true 表示"需要我判断"，此时先只建包、
    # 返回 pack 给插件，由插件调模型后再发 judge 命令落盘。
    need_judge = bool(payload.get("judge"))

    # 情绪判断同样走"系统算、模型判"：内核先给规则结果，
    # 若插件声明 need_affect，则先在 out 里带上 prompt 让插件调模型，
    # 再发 affect 命令写回（E-152：模型 MAE 0.0598 vs 规则 0.2702）
    need_affect = bool(payload.get("judge_affect"))

    ep = st.add(text,
                atmosphere=payload.get("atmosphere") or "",
                explicit=bool(payload.get("explicit")),
                resolve_conflict=True,
                judge_fn=None)          # ← 内核不调模型，只打包

    scope = st.scope_of(ep.ep_id)
    out = {
        "ok": True,
        "ep_id": ep.ep_id,
        "text": ep.raw_text,
        "scope": [k for k, v in scope.items() if v],
        "issues": st.issues_of(ep.ep_id),
        "cost": ep.cost,
        "affect": ep.affect_peak,
    }

    # 若插件要求，把情绪判断的 prompt 一起带回
    if need_affect:
        try:
            import affect_v4 as _affect
            system, user = _affect.build_prompt(text)
            out["need_affect"] = True
            out["affect_prompt"] = {"system": system, "user": user}
        except Exception as e:
            out["need_affect"] = False
            out["affect_error"] = f"{type(e).__name__}: {e}"

    # 若系统预筛认为值得判断，把候选包带回给插件
    if need_judge:
        import conflict as _conflict
        from store_v1 import Embedder as _Emb
        try:
            # 迭代扩散（E-132）：第一轮只取 10 条，模型说"没找到"再扩散
            pack = _conflict.prepare_candidates(st, _Emb.get(), text, top_k=10)
            if _conflict.needs_model(pack):
                out["need_judge"] = True
                out["pack"] = _conflict.pack_to_prompt(pack, ep.ep_id)
                out["expand_round"] = 1
                out["expand_ladder"] = _conflict.EXPAND_LADDER
                out["can_expand"] = _conflict.should_expand(pack)
            else:
                out["need_judge"] = False
                out["judge_skip_reason"] = (
                    "系统预筛：最高相似度 %.3f 低于门槛，判为无关"
                    % (pack.candidates[0].cos if pack.candidates else 0.0))
                verdict = _conflict.Verdict(relation="none",
                                            reason=out["judge_skip_reason"])
                _conflict.log_verdict(st, ep.ep_id, verdict,
                                      {"relation": "none"}, pack)
        except Exception as e:
            out["need_judge"] = False
            out["judge_error"] = f"{type(e).__name__}: {e}"

    # 审计日志
    _audit(st, "remember",
           msg=("去重命中" if out.get("deduped") else "写入") + " " + text[:60],
           ep_id=out.get("ep_id") or "",
           extra={"deduped": bool(out.get("deduped")),
                  "need_judge": bool(out.get("need_judge")),
                  "cost": out.get("cost"), "affect": out.get("affect")})

    return out


def cmd_expand(st, payload) -> dict:
    """
    扩散一轮：系统扩大候选池，返回"新增的那部分"给模型再看一次。

    这是用户设计的核心（2026-11-02）：
      "看到的结果不一样的时候，再让模型自主地去确定要不要扩散、
       要不要提高这个 top k，然后去查找别的地方，而不是上来就给一个确定的 top k"

    payload:
      ep_id        要判定的记忆
      prev_k       上一轮的 top_k（默认 10）
      next_k       本轮要扩到的 top_k（默认 20）
    """
    import conflict as _conflict
    from store_v1 import Embedder as _Emb

    ep_id = payload.get("ep_id") or ""
    if not ep_id:
        return {"ok": False, "error": "ep_id 不能为空"}

    ep = st.get(ep_id)
    if not ep:
        return {"ok": False, "error": f"记忆不存在: {ep_id}"}

    prev_k = int(payload.get("prev_k") or 10)
    next_k = int(payload.get("next_k") or 20)

    try:
        # offset=prev_k：跳过已经看过的那部分，只给新增的
        pack = _conflict.prepare_candidates(
            st, _Emb.get(), ep.raw_text, top_k=next_k, offset=prev_k)

        out = {
            "ok": True,
            "ep_id": ep_id,
            "expand_round": int(payload.get("expand_round") or 2),
            "prev_k": prev_k,
            "next_k": next_k,
            "new_candidates": len(pack.candidates),
            "pack": _conflict.pack_to_prompt(pack, ep_id),
        }

        # 下一档还有吗
        ladder = _conflict.EXPAND_LADDER
        nxt = next((k for k in ladder if k > next_k), None)
        out["next_ladder_k"] = nxt
        out["can_expand"] = bool(nxt) and _conflict.should_expand(
            pack, max_k=(nxt or next_k))
        return out
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def cmd_judge(st, payload) -> dict:
    """
    插件把模型判断结果送回来，内核负责解析与落盘。

    payload:
      ep_id     要判定的记忆
      raw       模型的原始输出（JSON 字符串）
      top_k     判定时用的候选池大小（默认 10；扩散过就传实际值）
    """
    import conflict as _conflict
    from store_v1 import Embedder as _Emb

    ep_id = payload.get("ep_id") or ""
    raw = payload.get("raw") or ""
    if not ep_id or not raw:
        return {"ok": False, "error": "ep_id 与 raw 均不能为空"}

    ep = st.get(ep_id)
    if not ep:
        return {"ok": False, "error": f"记忆不存在: {ep_id}"}

    top_k = int(payload.get("top_k") or 10)

    try:
        pack = _conflict.prepare_candidates(st, _Emb.get(), ep.raw_text, top_k=top_k)
        verdict = _conflict.parse_model_output(raw, pack)
        actions = (_conflict.apply_verdict(st, verdict, ep_id)
                   if verdict.relation != "none"
                   else {"relation": "none"})
        _conflict.log_verdict(st, ep_id, verdict, actions, pack)
        # 必须用 invalidate_vector_cache：只清 _vec 不会清 _vec_cache，
        # 常驻内核会返回旧的向量矩阵，导致失效记录仍在检索结果里（E-126 同类 bug）
        st.invalidate_vector_cache()

        # 审计日志：conflict（矛盾判定落盘）
        # E-250：若没解析出目标（no_action），用 warn 级别记录，便于排查
        _audit(st, "conflict",
               msg=(f"判定 {verdict.relation}"
                    + ("（未执行:" + str(actions.get("warn", ""))[:40] + "）"
                       if actions.get("no_action") else "")
                    + f" ← {ep.raw_text[:40]}"),
               ep_id=ep_id,
               extra={"relation": verdict.relation,
                      "target_ep_id": verdict.target_ep_id,
                      "confidence": verdict.confidence,
                      "no_action": bool(actions.get("no_action")),
                      "actions": actions})

        return {
            "ok": True,
            "ep_id": ep_id,
            "relation": verdict.relation,
            "target_text": verdict.target_text,
            "reason": verdict.reason,
            "actions": actions,
            # E-250：显式告知调用方本次是否真的做了改动
            "no_action": bool(actions.get("no_action")),
            "warn": actions.get("warn", ""),
        }
    except Exception as e:
        _audit(st, "error", msg="矛盾判定失败",
               ep_id=ep_id, extra={"error": str(e)})
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def cmd_chain_prepare(st, payload) -> dict:
    """
    链式展开第 1 步：系统准备候选（不判断"是否同链"）。

    E-143 实测：链式目标在 seed 的 top-20 里 7/7 命中，平均排名第 2；
                 模型判链 7/7 全对；余弦 Top-1 只有 4/7。
    → 系统只做候选生成，判断权交给模型。

    payload:
      seed_ep_id  已经命中的那条记忆
      query       用户原始提问（可选，给模型更多上下文）
      top_n       候选池大小（默认 20）
    """
    import chain as _chain
    from store_v1 import Embedder as _Emb

    seed_id = payload.get("seed_ep_id") or ""
    if not seed_id:
        return {"ok": False, "error": "seed_ep_id 不能为空"}

    ep = st.get(seed_id)
    if not ep:
        return {"ok": False, "error": f"记忆不存在: {seed_id}"}

    top_n = int(payload.get("top_n") or 20)
    try:
        pack = _chain.prepare_chain_candidates(
            st, _Emb.get(), ep.raw_text,
            query=payload.get("query") or "", top_n=top_n)
        return {
            "ok": True,
            "seed_ep_id": seed_id,
            "n_candidates": len(pack.candidates),
            "pack": _chain.chain_to_prompt(pack),
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def cmd_chain_apply(st, payload) -> dict:
    """
    链式展开第 2 步：把模型选中的同链记忆落库 + 返回给插件。

    payload:
      seed_ep_id  种子记忆
      raw         模型的原始输出（含 chain 数组）
      query       用户原始提问（审计用）
    """
    import chain as _chain
    from store_v1 import Embedder as _Emb

    seed_id = payload.get("seed_ep_id") or ""
    raw = payload.get("raw") or ""
    if not seed_id or not raw:
        return {"ok": False, "error": "seed_ep_id 与 raw 均不能为空"}

    ep = st.get(seed_id)
    if not ep:
        return {"ok": False, "error": f"记忆不存在: {seed_id}"}

    try:
        pack = _chain.prepare_chain_candidates(
            st, _Emb.get(), ep.raw_text,
            query=payload.get("query") or "", top_n=20)
        texts = _chain.parse_chain_output(raw, pack)

        rows = st.con.execute(
            "SELECT ep_id, raw_text FROM episodes "
            "WHERE invalid_at IS NULL AND raw_text IN (%s)"
            % ",".join("?" * len(texts)), texts).fetchall() if texts else []

        _chain.log_chain(st, seed_id, pack, texts,
                         reason=payload.get("reason") or "")

        # 审计日志：chain（链式展开）
        _audit(st, "chain",
               msg=f"链式展开 {len(rows)} 条 ← {ep.raw_text[:36]}",
               ep_id=seed_id,
               extra={"query": (payload.get("query") or "")[:60],
                      "n_candidates": len(pack.candidates),
                      "n_chain": len(rows)})

        return {
            "ok": True,
            "seed_ep_id": seed_id,
            "chain_count": len(rows),
            "chain": [{"ep_id": e, "text": t} for e, t in rows],
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def cmd_affect(st, payload) -> dict:
    """
    插件把模型的情绪判断送回来，内核负责解析与落盘。

    E-152：模型 MAE 0.0598（漏判 1/25）vs 规则 0.2702（漏判 17/25）

    payload:
      ep_id  要更新的记忆
      raw    模型的原始输出（含 affect / valence）
    """
    import affect_v4 as _affect

    ep_id = payload.get("ep_id") or ""
    raw = payload.get("raw") or ""
    if not ep_id or not raw:
        return {"ok": False, "error": "ep_id 与 raw 均不能为空"}

    ep = st.get(ep_id)
    if not ep:
        return {"ok": False, "error": f"记忆不存在: {ep_id}"}

    parsed = _affect.parse_affect_output(raw)
    if not parsed:
        return {"ok": False, "error": "模型输出无法解析"}

    try:
        st.con.execute(
            "UPDATE episodes SET affect_peak=?, valence=? WHERE ep_id=?",
            (parsed["affect"], parsed["valence"], ep_id))
        st.con.commit()

        # 审计日志：affect（情绪判断写回）
        _audit(st, "affect",
               msg=f"情绪 {parsed['affect']:.2f}/{parsed['valence']:+d} ← {ep.raw_text[:36]}",
               ep_id=ep_id,
               extra={"affect": parsed["affect"],
                      "valence": parsed["valence"],
                      "reason": (parsed.get("reason") or "")[:80]})

        return {
            "ok": True,
            "ep_id": ep_id,
            "affect": parsed["affect"],
            "valence": parsed["valence"],
            "reason": parsed["reason"],
            "source": "model",
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def cmd_sleep_prepare(st, payload) -> dict:
    """
    睡眠/巩固机制第 1 步：系统机械准备"整理候选包"。

    docs/71：模型在任务/项目告一段落时主动触发睡眠，
    做整理（merge 碎片）、回忆（bump 强化）、遗忘（invalidate 过时）。

    这里系统只做：扫描最近记忆 + 算两两相似 + 打包候选对。
    判断权（哪些该 merge/遗忘/强化）交给模型。

    payload:
      limit    候选对上限（默认 20）
      recent_n 扫描窗口（默认 50）
    """
    import consolidate as _con
    from store_v1 import Embedder as _Emb

    limit = int(payload.get("limit") or 20)
    recent_n = int(payload.get("recent_n") or 50)

    try:
        pack = _con.prepare_sleep_pack(st, _Emb.get(), limit=limit,
                                       recent_n=recent_n)
        return {
            "ok": True,
            "total_recent": pack.total,
            "n_pairs": len(pack.pairs),
            "window": pack.window,            # "fresh"=增量 / "recent"=兜底
            "cursor": pack.cursor,            # 上次睡眠时间戳
            "skipped_judged": pack.skipped_judged,  # 跳过的已判定对数
            "fresh": pack.fresh,              # 本次新增记忆
            "l3_candidates": pack.l3_candidates,  # 待模型判断是否过时的 L3
            "recent": pack.recent,
            "pairs": [{
                "a_id": p.a_id, "a_text": p.a_text,
                "b_id": p.b_id, "b_text": p.b_text,
                "cos": p.cos,
            } for p in pack.pairs],
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def cmd_sleep_apply(st, payload) -> dict:
    """
    睡眠/巩固机制第 2 步：模型判定后的动作落盘。

    payload:
      actions      模型返回的动作数组
                   [{op:"merge",keep_id,drop_id,merged_text},
                    {op:"invalidate",ep_id},
                    {op:"bump",ep_id,channel,n}]
      advance_cursor  是否推进睡眠游标（默认 True）
                      True 时，本次整理后新记忆的"增量窗口"从此刻起算

    红线：merge 保留旧记忆（superseded_by），invalidate 打标记不删。
    """
    import consolidate as _con

    actions = payload.get("actions") or []
    if not isinstance(actions, list):
        return {"ok": False, "error": "actions 必须是数组"}

    try:
        # E-247：本次判定过的候选对（下次 prepare 跳过）
        judged = payload.get("judged_pairs") or []
        result = _con.apply_sleep_verdict(st, actions, judged_pairs=judged)
        _con.log_sleep(st, actions, result)
        # 推进睡眠游标：下次只扫这之后新增的（避免老记忆永远扫不到）
        new_cursor = None
        if payload.get("advance_cursor", True):
            new_cursor = _con.set_cursor(st)
        # 审计日志：sleep
        _audit(st, "sleep",
               msg=f"睡眠整理 {len(actions)} 个动作",
               extra={"merged": len(result.get("merged", [])),
                      "invalidated": len(result.get("invalidated", [])),
                      "bumped": len(result.get("bumped", [])),
                      "skipped_hooks": len(result.get("skipped_hooks", [])),
                      "cursor": new_cursor})
        return {"ok": True, "result": result, "cursor": new_cursor}
    except Exception as e:
        _audit(st, "error", msg="睡眠整理失败", extra={"error": str(e)})
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def cmd_sleep_history(st, payload) -> dict:
    """
    读取睡眠整理历史（供 UI 展示）。

    数据存在 meta 表的 sleep:log 键，log_sleep 写入，最多 50 条。
    """
    import json as _json

    try:
        row = st.con.execute(
            "SELECT val FROM meta WHERE key='sleep:log'").fetchone()
        if not row:
            return {"ok": True, "history": []}
        log = _json.loads(row[0])
        return {"ok": True, "history": log}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def cmd_audit(st, payload) -> dict:
    """
    读统一审计日志（供 UI 和排查用）。

    payload:
      kind     可选，按类型过滤
      sid      可选，按会话过滤
      limit    默认 100
    """
    import audit as _audit_mod

    kind = payload.get("kind") or None
    sid = payload.get("sid") or None
    limit = int(payload.get("limit") or 100)
    try:
        rows = _audit_mod.query(st, kind=kind, sid=sid, limit=limit)
        return {"ok": True, "logs": rows, "stats": _audit_mod.stats(st)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def cmd_hook(st, payload) -> dict:
    """
    记忆分层保护管理（L0~L4，详见 docs/72）。

    用户要求（2026-11-07）：
      "应该不止钩子记忆。你先分析一下这些记忆，
       看一下应该保留哪些记忆，以及应该保证必须存在的记忆是哪些。"

    层级：
      L0 不可变    红线/底线。不可失效/合并/修订。
      L1 常驻准则  每次对话都必须生效。不可失效/合并，强制在场。
      L2 设计基石  定义"系统是什么"。不可失效/合并。
      L3 阶段性事实 未标记的都是这层，可失效/合并。
      L4 一次性细节 可清理。

    payload:
      action  list | lock | unlock | check | levels
      ep_id   目标记忆（lock/unlock/check）
      level   L0 | L1 | L2（lock 时用）
      kind    分类标签
      note    说明
    """
    import hook as _hook

    action = payload.get("action") or "list"
    try:
        if action == "levels":
            return {"ok": True, "levels": _hook.LEVELS}
        if action == "list":
            return {"ok": True, "protected": _hook.protected_of(st),
                    "stats": _hook.stats(st)}
        if action == "check":
            eid = payload.get("ep_id") or ""
            lv = _hook.level_of(st, eid)
            return {"ok": True, "ep_id": eid, "level": lv,
                    "level_name": _hook.LEVELS[lv]["name"] if lv else "未保护(L3)",
                    "can_invalidate": _hook.can_invalidate(st, eid),
                    "can_merge": _hook.can_merge(st, eid),
                    "can_revise": _hook.can_revise(st, eid),
                    "always_present": _hook.always_present(st, eid)}
        if action == "lock":
            eid = payload.get("ep_id") or ""
            level = payload.get("level") or "L1"
            if not eid or not st.get(eid):
                return {"ok": False, "error": f"记忆不存在: {eid}"}
            if level not in ("L0", "L1", "L2"):
                return {"ok": False, "error": f"level 必须是 L0/L1/L2，收到 {level}"}
            ok = _hook.lock(st, eid, level=level,
                            kind=payload.get("kind") or "policy",
                            note=payload.get("note") or "")
            if ok:
                _audit(st, "config", msg=f"保护记忆 {level}",
                       ep_id=eid, extra={"level": level,
                                         "kind": payload.get("kind")})
            return {"ok": bool(ok), "ep_id": eid, "level": _hook.level_of(st, eid)}
        if action == "unlock":
            eid = payload.get("ep_id") or ""
            lv = _hook.level_of(st, eid)
            if lv == "L0":
                return {"ok": False, "error": "L0 不可解锁（红线不可动）"}
            ok = _hook.unlock(st, eid)
            if ok:
                _audit(st, "config", msg="解除保护", ep_id=eid,
                       extra={"was_level": lv})
            return {"ok": bool(ok), "ep_id": eid, "level": _hook.level_of(st, eid)}
        return {"ok": False, "error": f"未知 action: {action}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}



def cmd_seed(st, payload) -> dict:
    """
    种子记忆管理（系统自带的认知基线，恢复出厂后仍保留）。

    用户要求（2026-11-07）：
      "记忆初始化后清空了，但应该保留：
       1. 项目本身的记忆（这个系统是什么）；
       2. 相关记忆钩子；
       3. 有价值、通用性的记忆，能提高系统能力和模型认知能力。
       一定要通用，不要具体私人内容，以后要发给别人用。"

    payload:
      action  list | load | stats | factory   (factory = 恢复出厂)
      force   load 时是否强制重写
    """
    import seed as _seed

    action = payload.get("action") or "list"
    try:
        if action == "list":
            data = _seed.load_seed_file()
            out = []
            for level in ("L0", "L1", "L2"):
                for spec in data.get(level, []):
                    out.append({
                        "level": level,
                        "title": spec["title"],
                        "kind": spec.get("kind", "policy"),
                        "chars": len(spec["text"]),
                        "text": spec["text"],
                    })
            return {"ok": True, "seeds": out,
                    "stats": _seed.seed_stats(st)}
        if action == "load":
            r = _seed.load_seeds(st, force=bool(payload.get("force")))
            return {"ok": True, **r, "stats": _seed.seed_stats(st)}
        if action == "stats":
            return {"ok": True, **_seed.seed_stats(st)}
        if action == "factory":
            r = _seed.restore_seeds_only(st)
            _audit(st, "config", msg="恢复出厂设置（仅保留种子记忆）",
                   extra={"invalidated": r["invalidated"],
                          "seeds_kept": r["seeds_kept"]})
            return {"ok": True, **r}
        return {"ok": False, "error": f"未知 action: {action}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def cmd_trace(st, payload) -> dict:
    """
    演化链追溯（R4 可溯源的完整实现）。

    给一条记忆，返回它的完整演化链 A → B → C：
      · root = 最老的一条
      · tip  = 最新的一条
      · chain 从老到新排列，每条带 invalid_at / revision / is_current

    纯机械查询，无判断成分。

    payload:
      ep_id  任意一条链上的记忆
    """
    import consolidate as _con

    ep_id = payload.get("ep_id") or ""
    if not ep_id:
        return {"ok": False, "error": "ep_id 不能为空"}
    if not st.get(ep_id):
        return {"ok": False, "error": f"记忆不存在: {ep_id}"}

    try:
        r = _con.evolution_chain(st, ep_id)
        return {"ok": True, **r}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def cmd_inject_log(st, payload) -> dict:
    """
    注入审计落库（E-236）。

    背景：原来注入历史只存在插件内存里，重启即丢，
          导致无法事后评估「这条记忆注入后有没有用」。
          落库后才能做效果回测。

    payload:
      query            触发注入的用户消息
      mode             query / slots
      sid              会话 id
      ep_ids           注入的记忆 id 数组
      protected_count  L0/L1 条数
      content_chars    注入内容总字符数
      has_prewarm      是否有前瞻
    """
    import json as _json

    try:
        ep_ids = payload.get("ep_ids") or []
        query = (payload.get("query") or "")[:120]
        _audit(st, "inject",
               msg=f"注入 {len(ep_ids)} 条 ← {query[:40]}",
               sid=payload.get("sid") or "",
               extra={
                   "query": query,
                   "mode": payload.get("mode"),
                   "ep_ids": ep_ids,
                   "protected_count": payload.get("protected_count", 0),
                   "content_chars": payload.get("content_chars", 0),
                   "has_prewarm": bool(payload.get("has_prewarm")),
               })
        return {"ok": True, "logged": len(ep_ids)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def cmd_inject_history(st, payload) -> dict:
    """
    读注入历史（从 DB，持久化，跨重启可查）。

    payload:
      limit  默认 100
      sid    可选，按会话过滤
    """
    import json as _json

    limit = int(payload.get("limit") or 100)
    sid = payload.get("sid") or None
    try:
        rows = st.con.execute(
            "SELECT at, sid, msg, extra FROM audit_log "
            "WHERE kind='inject'" +
            (" AND sid=?" if sid else "") +
            " ORDER BY seq DESC LIMIT ?",
            ((sid, limit) if sid else (limit,))).fetchall()
        out = []
        for at, s, msg, extra in rows:
            try:
                d = _json.loads(extra) if extra else {}
            except Exception:
                d = {}
            out.append({
                "at": at, "sid": s or "", "query": d.get("query", ""),
                "mode": d.get("mode", ""), "n_items": len(d.get("ep_ids") or []),
                "ep_ids": d.get("ep_ids") or [],
                "protected_count": d.get("protected_count", 0),
                "content_chars": d.get("content_chars", 0),
                "has_prewarm": d.get("has_prewarm", False),
            })
        return {"ok": True, "history": out, "count": len(out)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def cmd_note(st, payload) -> dict:
    """
    知识沉淀管理（三层：随笔 → 笔记 → 手册）。

    用户要求（2026-11-07）：
      "我们不可能去背诵书，也不可能记住所有的自然规律，
       但是我们会记住一些重要或者常用的东西。
       而那些不常用的、少见但重要的，或者少见又不重要的东西，
       我们经常会把它体系性地记录在笔记本里。
       有的时候，可以在闲暇时候把这些笔记整理成体系性的书籍、参考手册等等。
       所以，先是像随笔一样的零散笔记，然后是体系化的书籍、手册等等。"

    三层：
      scrap  随笔  零散灵感、踩坑记录（随时记，不成体系）
      note   笔记  按主题整理，能独立看懂
      manual 手册  体系化的书，可有章节（parent_id + order_idx）

    payload:
      action  add | get | revise | promote | children | invalidate
              | list | search | stats | suggest | suggest_manual | suggest_scrap
      tier    scrap | note | manual
    """
    import notebook as _nb

    action = payload.get("action") or "list"
    try:
        if action == "add":
            r = _nb.note_add(
                st,
                title=payload.get("title") or "",
                body=payload.get("body") or "",
                tier=payload.get("tier") or "note",
                topic=payload.get("topic") or "",
                tags=payload.get("tags") or [],
                source_ep_ids=payload.get("source_ep_ids") or [],
                source_note_ids=payload.get("source_note_ids") or [],
                parent_id=payload.get("parent_id") or "",
                order_idx=int(payload.get("order_idx") or 0),
                level=payload.get("level") or "")
            _audit(st, "note",
                   msg=f"新建{r['tier_name']} {r['title'][:40]}",
                   extra={"note_id": r["note_id"], "tier": r["tier"],
                          "chars": r["chars"]})
            return {"ok": True, "note": r}

        if action == "get":
            n = _nb.get_note(st, payload.get("note_id") or "")
            return {"ok": bool(n), "note": n}

        if action == "revise":
            n = _nb.note_revise(
                st, payload.get("note_id") or "",
                title=payload.get("title"),
                body=payload.get("body"),
                topic=payload.get("topic"),
                tags=payload.get("tags"),
                tier=payload.get("tier"))
            if n:
                _audit(st, "note", msg=f"修订{n['tier_name']} {n['title'][:40]}",
                       extra={"note_id": n["note_id"], "revision": n["revision"]})
            return {"ok": bool(n), "note": n}

        if action == "promote":
            # 升层：scrap → note → manual
            n = _nb.note_promote(st, payload.get("note_id") or "",
                                 payload.get("to_tier") or "note")
            if n:
                _audit(st, "note", msg=f"升层到{n['tier_name']} {n['title'][:36]}",
                       extra={"note_id": n["note_id"], "tier": n["tier"]})
            return {"ok": bool(n), "note": n}

        if action == "children":
            return {"ok": True,
                    "children": _nb.note_children(st,
                                                  payload.get("note_id") or "")}

        if action == "invalidate":
            ok = _nb.note_invalidate(st, payload.get("note_id") or "",
                                     payload.get("superseded_by") or "")
            return {"ok": ok}

        if action == "list":
            return {"ok": True,
                    "notes": _nb.note_list(
                        st, tier=payload.get("tier") or "",
                        topic=payload.get("topic") or "",
                        include_invalid=bool(payload.get("include_invalid")),
                        limit=int(payload.get("limit") or 200)),
                    "stats": _nb.note_stats(st)}

        if action == "search":
            return {"ok": True,
                    "notes": _nb.note_search(st, payload.get("query") or "",
                                             tier=payload.get("tier") or "",
                                             limit=int(payload.get("limit") or 30))}

        if action == "stats":
            return {"ok": True, "stats": _nb.note_stats(st)}

        if action == "suggest":
            # 记忆 → 笔记
            return {"ok": True, "stage": "episode→note",
                    "topics": _nb.suggest_note_topics(
                        st, int(payload.get("min_count") or 5))}

        if action == "suggest_scrap":
            # 随笔 → 笔记
            return {"ok": True, "stage": "scrap→note",
                    "topics": _nb.suggest_scrap_promote(
                        st, int(payload.get("min_count") or 3))}

        if action == "suggest_manual":
            # 笔记 → 手册
            return {"ok": True, "stage": "note→manual",
                    "topics": _nb.suggest_manual_topics(
                        st, int(payload.get("min_count") or 5))}

        return {"ok": False, "error": f"未知 action: {action}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def cmd_todo(st, payload) -> dict:
    """
    待办管理（模型自己的任务队列，模型自主完全控制）。

    payload:
      action  add | list | update | done | invalidate | stats
      kind    task | note_hint | review
    """
    import notebook as _nb

    action = payload.get("action") or "list"
    try:
        if action == "add":
            t = _nb.todo_add(
                st,
                title=payload.get("title") or "",
                detail=payload.get("detail") or "",
                kind=payload.get("kind") or "task",
                priority=int(payload.get("priority") or 0),
                ref_ep_ids=payload.get("ref_ep_ids") or [])
            _audit(st, "todo", msg=f"新建待办 {t['title'][:40]}",
                   extra={"todo_id": t["todo_id"], "kind": t["kind"]})
            return {"ok": True, "todo": t}

        if action == "list":
            return {"ok": True,
                    "todos": _nb.todo_list(
                        st, status=payload.get("status") or "",
                        kind=payload.get("kind") or "",
                        include_done=bool(payload.get("include_done")),
                        limit=int(payload.get("limit") or 200)),
                    "stats": _nb.todo_stats(st)}

        if action == "update":
            t = _nb.todo_update(
                st, payload.get("todo_id") or "",
                **(payload.get("fields") or
                   {k: payload[k] for k in
                    ("title", "detail", "priority", "status") if k in payload}))
            return {"ok": bool(t), "todo": t}

        if action == "done":
            t = _nb.todo_done(st, payload.get("todo_id") or "")
            return {"ok": bool(t), "todo": t}

        if action == "invalidate":
            return {"ok": _nb.todo_invalidate(st, payload.get("todo_id") or "")}

        if action == "stats":
            return {"ok": True, "stats": _nb.todo_stats(st)}

        return {"ok": False, "error": f"未知 action: {action}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def cmd_compose(st, payload) -> dict:
    """
    知识册装配（三段式，避免模型非必要的上下文整理）。

    用户指出的架构（2026-11-07）：
      "这种随笔到手册书籍应该是流程性的，
       不应该是每一个东西都需要大模型从头到尾自己重新生成。
       模型每次生成的是随笔，积累到一定量之后，
       模型不需要去处理，只需要调用那个程序，
       程序直接把这些东西合并成一个册子，
       之后模型再对这个册子进行重新重构和梳理。
       也就是说，要避免模型这种非必要的上下文整理。"

    阶段：
      1. 模型写随笔（外部完成）
      2. **程序**机械合并成册子（本命令的 action=digest，零模型）
      3. 模型对册子重构（本命令返回 prompt，由插件层调模型；
         结果用 action=finalize 写回）

    payload:
      action   digest  | list_topics | finalize
      topic    主题
      tier     源层级（默认 scrap）
      min_items 最少条目数
      digest    （finalize 时）模型重构后的正文
      note_ids  （finalize 时）来源条目 id
    """
    import compose as _cp

    action = payload.get("action") or "digest"
    try:
        if action == "list_topics":
            # 程序判定：哪些主题够格合并（零模型）
            return {"ok": True,
                    "topics": _cp.compile_all_topics(
                        st, tier=payload.get("tier") or "scrap",
                        min_items=int(payload.get("min_items") or 3))}

        if action == "digest":
            r = _cp.compose_flow(
                st, topic=payload.get("topic") or "",
                tier=payload.get("tier") or "scrap",
                min_items=int(payload.get("min_items") or 3),
                call_model=None)
            _audit(st, "note",
                   msg=f"合并册子 {r.get('topic', '')} ({r.get('n_items', 0)}条)",
                   extra={"stage": r.get("stage"),
                          "chars": r.get("digest_chars", 0)})
            return {"ok": r.get("stage") != "insufficient", **r}

        if action == "finalize":
            # 两种输入：
            #   outline_raw  — 模型输出的结构方案 JSON（推荐）
            #   digest       — 已组装好的正文（兼容旧调用）
            import compose as _cp

            topic = payload.get("topic") or ""
            note_ids = payload.get("note_ids") or []
            title = payload.get("title") or ""

            outline_raw = payload.get("outline_raw") or ""
            items = payload.get("items") or []

            if outline_raw:
                outline = _cp.parse_outline(outline_raw)
                if not outline:
                    return {"ok": False,
                            "error": "模型输出无法解析为结构方案",
                            "raw": outline_raw[:300]}
                body = _cp.assemble_manual(st, outline, items,
                                           topic=topic, title=title)
                m = _cp.finalize_manual(
                    st, topic=topic, manual_body=body,
                    source_note_ids=note_ids,
                    title=title or outline.get("title") or "")
                _audit(st, "note",
                       msg=f"装订手册 {m['title'][:40]}（模型定大纲+程序组装）",
                       extra={"note_id": m["note_id"],
                              "sources": len(note_ids),
                              "chars": m["chars"],
                              "sections": len(outline.get("outline") or [])})
                return {"ok": True, "manual": m, "outline": outline,
                        "assembly": "program"}

            # 兼容：直接给正文
            body = payload.get("digest") or ""
            if not body.strip():
                return {"ok": False, "error": "需要 outline_raw 或 digest"}
            m = _cp.finalize_manual(st, topic=topic, manual_body=body,
                                    source_note_ids=note_ids, title=title)
            _audit(st, "note", msg=f"生成手册 {m['title'][:40]}",
                   extra={"note_id": m["note_id"], "sources": len(note_ids)})
            return {"ok": True, "manual": m}

        return {"ok": False, "error": f"未知 action: {action}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def cmd_triage(st, payload) -> dict:
    """
    记忆分诊：用现有数据决定"该记在哪"（详见 triage.py）。

    不需要发明新指标——系统已有两维数据：
      常用度 ← channel 访问计数
      重要性 ← cost（失去代价）

    payload:
      action   one | all | suggest | stats
      ep_id    （one 时）单条记忆
    """
    import triage as _tg

    action = payload.get("action") or "stats"
    try:
        if action == "one":
            eid = payload.get("ep_id") or ""
            if not eid:
                return {"ok": False, "error": "ep_id 不能为空"}
            return {"ok": True, **_tg.classify(st, eid)}

        if action == "all":
            t = _tg.triage_all(
                st,
                freq_high=int(payload.get("freq_high") or 3),
                cost_high=float(payload.get("cost_high") or 0.7),
                limit=int(payload.get("limit") or 500))
            return {"ok": True,
                    "counts": t["counts"], "thresholds": t["thresholds"],
                    "critical": t["critical"][:20],
                    "core": t["core"][:10],
                    "minor": t["minor"][:20]}

        if action == "suggest":
            return {"ok": True,
                    "suggestions": _tg.suggest_from_triage(
                        st,
                        note_hint_min=int(payload.get("min_items") or 3),
                        freq_high=int(payload.get("freq_high") or 3),
                        cost_high=float(payload.get("cost_high") or 0.7))}

        if action == "stats":
            return {"ok": True, "stats": _tg.stats(st)}

        return {"ok": False, "error": f"未知 action: {action}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def cmd_history(st, payload) -> dict:
    """
    内容历史版本查询（R4 可塑性的完整实现）。

    E-244 修：原来 revise 直接覆盖，旧文本永久丢失。
    现在每次修订前自动快照进 history 表，可查任意版本。

    payload:
      kind    episode | note | stats
      id      ep_id 或 note_id
    """
    import history as _hist

    kind = payload.get("kind") or "stats"
    try:
        if kind == "stats":
            return {"ok": True, "stats": _hist.stats(st)}

        if kind == "episode":
            eid = payload.get("id") or ""
            if not eid:
                return {"ok": False, "error": "id 不能为空"}
            h = _hist.episode_history(st, eid)
            return {"ok": True, "ep_id": eid, "versions": h,
                    "count": len(h)}

        if kind == "note":
            nid = payload.get("id") or ""
            if not nid:
                return {"ok": False, "error": "id 不能为空"}
            h = _hist.note_history(st, nid)
            return {"ok": True, "note_id": nid, "versions": h,
                    "count": len(h)}

        return {"ok": False, "error": f"未知 kind: {kind}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def cmd_scan_conflicts(st, payload) -> dict:
    import conflict as _conflict
    from store_v1 import Embedder as _Emb

    limit = int(payload.get("limit") or 20)
    out = []
    rows = st.con.execute(
        "SELECT ep_id, raw_text FROM episodes WHERE invalid_at IS NULL "
        "ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    for eid, txt in rows:
        # 已有判定记录就跳过
        rec = st.conflict_of(eid)
        if rec:
            continue
        try:
            pack = _conflict.prepare_candidates(st, _Emb.get(), txt)
            if _conflict.needs_model(pack):
                out.append({"ep_id": eid,
                            "pack": _conflict.pack_to_prompt(pack, eid)})
        except Exception:
            continue
    return {"ok": True, "items": out, "count": len(out)}


def cmd_search(st, payload) -> dict:
    query = payload.get("query") or ""
    top = int(payload.get("top") or 5)
    fixed_slots = int(payload.get("fixed_slots") or 0)
    min_cost = float(payload.get("min_cost") or 0.0)

    # 无查询词 + 有 fixed_slots → 取"固定席位"（cost 最高的记忆）
    if not query:
        if fixed_slots <= 0:
            return {"ok": False, "error": "query 不能为空（或用 fixed_slots 取固定席位）",
                    "results": []}
        rows = st.con.execute(
            "SELECT ep_id, raw_text, cost, affect_peak FROM episodes "
            "WHERE invalid_at IS NULL AND cost >= ? "
            "ORDER BY cost DESC LIMIT ?", (min_cost, fixed_slots)).fetchall()
        return {"ok": True, "mode": "fixed_slots",
                "results": [{"ep_id": r[0], "text": r[1],
                             "cost": r[2], "affect": r[3]} for r in rows]}

    if payload.get("scoped"):
        results, qdims, stats = st.search_scoped(query, top=top)
        items = [{"ep_id": ep.ep_id, "text": ep.raw_text,
                  "score": sc, "note": note}
                 for ep, sc, note in results]
        return {"ok": True, "query": query, "dims": qdims,
                "results": items, "stats": stats}

    raw = st.search(query, top=top, rerank=bool(payload.get("rerank")),
                    rerank_pool=int(payload.get("rerank_pool") or 0))
    items = []
    for tup in raw:
        ep = tup[0]
        sc = tup[1] if len(tup) > 1 else None
        fl = tup[2] if len(tup) > 2 else 0
        # E-231：返回 flag，让调用方能区分"受保护的 L0/L1"与"普通检索结果"
        # flag: 0=普通, 1=固定席位, 2=L0/L1 强制在场
        items.append({"ep_id": ep.ep_id, "text": ep.raw_text,
                      "score": sc, "flag": fl})

    # 审计日志：recall（检索）
    _audit(st, "recall", msg="检索 " + query[:60],
           extra={"hits": len(items), "rerank": bool(payload.get("rerank"))})

    return {"ok": True, "query": query, "results": items}


def cmd_revise(st, payload) -> dict:
    ep_id = payload.get("ep_id")
    new_text = payload.get("new_text")
    if not ep_id:
        return {"ok": False, "error": "ep_id 不能为空"}
    ok = st.revise(ep_id, new_text=new_text)
    return {"ok": bool(ok)}


def cmd_invalidate(st, payload) -> dict:
    ep_id = payload.get("ep_id")
    if not ep_id:
        return {"ok": False, "error": "ep_id 不能为空"}
    by = payload.get("superseded_by")
    if by:
        ok, new_ep = st.supersede(ep_id, by)
        return {"ok": True, "new_ep_id": new_ep.ep_id}
    st.invalidate(ep_id)
    return {"ok": True}


def cmd_stats(st, payload) -> dict:
    return st.stats()


def cmd_list(st, payload) -> dict:
    """
    UI 专用：列出记忆（带派生层信息），供管理界面展示。

    payload:
      query          可选，搜索词；空则按时间倒序列出
      top            返回条数，默认 200
      includeInvalid 是否包含已失效
    """
    query = (payload.get("query") or "").strip()
    top = int(payload.get("top") or 200)
    include_invalid = bool(payload.get("includeInvalid"))

    def pack(ep):
        try:
            scope_map = st.scope_of(ep.ep_id)
            scope = [k for k, v in scope_map.items() if v]
        except Exception:
            scope = []
        try:
            issues = st.issues_of(ep.ep_id)
        except Exception:
            issues = []
        # 分层保护级别（docs/72）
        try:
            import hook as _hook
            level = _hook.level_of(st, ep.ep_id)
        except Exception:
            level = None
        return {
            "ep_id": ep.ep_id,
            "raw_text": ep.raw_text,
            "cost": ep.cost,
            "affect": ep.affect_peak,
            "valence": ep.valence,
            "atmosphere": ep.atmosphere,
            "triggers": ep.triggers if isinstance(ep.triggers, list) else [],
            "person": ep.person if isinstance(ep.person, list) else [],
            "time": ep.time if isinstance(ep.time, list) else [],
            "location": ep.location if isinstance(ep.location, list) else [],
            "confidence": ep.confidence,
            "created_at": ep.created_at,
            "invalid_at": ep.invalid_at,
            "superseded_by": ep.superseded_by,
            "revision": ep.revision,
            "text_hash": ep.text_hash,
            "scope": scope,
            "issues": issues,
            "level": level,          # L0/L1/L2/nil(L3)
        }

    items = []
    if query:
        res = st.search(query, top, include_invalid)
        for tup in res:
            ep = tup[0]
            items.append(pack(ep))
    else:
        sql = "SELECT ep_id FROM episodes"
        if not include_invalid:
            sql += " WHERE invalid_at IS NULL"
        sql += " ORDER BY created_at DESC LIMIT ?"
        for (eid,) in st.con.execute(sql, (top,)).fetchall():
            ep = st.get(eid)
            if ep:
                items.append(pack(ep))

    return {"ok": True, "items": items, "stats": st.stats()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--cmd", required=True)
    args = ap.parse_args()

    payload = _read_payload()
    st = _load_store(args.db)

    handlers = {
        "add": cmd_add,
        "search": cmd_search,
        "revise": cmd_revise,
        "invalidate": cmd_invalidate,
        "stats": cmd_stats,
        "list": cmd_list,
        "judge": cmd_judge,
        "scan_conflicts": cmd_scan_conflicts,
        "expand": cmd_expand,
        "chain_prepare": cmd_chain_prepare,
        "chain_apply": cmd_chain_apply,
        "affect": cmd_affect,
        "sleep_prepare": cmd_sleep_prepare,
        "sleep_apply": cmd_sleep_apply,
        "sleep_history": cmd_sleep_history,
        "audit": cmd_audit,
        "hook": cmd_hook,
        "seed": cmd_seed,
        "trace": cmd_trace,
        "inject_log": cmd_inject_log,
        "inject_history": cmd_inject_history,
        "note": cmd_note,
        "todo": cmd_todo,
        "compose": cmd_compose,
        "triage": cmd_triage,
        "history": cmd_history,
    }
    fn = handlers.get(args.cmd)
    if not fn:
        out = {"ok": False, "error": f"未知命令 {args.cmd}"}
    else:
        try:
            out = fn(st, payload)
        except Exception as e:
            out = {"ok": False, "error": f"{type(e).__name__}: {e}"}

    sys.stdout.write(json.dumps(out, ensure_ascii=False))
    sys.stdout.flush()


if __name__ == "__main__":
    main()


