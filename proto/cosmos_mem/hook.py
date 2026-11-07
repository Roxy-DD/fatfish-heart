"""
hook.py —— 记忆分层保护（L0~L4）

用户指出（2026-11-07）：
  "应该不止钩子记忆。你先分析一下这些记忆以及这些设计的地方，
   看一下应该保留哪些记忆，以及应该保证必须存在的记忆是哪些。"

→ 不是二元的"钩子/非钩子"，而是五层保护等级。

层级定义（详见 docs/72）：
  L0 不可变    红线/底线。动了就是错。不可失效/合并/修订。
  L1 常驻准则  每次对话都必须生效的指令。不可失效/合并，可修订，强制在场。
  L2 设计基石  定义"系统是什么"的核心洞察。不可失效/合并，可修订。
  L3 阶段性事实 会随进展变化。可失效、可合并。
  L4 一次性细节 测试数据、临时记录。可清理。

未标记的一律按 L3 处理（可失效）。

存储（守 R1：不加表，用 meta）：
  key   = "hook:<ep_id>"
  value = {"level":"L0"|"L1"|"L2", "kind":"...", "note":"...", "at":...}

为什么用 meta 而不是新列：
  · 不改 episodes 表结构 → schema_version 不变 → R1 无损迁移风险最低
  · meta 已是 key-value 存储，加前缀不影响现有数据
"""
from __future__ import annotations

import json
import time

# ============ 五层定义 ============

LEVELS = {
    "L0": {
        "name": "不可变",
        "desc": "红线/底线。动了就是错。",
        "no_invalidate": True,   # 不可失效
        "no_merge": True,        # 不可被合并掉
        "no_revise": True,       # 不可修订（连改表述都不允许）
        "always_present": True,  # 检索时强制在场
    },
    "L1": {
        "name": "常驻准则",
        "desc": "每次对话都必须生效的行为指令。",
        "no_invalidate": True,
        "no_merge": True,
        "no_revise": False,      # 允许优化表述
        "always_present": True,
    },
    "L2": {
        "name": "设计基石",
        "desc": "定义'系统是什么'的核心洞察。",
        "no_invalidate": True,
        "no_merge": True,
        "no_revise": False,
        "always_present": False,  # 按需检索即可
    },
    "L3": {
        "name": "阶段性事实",
        "desc": "会随项目进展变化的事实。",
        "no_invalidate": False,
        "no_merge": False,
        "no_revise": False,
        "always_present": False,
    },
    "L4": {
        "name": "一次性细节",
        "desc": "测试数据、临时记录。",
        "no_invalidate": False,
        "no_merge": False,
        "no_revise": False,
        "always_present": False,
    },
}

# 兼容旧命名
KINDS = ("policy", "workflow", "principle", "identity", "fact", "test")


def _key(ep_id: str) -> str:
    return f"hook:{ep_id}"


def level_of(st, ep_id: str) -> str | None:
    """
    返回该记忆的保护层级（L0~L2），未保护返回 None（视同 L3）。
    """
    try:
        row = st.con.execute("SELECT val FROM meta WHERE key=?",
                             (_key(ep_id),)).fetchone()
        if not row:
            return None
        d = json.loads(row[0])
        lv = d.get("level")
        # 兼容旧格式（只有 locked:true）
        if not lv and d.get("locked"):
            return "L1"
        return lv if lv in LEVELS else None
    except Exception:
        return None


def is_protected(st, ep_id: str) -> bool:
    """是否受保护（L0/L1/L2）"""
    lv = level_of(st, ep_id)
    return lv in ("L0", "L1", "L2")


# 兼容旧 API
def is_hook(st, ep_id: str) -> bool:
    return is_protected(st, ep_id)


def can_invalidate(st, ep_id: str) -> bool:
    """允许失效？"""
    lv = level_of(st, ep_id)
    if lv is None:
        return True
    return not LEVELS[lv]["no_invalidate"]


def can_merge(st, ep_id: str) -> bool:
    """允许被合并掉？"""
    lv = level_of(st, ep_id)
    if lv is None:
        return True
    return not LEVELS[lv]["no_merge"]


def can_revise(st, ep_id: str) -> bool:
    """允许修订？"""
    lv = level_of(st, ep_id)
    if lv is None:
        return True
    return not LEVELS[lv]["no_revise"]


def always_present(st, ep_id: str) -> bool:
    """检索时是否强制在场？"""
    lv = level_of(st, ep_id)
    if lv is None:
        return False
    return bool(LEVELS[lv]["always_present"])


# ============ 写操作 ============

def lock(st, ep_id: str, level: str = "L1", kind: str = "policy",
         note: str = "") -> bool:
    """
    把一条记忆标记为受保护。

    level: L0 | L1 | L2
    （L3/L4 不需要标记——不标记就是 L3）
    """
    if level not in ("L0", "L1", "L2"):
        return False
    try:
        st.con.execute(
            "INSERT OR REPLACE INTO meta VALUES (?,?)",
            (_key(ep_id),
             json.dumps({"level": level, "kind": kind, "note": note,
                         "at": time.time()}, ensure_ascii=False)))
        st.con.commit()
        return True
    except Exception:
        return False


def unlock(st, ep_id: str) -> bool:
    """解除保护。L0 不允许解锁（需要直接改 DB）。"""
    lv = level_of(st, ep_id)
    if lv == "L0":
        return False
    try:
        st.con.execute("DELETE FROM meta WHERE key=?", (_key(ep_id),))
        st.con.commit()
        return True
    except Exception:
        return False


# ============ 查询 ============

def protected_of(st) -> list[dict]:
    """列出全部受保护记忆（L0/L1/L2）"""
    out = []
    try:
        rows = st.con.execute(
            "SELECT m.key, m.val, e.ep_id, e.raw_text FROM meta m "
            "LEFT JOIN episodes e ON e.ep_id = SUBSTR(m.key, 6) "
            "WHERE m.key LIKE 'hook:%'").fetchall()
        for key, val, eid, txt in rows:
            try:
                d = json.loads(val)
            except Exception:
                continue
            lv = d.get("level")
            if not lv and d.get("locked"):
                lv = "L1"
            if lv not in LEVELS:
                continue
            out.append({
                "ep_id": eid or key[5:],
                "level": lv,
                "level_name": LEVELS[lv]["name"],
                "kind": d.get("kind", "policy"),
                "note": d.get("note", ""),
                "text": txt or "",
                "at": d.get("at", 0),
            })
    except Exception:
        pass
    return out


# 兼容旧 API
def hooks_of(st) -> list[dict]:
    return protected_of(st)


def present_ids(st) -> set:
    """必须在场（L0/L1）的 ep_id 集合"""
    ids = set()
    for p in protected_of(st):
        if always_present(st, p["ep_id"]):
            ids.add(p["ep_id"])
    return ids


# 兼容旧 API
def hook_ids(st) -> set:
    return present_ids(st)


def stats(st) -> dict:
    ps = protected_of(st)
    from collections import Counter
    return {
        "total": len(ps),
        "by_level": dict(Counter(p["level"] for p in ps)),
        "by_kind": dict(Counter(p["kind"] for p in ps)),
    }
