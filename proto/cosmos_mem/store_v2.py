"""
store_v2.py —— 组装版记忆系统（v2.0）

在 store_v1（四红线 + 双维度 + 触发 + 链式展开 + 固定席位）基础上，
组装 E-86 已确认精确复现的三个能力：

  capability.py（E-86 单元校验通过）
    ├─ infer_scope         能力范围（6 维度）
    ├─ detect_question_dims 问题维度识别
    ├─ full_check          完整性闸（7 类缺陷）
    └─ identity_can        身份判定 v5

架构分层（符合红线 R1/R3：派生层可重建，不写进真值层）：
  真值层（不可丢，进 DB）      episodes / vectors / ep_fts
  派生层（可重算，进内存）    capability 推断结果 ← 由 raw_text 确定性重算

新能力：search 支持 respect_scope
  - True：按问题维度路由，优先返回"该维度能答"的记忆
  - 返回结构带 note（"这条能答X，但答不出Y"）

可塑性：revise 后自动重算能力范围与完整性（保持派生层一致）
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from store_v1 import MemoryStore as MemoryStoreV1, Episode, COLS, SCHEMA_VERSION  # noqa
from capability import (infer_scope, detect_question_dims, full_check,
                        identity_can, DIMS)
from affect_v3 import estimate_affect_v3  # noqa: E-93 接入的显式情绪词方案


class MemoryStore(MemoryStoreV1):
    """v2.0：能力范围 + 完整性闸 + affect_v3 情绪 组装版"""

    def __init__(self, path="mem.db", use_semantic=True):
        super().__init__(path, use_semantic)
        self._scope_cache = {}     # ep_id -> {dim: bool}（派生，可重算）
        self._issues_cache = {}    # ep_id -> [issues]（派生，可重算）

    # ---------- 派生层计算 ----------
    def _derive(self, ep_id: str, raw_text: str):
        """重算一条记忆的能力范围与完整性（确定性，可重建）"""
        self._scope_cache[ep_id] = infer_scope(raw_text)
        self._issues_cache[ep_id] = full_check(raw_text)

    def _ensure_derived(self, ep_id: str, raw_text: str):
        if ep_id not in self._scope_cache:
            self._derive(ep_id, raw_text)

    def _refresh_affect(self, ep_id: str, text: str, explicit: bool,
                        affect_fn=None):
        """
        重算 affect_peak / valence，更新到真值层。

        E-152 实测：
          规则法 affect_v3 : MAE 0.2702  漏判 17/25  相关 0.465
          模型判断         : MAE 0.0598  漏判 1/25   相关 0.967
        → 默认走模型（通过 affect_fn 注入），无模型时降级到规则。

        E-151 的反面证据：用规则做预筛最多只能覆盖 65% 真情绪，
        因为"预筛"本身就是一次情绪判断，规则做不了。
        """
        af = None
        if affect_fn is not None:
            try:
                from affect_v4 import estimate_affect_v4
                af = estimate_affect_v4(text, judge_fn=affect_fn,
                                        explicit=explicit)
            except Exception:
                af = None

        if not af or af.get("affect") is None:
            try:
                from affect_v4 import fallback_rule
                af = fallback_rule(text, explicit=explicit)
            except Exception:
                af = {"affect": 0.0, "valence": 0}

        self.con.execute(
            "UPDATE episodes SET affect_peak=?, valence=? WHERE ep_id=?",
            (af.get("affect", 0.0), af.get("valence", 0), ep_id))
        self.con.commit()

    # ---------- 覆写写入/修订，保持派生层一致 + 情绪重算 + 矛盾处理 ----------
    def add(self, text, atmosphere="", explicit=False, encode=True,
            resolve_conflict=True, judge_fn=None, affect_fn=None,
            return_verdict=False):
        """
        参数：
          resolve_conflict  是否做矛盾处理
          judge_fn          **矛盾判定的模型回调**。签名 fn(pack) -> raw_output(str)
                            由插件层传入（内部调 LLM）。
          affect_fn         **情绪判断的模型回调**。签名 fn(system, user) -> raw_output(str)
                            由插件层传入。为 None 时降级到规则（affect_v3）。

        职责划分（E-122 实测 G3=14/14；E-152 情绪模型 MAE 0.0598 vs 规则 0.2702）：
          系统：算余弦、提值类型、筛候选、打包、落盘
          模型：判断哪条是目标、关系类型、情绪强度
        """
        ep = super().add(text, atmosphere=atmosphere, explicit=explicit,
                         encode=encode)
        self._derive(ep.ep_id, text)
        self._refresh_affect(ep.ep_id, text, explicit, affect_fn=affect_fn)

        pack = None
        verdict = None

        if resolve_conflict:
            try:
                import conflict as _conflict
                from store_v1 import Embedder as _Emb

                # ① 系统准备候选包（不做判断，纯矩阵运算）
                pack = _conflict.prepare_candidates(self, _Emb.get(), text)

                # ② 系统预筛：先由系统判断"值不值得调模型"
                #    这是两级架构的粗筛层：精确率优先、宁漏勿错。
                #    系统能确定的（明显无关）直接判 none，不浪费一次模型调用。
                need = _conflict.needs_model(pack)

                if not need:
                    # 系统直接判无关，不调模型
                    verdict = _conflict.Verdict(
                        relation="none",
                        reason=("系统预筛：最高相似度 %.3f 低于门槛，判为无关"
                                % (pack.candidates[0].cos if pack.candidates else 0.0)),
                    )
                    _conflict.log_verdict(self, ep.ep_id, verdict,
                                          {"relation": "none"}, pack)

                elif judge_fn is not None:
                    # ③ 模型判断（需要语义理解的部分）
                    prompt_pack = _conflict.pack_to_prompt(pack, ep.ep_id)
                    raw = judge_fn(prompt_pack)
                    verdict = _conflict.parse_model_output(raw, pack)

                    # ④ 系统落盘
                    if verdict.relation != "none":
                        actions = _conflict.apply_verdict(self, verdict, ep.ep_id)
                    else:
                        actions = {"relation": "none"}
                    _conflict.log_verdict(self, ep.ep_id, verdict, actions, pack)
                    self.invalidate_vector_cache()  # 失效标记可能变了，向量缓存重建

                else:
                    # 没有判断回调：存下候选包，供后续异步判定
                    self._store_pack(ep.ep_id, pack)

            except Exception as e:
                try:
                    self.con.execute(
                        "INSERT OR REPLACE INTO meta VALUES (?,?)",
                        ("last_conflict_error", f"{type(e).__name__}: {e}"))
                    self.con.commit()
                except Exception:
                    pass

        if return_verdict:
            return ep, verdict
        return ep

    def _store_pack(self, ep_id, pack):
        """无模型判断时，把候选包存进 meta，供后续异步判定"""
        import json as _json
        import conflict as _conflict
        try:
            payload = _json.dumps(_conflict.pack_to_dict(pack), ensure_ascii=False)
            self.con.execute(
                "INSERT OR REPLACE INTO meta VALUES (?,?)",
                (f"conflict-pack:{ep_id}", payload))
            self.con.commit()
        except Exception as e:
            # 不再静默吞掉：记录到 last_conflict_error，便于排查
            try:
                self.con.execute(
                    "INSERT OR REPLACE INTO meta VALUES (?,?)",
                    ("last_pack_error", f"{type(e).__name__}: {e}"))
                self.con.commit()
            except Exception:
                pass

    def pending_packs(self, limit: int = 50) -> list[dict]:
        """读取尚未判定的候选包（异步判定用）"""
        import json as _json
        rows = self.con.execute(
            "SELECT key, val FROM meta WHERE key LIKE 'conflict-pack:%' LIMIT ?",
            (limit,)).fetchall()
        out = []
        for k, v in rows:
            try:
                d = _json.loads(v)
                d["ep_id"] = k.replace("conflict-pack:", "")
                out.append(d)
            except Exception:
                pass
        return out

    def fill_pending(self, judge_fn, limit: int = 50) -> dict:
        """
        批量补做判定（异步场景：先写入、稍后由模型批量判断）。

        返回 {"filled": n, "relations": {...}}
        """
        import conflict as _conflict
        from store_v1 import Embedder as _Emb

        stats = {"filled": 0, "relations": {}}
        for item in self.pending_packs(limit):
            eid = item["ep_id"]
            ep = self.get(eid)
            if not ep or ep.invalid_at is not None:
                continue
            try:
                pack = _conflict.prepare_candidates(self, _Emb.get(), ep.raw_text)
                if not pack.candidates:
                    continue
                raw = judge_fn(_conflict.pack_to_prompt(pack, eid))
                v = _conflict.parse_model_output(raw, pack)
                if v.relation != "none":
                    actions = _conflict.apply_verdict(self, v, eid)
                else:
                    actions = {"relation": "none"}
                _conflict.log_verdict(self, eid, v, actions, pack)
                self.con.execute(
                    "DELETE FROM meta WHERE key=?", (f"conflict-pack:{eid}",))
                self.con.commit()
                stats["filled"] += 1
                stats["relations"][v.relation] = stats["relations"].get(v.relation, 0) + 1
            except Exception:
                continue
        if stats["filled"]:
            self.invalidate_vector_cache()
        return stats

    def conflict_of(self, ep_id: str) -> dict:
        """读取某条记忆的矛盾判定记录（UI 用）"""
        import json as _json
        row = self.con.execute(
            "SELECT val FROM meta WHERE key=?", (f"conflict:{ep_id}",)).fetchone()
        if not row:
            return {}
        try:
            return _json.loads(row[0])
        except Exception:
            return {}

    def revise(self, ep_id, new_text=None, confidence=None, affect_fn=None):
        ok = super().revise(ep_id, new_text=new_text, confidence=confidence)
        if ok and new_text is not None:
            self._derive(ep_id, new_text)
            self._refresh_affect(ep_id, new_text, explicit=False,
                                 affect_fn=affect_fn)
        return ok

    # ---------- 读取派生 ----------
    def scope_of(self, ep_id: str) -> dict:
        ep = self.get(ep_id)
        if not ep:
            return {d: False for d in DIMS}
        self._ensure_derived(ep_id, ep.raw_text)
        return dict(self._scope_cache[ep_id])

    def issues_of(self, ep_id: str) -> list[str]:
        ep = self.get(ep_id)
        if not ep:
            return []
        self._ensure_derived(ep_id, ep.raw_text)
        return list(self._issues_cache[ep_id])

    def identity_of(self, ep_id: str) -> bool:
        ep = self.get(ep_id)
        if not ep:
            return False
        self._ensure_derived(ep_id, ep.raw_text)
        return bool(self._scope_cache[ep_id].get("identity"))

    # ---------- 能力范围路由检索 ----------
    def search_scoped(self, query, top=5, include_invalid=False,
                      expand=False, hops=2, fixed_slots=0):
        """
        按问题维度路由的检索。
        返回 (list[(ep, score, note)], qdims, stats)
          note = None 或 "这条能答X，但答不出Y"

        E-350 修：原实现有两个严重问题

          ① **丢弃 flag**：返回的三元组是 (ep, score, note)，
             note 是字符串或 None，与 flag(int) 语义完全不同。
             而 cli_mcp.cmd_search 的 scoped 分支把它当 note 用，
             插件侧 `filter(it => it.flag === 2)` 永远拿不到东西
             → **L0/L1 的"永远在场"从未生效**

          ② **维度过滤会丢掉 L0/L1**：只有"能力维度与问题匹配"的记忆
             才会进入 full/partial。L0/L1 是准则类记忆，
             其能力维度往往与用户问题无关，于是被整体过滤掉。

        修法：
          · 返回值第四位带上 flag，由 cli_mcp 透传给插件
          · 无论维度是否匹配，L0/L1 都必须强制保留（flag=2）
        """
        qdims = detect_question_dims(query)

        # 先做普通检索，取更多候选
        base = self.search(query, top=top * 3, include_invalid=include_invalid,
                           expand=False, hops=hops, fixed_slots=fixed_slots)
        # base 元素是 (ep, score, flag) 三元组
        norm = [(ep, sc, flag) for ep, sc, flag in base]

        full, partial, forced = [], [], []
        for ep, sc, flag in norm:
            # E-350：flag==2（L0/L1）无条件保留，不参与维度过滤
            if flag == 2:
                forced.append((ep, sc, flag, None))
                continue
            s = self.scope_of(ep.ep_id)
            hit = sum(1 for d in qdims if s.get(d))
            miss = [d for d in qdims if not s.get(d)]
            if hit == len(qdims):
                full.append((ep, sc, flag, None))
            elif hit > 0:
                note = (f"能答{'/'.join(d for d in qdims if s.get(d))}，"
                        f"答不出{'/'.join(miss)}")
                partial.append((ep, sc, flag, note))

        picked = full + partial
        out = []
        # E-350 修（第二处）：L0/L1 不能占用 top 名额
        #
        # 上一版写成 picked = forced + full + partial 然后 [:top]，
        # 但 forced 有 11 条，top=5 时直接把名额吃光，
        # 普通检索结果一条都进不来（实测「Docker 容器」查询
        # 返回 5 条全是 flag=2 且 cos=0.00）。
        #
        # 正确语义（与 store_v1.search 一致）：
        #   普通结果照常给 top 条，L0/L1 追加在后面。
        for ep, sc, flag, note in picked[:top]:
            out.append((ep, sc, note, flag))
        # 强制在场的 L0/L1 追加到末尾（不占 top 名额）
        present = {ep.ep_id for ep, _, _, _ in out}
        for ep, sc, flag, note in forced:
            if ep.ep_id not in present:
                out.append((ep, sc, note, flag))
        if expand:
            out = self._expand_scoped(out, include_invalid, hops)

        stats = {"qdims": qdims, "full": len(full),
                 "partial": len(partial), "forced": len(forced)}
        return out, qdims, stats

    def _expand_scoped(self, base, include_invalid, hops=2):
        """链式展开（复用 v1 的 _expand，但 base 是四元组+note+flag）"""
        triples = [(ep, sc, flag) for ep, sc, _, flag in base]
        expanded = self._expand(triples, include_invalid, hops)
        # 重新附 note 与 flag
        out = []
        base_flag = {ep.ep_id: flag for ep, _, _, flag in base}
        for ep, sc, flag in expanded:
            note = None
            # 展开出来的新节点不保留原 flag；原节点保留
            if ep.ep_id in base_flag:
                flag = base_flag[ep.ep_id]
            else:
                flag = 0 if flag == 0 else flag
            out.append((ep, sc, note, flag))
        return out

    # ---------- stats 扩展 ----------
    def stats(self):
        s = super().stats()
        n_incomplete = 0
        n_identity = 0
        for r in self.con.execute("SELECT ep_id, raw_text FROM episodes").fetchall():
            issues = self.issues_of(r[0])
            if issues:
                n_incomplete += 1
            if identity_can(r[1]):
                n_identity += 1
        s["incomplete"] = n_incomplete
        s["identity_can"] = n_identity
        return s


# 便于外部 import
__all__ = ["MemoryStore", "Episode", "COLS", "SCHEMA_VERSION",
           "infer_scope", "detect_question_dims", "full_check",
           "identity_can", "DIMS"]
