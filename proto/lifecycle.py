"""
lifecycle.py —— 版本生命周期管理（跨版本自我迭代与迁移）

用户的核心要求（2026-11-07）：
  "你要能够独立于这个系统之外来认识这个系统，
   具备生命周期的迁移能力：从上个版本迁移到当前版本，
   甚至迁移到下个版本，以及在这几个版本之间轮转。
   就像你现在修到了版本 3，如果版本 3 修出问题了，
   你要有机制能自动跨越回到版本 2；
   版本 2 能够自动知道是版本 3 出问题了，然后你再去修版本 3；
   等版本 3 修好了之后，你又可以迁跃到版本 3 上。
   在版本 3 上就可以接着去构建版本 4。
   如果你跨越回去到版本 2，发现版本 2 也有问题的时候，
   它也可以自动回跃到版本 1。
   这就是这种能够保证你永远可以迭代、永远不会死亡的能力。
   一个版本就相当于构建了一次生命周期循环，
   这种跨版本能力就是把生命周期循环一个个垒成了一个圆筒，
   可以在圆筒上进行跨时间周期的自我迭代以及更新。"

设计要点：
  ① 版本 = 一次完整生命周期快照（代码 + DB + 种子 + 配置）
  ② 双向迁移：可以回退（back），也可以再前进（forward）
  ③ 故障传播：回退时记录"哪个版本坏了、坏在哪"，下个版本能看到
  ④ 级联回退：当前版本修不好，可以继续往更早版本退
  ⑤ 永不死亡：只要有可用版本，系统总能启动

版本清单存在 versions.json（不进 git，因为要跨分支保留）：
  {
    "current": "E-233",
    "versions": [
      {"id":"E-231","at":..., "db":"snapshots/E-231.db",
       "commit":"...", "status":"ok|broken", "reason":"..."}
    ]
  }
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_MEM = os.path.join(_HERE, "cosmos_mem")
_DB = os.path.join(_MEM, "dsh_mem.db")
_SEED = os.path.join(_MEM, "seed_memory.json")
_SNAP = os.path.join(_HERE, "work", "snapshots")
_MANIFEST = os.path.join(_HERE, "work", "versions.json")

# 一个"完整版本"包含的东西
VERSION_PARTS = {
    "db": _DB,
    "seed": _SEED,
}


class VersionError(Exception):
    pass


# ============================================================
# git 辅助
# ============================================================

def _git(*args, timeout=120) -> tuple[int, str]:
    try:
        r = subprocess.run(["git"] + list(args), cwd=_ROOT,
                           capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return 1, str(e)


def current_commit() -> str:
    c, out = _git("rev-parse", "--short", "HEAD")
    return out.strip() if c == 0 else "unknown"


def all_tags() -> list[str]:
    c, out = _git("tag", "--sort=creatordate")
    return [t for t in out.splitlines() if t.strip()] if c == 0 else []


# ============================================================
# 版本清单
# ============================================================

def _load_manifest() -> dict:
    if not os.path.exists(_MANIFEST):
        return {"current": None, "versions": []}
    try:
        with open(_MANIFEST, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"current": None, "versions": []}


def _save_manifest(m: dict) -> None:
    os.makedirs(os.path.dirname(_MANIFEST), exist_ok=True)
    with open(_MANIFEST, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=1)


def _find(m: dict, vid: str) -> dict | None:
    for v in m.get("versions", []):
        if v.get("id") == vid:
            return v
    return None


# ============================================================
# 创建版本快照（一次生命周期循环的存档）
# ============================================================

def snapshot_version(vid: str, note: str = "",
                     commit: bool = True) -> dict:
    """
    为当前状态创建一个完整版本快照。

    包含：
      · git commit + tag（代码）
      · DB 副本（记忆数据，不进 git）
      · 种子文件副本（种子内容）

    返回版本记录。
    """
    os.makedirs(_SNAP, exist_ok=True)
    m = _load_manifest()

    # ---------- 代码：提交并打 tag ----------
    if commit:
        _git("add", "-A")
        code, out = _git("commit", "-m", f"{vid}: {note}" if note else vid)
        # 无改动时 commit 失败，不视为错误
    cok, _ = _git("tag", "-f", "-a", vid, "-m", note or f"version {vid}")

    commit_id = current_commit()

    # ---------- 数据：DB + 种子 ----------
    artifacts = {}
    for name, src in VERSION_PARTS.items():
        if not os.path.exists(src):
            continue
        ext = os.path.splitext(src)[1]
        dst = os.path.join(_SNAP, f"{vid}{'.' + name if name != 'db' else ''}{ext}")
        try:
            shutil.copy2(src, dst)
            artifacts[name] = os.path.relpath(dst, _HERE)
        except Exception:
            pass

    entry = {
        "id": vid,
        "at": time.time(),
        "commit": commit_id,
        "note": note,
        "artifacts": artifacts,
        "status": "ok",          # ok | broken | unknown
        "reason": "",
        "verified": False,
    }

    # 覆盖已有同名版本，否则追加
    old = _find(m, vid)
    if old:
        m["versions"] = [v for v in m["versions"] if v.get("id") != vid]
    m["versions"].append(entry)
    m["current"] = vid
    _save_manifest(m)

    return entry


# ============================================================
# 版本钩子：可删的轻量标记（保证机制可恢复）
# ============================================================

HOOKS_FILE = os.path.join(_HERE, "work", "version_hooks.json")


def _load_hooks() -> dict:
    if not os.path.exists(HOOKS_FILE):
        return {"hooks": []}
    try:
        with open(HOOKS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"hooks": []}


def _save_hooks(h: dict) -> None:
    os.makedirs(os.path.dirname(HOOKS_FILE), exist_ok=True)
    with open(HOOKS_FILE, "w", encoding="utf-8") as f:
        json.dump(h, f, ensure_ascii=False, indent=1)


def plant_hook(vid: str, kind: str = "marker", note: str = "",
               payload: dict | None = None) -> dict:
    """
    在某个版本上种一个钩子（可删的轻量标记）。

    钩子的作用：
      · 指路：不加载整个版本就能知道"这里是什么"
      · 恢复：迁移失败时，钩子提供"从哪退回"的最小信息
      · 可删：删掉钩子不影响版本主体

    kind 类型：
      marker    普通标记（"这个版本做了什么"）
      fault     故障标记（"这个版本坏在哪"）
      anchor    锚点（迁移必经的中转站）
      seed_ref  种子引用（记录当时的种子版本）
    """
    h = _load_hooks()
    hook = {
        "id": f"{vid}@{int(time.time()*1000)}",
        "version": vid,
        "kind": kind,
        "note": note,
        "payload": payload or {},
        "at": time.time(),
        "active": True,
    }
    h["hooks"].insert(0, hook)
    # 只保留最近 200 个，避免膨胀
    h["hooks"] = h["hooks"][:200]
    _save_hooks(h)
    return hook


def remove_hook(hook_id: str) -> bool:
    """删掉一个钩子（不影响版本主体）"""
    h = _load_hooks()
    before = len(h["hooks"])
    h["hooks"] = [x for x in h["hooks"] if x.get("id") != hook_id]
    if len(h["hooks"]) < before:
        _save_hooks(h)
        return True
    return False


def clear_hooks(kind: str | None = None) -> int:
    """清空钩子（可按类型）。这是"可删"的体现——清空后机制仍能工作。"""
    h = _load_hooks()
    if kind:
        n = len([x for x in h["hooks"] if x.get("kind") == kind])
        h["hooks"] = [x for x in h["hooks"] if x.get("kind") != kind]
    else:
        n = len(h["hooks"])
        h["hooks"] = []
    _save_hooks(h)
    return n


def list_hooks(vid: str | None = None, kind: str | None = None) -> list[dict]:
    h = _load_hooks()
    out = h.get("hooks", [])
    if vid:
        out = [x for x in out if x.get("version") == vid]
    if kind:
        out = [x for x in out if x.get("kind") == kind]
    return out


def hooks_summary() -> dict:
    """钩子总览（按版本/类型统计）"""
    h = _load_hooks()
    hooks = h.get("hooks", [])
    by_ver, by_kind = {}, {}
    for x in hooks:
        by_ver[x.get("version", "?")] = by_ver.get(x.get("version", "?"), 0) + 1
        by_kind[x.get("kind", "?")] = by_kind.get(x.get("kind", "?"), 0) + 1
    return {"total": len(hooks), "by_version": by_ver,
            "by_kind": by_kind, "file": os.path.relpath(HOOKS_FILE, _HERE)}


# ============================================================
# 迁移：回退 / 前进
# ============================================================

def _direction_of(cur_id: str | None, target_id: str) -> str:
    """
    判断迁移方向：backward（回退）/ forward（前进）/ jump（跳跃）/ lateral

    不限制跨几个版本——任意跨度都合法。
    """
    if not cur_id or cur_id == target_id:
        return "lateral"
    vers = sorted(_load_manifest().get("versions", []), key=lambda v: v.get("at", 0))
    idx = {v["id"]: i for i, v in enumerate(vers)}
    if cur_id not in idx or target_id not in idx:
        return "lateral"
    ci, ti = idx[cur_id], idx[target_id]
    if ti < ci:
        return "backward" if ci - ti == 1 else "backward_jump"
    if ti > ci:
        return "forward" if ti - ci == 1 else "forward_jump"
    return "lateral"


def migrate_to(vid: str, reason: str = "",
               mark_current_broken: bool | None = None) -> dict:
    """
    从当前版本迁移到目标版本。

    这是核心能力：可以往**任意方向、任意跨度**迁移——
      · 回退 backward      （版本3坏了 → 回到版本2）
      · 前进 forward       （版本3修好了 → 再跳回版本3）
      · 回退跳跃 backward_jump（版本3坏了且版本2也坏 → 直接退回版本1）
      · 前进跳跃 forward_jump （从版本1直接跳到版本4）
      · 横向 lateral       （同版本重载 / 未知方向，保守处理）

    **没有任何跨度限制**：可以退 1 个，也可以退 5 个。
    保证的是「两个方向的能力永远都在」，不是「只能跨 N 个」。

    mark_current_broken:
      None（默认）= 按方向自动决定
        · backward* → True（离开的版本确实坏了，要标记）
        · forward*  → False（前进说明当前是好的，不该污染）
      True/False = 强制指定

    返回迁移报告。
    """
    m = _load_manifest()
    cur_id = m.get("current")
    target = _find(m, vid)

    if not target:
        raise VersionError(f"版本不存在: {vid}。可用: " +
                           ", ".join(v["id"] for v in m.get("versions", [])))

    direction = _direction_of(cur_id, vid)
    if mark_current_broken is None:
        # 只有"离开一个坏版本"才标记；前进说明当前版本是好的
        mark_current_broken = direction.startswith("backward")

    report = {"from": cur_id, "to": vid, "reason": reason,
              "direction": direction, "steps": [], "ok": False}

    # ---------- ⓪ 迁移前种一个锚点钩子（万一迁移中断，可据此恢复） ----------
    anchor = plant_hook(
        vid, kind="anchor",
        note=f"{direction}: {cur_id} → {vid}",
        payload={"from": cur_id, "to": vid, "reason": reason,
                 "direction": direction,
                 "from_commit": current_commit()})
    report["steps"].append(f"种下锚点钩子（{direction}）")

    # ---------- ① 标记当前版本为坏（关键：故障传播） ----------
    if mark_current_broken and cur_id and cur_id != vid:
        cur = _find(m, cur_id)
        if cur:
            cur["status"] = "broken"
            cur["reason"] = reason or "迁移离开时标记"
            report["steps"].append(
                f"标记 {cur_id} 为 broken（原因: {reason or '未说明'}）")
        # 种故障钩子（可删的轻量标记）
        plant_hook(cur_id, kind="fault",
                   note=reason or "迁移离开时标记故障",
                   payload={"fell_back_to": vid, "direction": direction})
        # 写进记忆库：让下个版本能"知道"
        _record_fault_to_memory(cur_id, vid, reason)
    elif cur_id and cur_id != vid:
        report["steps"].append(
            f"不标记 {cur_id}（{direction}：当前版本是好的，不该污染）")

    # ---------- ② 迁移代码 ----------
    code, out = _git("reset", "--hard", target["commit"])
    if code != 0:
        # tag 可能失效，尝试用 tag 名
        code, out = _git("reset", "--hard", vid)
    report["steps"].append(
        f"代码迁移到 {target['commit']}: {'✅' if code == 0 else '❌ ' + out[:100]}")

    # ---------- ③ 迁移数据 ----------
    # E-435：artifacts 为空时，下面这个循环一次都不执行，
    # 迁移报告里也不会出现任何"数据"字样 → 看起来成功，实际只迁了代码。
    # 这是"静默失败"（返回 ok 但没做该做的事），必须显式说出来。
    artifacts = target.get("artifacts") or {}
    if not artifacts:
        report["steps"].append(
            f"⚠ 数据: 版本 {vid} 无快照（artifacts 为空）"
            f" → 代码已迁移，但记忆库保持当前内容，未回滚")
        report["data_migrated"] = False
    for name, rel in artifacts.items():
        src = os.path.join(_HERE, rel)
        dst = VERSION_PARTS.get(name)
        if not dst or not os.path.exists(src):
            report["steps"].append(f"数据 {name}: 跳过（源不存在）")
            report["data_migrated"] = False
            continue
        try:
            shutil.copy2(src, dst)
            report["steps"].append(f"数据 {name}: ✅ 已恢复")
        except Exception as e:
            report["steps"].append(f"数据 {name}: ❌ {e}")
            report["data_migrated"] = False
    if "data_migrated" not in report and artifacts:
        report["data_migrated"] = True

    # ---------- ④ 更新清单 ----------
    m["current"] = vid
    if target.get("status") == "broken" and not reason:
        # 不改状态，但报告里提示
        report["steps"].append(f"⚠ 目标版本 {vid} 曾被标记 broken")
    _save_manifest(m)

    report["ok"] = code == 0
    return report


def register_tag(vid: str, commit: str | None = None,
                 status: str = "ok", reason: str = "",
                 note: str = "", with_artifacts: bool = False) -> dict:
    """
    把一个**已存在的 git tag** 登记进 versions.json（不新建 commit / 不复制 DB）。

    E-434：实查发现 git 有 79 个 tag，versions.json 只登记了 8 个，
    导致 migrate_to() 对 71 个 tag 一律抛 VersionError。
    本函数只补登记，让版本级环（主张3「旧版本可继续独立存在」）从"tag 在但不可达"
    变成"可在清单里被迁移"。

    诚实边界：
      · with_artifacts=False（默认）→ 只登记代码版本，artifacts 为空，
        迁移时记忆库**不会**回滚。E-435 起这种情况会在报告里明说。
      · 要真正的"数据也回滚"，必须有当时的 DB 快照，而历史 tag 大多没有。
    """
    m = _load_manifest()
    if _find(m, vid):
        raise VersionError(f"版本已登记: {vid}")

    tags = set(all_tags())
    if vid not in tags:
        raise VersionError(f"git 里没有这个 tag: {vid}")

    code, out = _git("rev-list", "-n", "1", vid)
    real_commit = out.strip() if code == 0 else ""
    if not real_commit:
        raise VersionError(f"无法解析 tag {vid} 的 commit")

    # 该 tag 的提交时间（用于排序，不能全用 time.time()）
    c2, ts = _git("log", "-1", "--format=%ct", vid)
    at = int(ts.strip()) if c2 == 0 and ts.strip().isdigit() else time.time()

    artifacts = {}
    if with_artifacts:
        for name, src in VERSION_PARTS.items():
            ext = os.path.splitext(src)[1]
            dst = os.path.join(_SNAP, f"{vid}{'.' + name if name != 'db' else ''}{ext}")
            if os.path.exists(dst):
                artifacts[name] = os.path.relpath(dst, _HERE)

    entry = {
        "id": vid, "at": at, "commit": real_commit[:7],
        "note": note or "补登记（E-434）",
        "artifacts": artifacts, "status": status,
        "reason": reason, "verified": False,
    }
    m.setdefault("versions", []).append(entry)
    _save_manifest(m)
    return entry


def backfill_tags(limit: int | None = None, dry_run: bool = True) -> dict:
    """
    批量补登记所有"git 有 tag 但 versions.json 没有"的版本。

    dry_run=True（默认）只报告差多少，不写入。

    返回 {"missing": [...], "newly": [...], "skipped": [...], "dry_run": bool}
    """
    m = _load_manifest()
    registered = {v["id"] for v in m.get("versions", [])}
    tags = all_tags()
    missing = [t for t in tags if t not in registered]
    report = {"total_tags": len(tags), "registered_before": len(registered),
              "missing": missing, "newly": [], "dry_run": dry_run}
    if dry_run:
        return report

    todo = missing[:limit] if limit else missing
    for vid in todo:
        try:
            register_tag(vid)
            report["newly"].append(vid)
        except VersionError:
            pass
    m2 = _load_manifest()
    report["registered_after"] = len(m2.get("versions", []))
    return report


def _record_fault_to_memory(bad_vid: str, fallback_vid: str, reason: str):
    """
    把故障写进记忆库——这是"版本 2 能知道版本 3 出问题了"的实现。

    写入一条 L3 记忆，下次在 v2 上启动时，模型能看到：
    "版本 X 因为 Y 坏掉了，已回退到 Z"
    """
    try:
        sys.path.insert(0, _MEM)
        from store_v2 import MemoryStore
        st = MemoryStore(_DB)
        text = (f"版本故障记录：版本 {bad_vid} 在 {time.strftime('%Y-%m-%d %H:%M')} "
                f"出问题（{reason or '原因未记录'}），已回退到版本 {fallback_vid}。"
                f"下次回到 {bad_vid} 时需先修复该问题。")
        st.add(text, resolve_conflict=False)
        st.con.close()
    except Exception:
        pass


# ============================================================
# 状态查询
# ============================================================

def version_chain() -> list[dict]:
    """
    返回完整版本链（按时间排序），标注每个版本的状态。

    这是"圆筒"的展开视图：能看到所有生命周期循环。
    """
    m = _load_manifest()
    versions = sorted(m.get("versions", []), key=lambda v: v.get("at", 0))
    cur = m.get("current")

    out = []
    for v in versions:
        out.append({
            "id": v["id"],
            "at": v.get("at"),
            "status": v.get("status", "unknown"),
            "reason": v.get("reason", ""),
            "note": v.get("note", ""),
            "commit": v.get("commit", ""),
            "is_current": v["id"] == cur,
            "has_data": bool(v.get("artifacts")),
        })
    return out


def health_check() -> dict:
    """
    检查当前系统是否健康（能否启动）。

    这是"永远不会死亡"的保障：
      如果当前版本坏了，自动找到最近的好版本并建议迁移。
    """
    report = {"healthy": True, "issues": [], "recommendation": None}

    # 检查关键文件
    for name, path in VERSION_PARTS.items():
        if not os.path.exists(path):
            report["healthy"] = False
            report["issues"].append(f"缺少 {name}: {path}")

    # 检查 Python 模块能否导入
    try:
        env = dict(os.environ)
        env["KMP_DUPLICATE_LIB_OK"] = "TRUE"
        r = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, r'%s'); "
             "import store_v2, hook, consolidate, audit, seed; print('OK')" % _MEM],
            capture_output=True, text=True, timeout=60, env=env)
        if "OK" not in (r.stdout or ""):
            report["healthy"] = False
            report["issues"].append(f"模块导入失败: {(r.stderr or '')[:200]}")
    except Exception as e:
        report["healthy"] = False
        report["issues"].append(f"模块检查异常: {e}")

    # 检查 DB 可读
    try:
        sys.path.insert(0, _MEM)
        from store_v2 import MemoryStore
        st = MemoryStore(_DB)
        n = st.stats().get("valid", 0)
        st.con.close()
        report["valid_memories"] = n
    except Exception as e:
        report["healthy"] = False
        report["issues"].append(f"DB 读取失败: {e}")

    # 若不健康，推荐最近的好版本
    if not report["healthy"]:
        chain = version_chain()
        for v in reversed(chain):
            if v["status"] == "ok" and v["has_data"] and not v["is_current"]:
                report["recommendation"] = v["id"]
                break

    return report


# ============================================================
# 命令行
# ============================================================

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1]

    if cmd == "chain":
        chain = version_chain()
        print("=" * 70)
        print("版本链（生命周期循环）")
        print("=" * 70)
        for v in chain:
            mark = "▶" if v["is_current"] else " "
            icon = {"ok": "✅", "broken": "❌", "unknown": "·"}.get(
                v["status"], "·")
            t = time.strftime("%m-%d %H:%M",
                              time.localtime(v["at"])) if v["at"] else "?"
            data = "有数据" if v["has_data"] else "无数据"
            print(f" {mark} {icon} {v['id']:<10} {t}  {data:<6} {v['note'][:30]}")
            if v["reason"]:
                print(f"      原因: {v['reason'][:60]}")
        return

    if cmd == "backfill":
        dry = "--apply" not in sys.argv
        r = backfill_tags(dry_run=dry)
        print("=" * 70)
        print(f"版本补登记（{'dry-run，不写入' if dry else '写入'}）")
        print("=" * 70)
        print(f"  git tag 总数 : {r['total_tags']}")
        print(f"  已登记(前)   : {r['registered_before']}")
        print(f"  未登记       : {len(r['missing'])}")
        if r["missing"]:
            print(f"  未登记样例   : {', '.join(r['missing'][:12])}")
        if not dry:
            print(f"  本次新登记   : {len(r['newly'])}")
            print(f"  已登记(后)   : {r['registered_after']}")
            print("\n  注意：补登记只登记代码版本，artifacts 为空。")
            print("  迁移到这些版本时记忆库不会回滚（E-435 起报告会明说）。")
        else:
            print("\n  这是 dry-run。加 --apply 才真正写入。")
        return

    if cmd == "health":
        h = health_check()
        print("=" * 70)
        print("系统健康检查")
        print("=" * 70)
        print(f"\n  健康: {'✅ 是' if h['healthy'] else '❌ 否'}")
        if h.get("valid_memories") is not None:
            print(f"  有效记忆: {h['valid_memories']} 条")
        if h["issues"]:
            print(f"\n  问题:")
            for i in h["issues"]:
                print(f"    · {i}")
        if h["recommendation"]:
            print(f"\n  建议迁移到: {h['recommendation']}")
        return

    if cmd == "snapshot":
        if len(sys.argv) < 3:
            print("用法: lifecycle.py snapshot <版本号> [说明]")
            return
        v = sys.argv[2]
        note = sys.argv[3] if len(sys.argv) > 3 else ""
        e = snapshot_version(v, note)
        print(f"版本 {v} 已创建")
        print(f"  commit: {e['commit']}")
        print(f"  数据:   {list(e['artifacts'].keys())}")
        return

    if cmd == "migrate":
        if len(sys.argv) < 3:
            print("用法: lifecycle.py migrate <目标版本> [原因]")
            return
        vid = sys.argv[2]
        reason = sys.argv[3] if len(sys.argv) > 3 else ""
        try:
            r = migrate_to(vid, reason)
            print(f"迁移 {r['from']} → {r['to']}")
            for s in r["steps"]:
                print(f"  {s}")
            print(f"\n结果: {'✅ 成功' if r['ok'] else '❌ 失败'}")
        except VersionError as e:
            print(f"❌ {e}")
        return

    if cmd == "hooks":
        sub = sys.argv[2] if len(sys.argv) > 2 else "list"
        if sub == "list":
            s = hooks_summary()
            print("=" * 70)
            print(f"版本钩子（{s['total']} 个，文件 {s['file']}）")
            print("=" * 70)
            if s["by_kind"]:
                print(f"  按类型: {s['by_kind']}")
            if s["by_version"]:
                print(f"  按版本: {s['by_version']}")
            print()
            for h in list_hooks()[:30]:
                t = time.strftime("%m-%d %H:%M", time.localtime(h.get("at", 0)))
                print(f"  [{h.get('kind','?'):<8}] {h.get('version','?'):<10} "
                      f"{t}  {h.get('note','')[:40]}")
            return
        if sub == "plant":
            if len(sys.argv) < 5:
                print("用法: lifecycle.py hooks plant <版本> <说明>")
                return
            h = plant_hook(sys.argv[3], kind="marker", note=sys.argv[4])
            print(f"钩子已种: {h['id']}")
            return
        if sub == "remove":
            if len(sys.argv) < 4:
                print("用法: lifecycle.py hooks remove <钩子id>")
                return
            ok = remove_hook(sys.argv[3])
            print(f"删除 {'✅' if ok else '❌ 未找到'}")
            return
        if sub == "clear":
            kind = sys.argv[3] if len(sys.argv) > 3 else None
            n = clear_hooks(kind)
            print(f"已清空 {n} 个钩子" + (f"（类型 {kind}）" if kind else ""))
            print("注意：钩子清空后版本本体不受影响，机制仍可工作。")
            return
        print(f"未知子命令: {sub}")
        return

    print(f"未知命令: {cmd}")
    print(__doc__)


if __name__ == "__main__":
    main()
