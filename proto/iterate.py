"""
iterate.py —— 自主迭代规程（版本跃迁 / 验证 / 回退）

用户要求（2026-11-07）：
  "你可以开始自主研究，并且迭代你的记忆系统了。
   先给你一个保险的指导：你要记得保存迭代版本，比如用 git 进行版本管理，
   注意这些版本跃迁，避免万一改坏了。
   你这种迭代方法就是要实验性的、以真实数据为主，
   并且要具备自己进行版本跃迁、版本迭代和回退的能力。"

设计原则：
  1. 任何改动前先建快照（记忆库 + 代码状态）
  2. 改动后必须跑红线回归，不过就自动回退
  3. 每次迭代都有明确的实验编号和结论记录
  4. 回退是**一等公民**，不是补救措施

用法（命令行）：
  python iterate.py start  <实验号> <描述>     # 开始迭代：建快照 + 打 git tag
  python iterate.py check  <实验号>            # 跑全部回归，判定是否可保留
  python iterate.py commit <实验号> <结论>     # 通过则提交
  python iterate.py revert <实验号>            # 回退到迭代前
  python iterate.py list                       # 查看迭代历史

用法（Python）：
  from iterate import Iteration
  with Iteration("E-230", "尝试 X 优化") as it:
      ...  # 做改动
      it.verify()   # 不过会自动抛异常并回退
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)                       # 肥鱼之心/
_MEM = os.path.join(_HERE, "cosmos_mem")             # proto/cosmos_mem/
_DB = os.path.join(_MEM, "dsh_mem.db")
_SNAP_DIR = os.path.join(_HERE, "work", "snapshots")
_LOG = os.path.join(_HERE, "work", "iterations.json")

# 回归套件（按重要性排序，全过才算通过）
REGRESSION_SUITE = [
    ("红线回归", "run_e124_redlines.py", 300),
    ("种子验证", "run_seed_verify.py", 200),
    ("分层保护", "run_e28_levels.py", 200),
    ("生命周期", "run_e29_lifecycle.py", 200),
    ("集成测试", "run_e22b_integration.py", 300),
    # E-252：静默失败回归（返回 ok 但实际没做事，极危险）
    ("静默失败", "run_e252_silent_regression.py", 200),
]

# 前端安全检查（node 脚本，单独跑）
CLIENT_CHECKS = [
    ("前端安全", "check-client.mjs"),
    # E-290：生命周期监督器离线验证
    ("生命周期拦截", "check-lifecycle.mjs"),
    # E-296：自重启机制离线验证
    ("自重启", "check-selfreload.mjs"),
    # E-329：消息提取游标（含 log 重置边界）
    ("消息提取", "check-scan-cursor.mjs"),
    # E-348：只注入一次（首轮判断顺序 + 并发防重）
    ("注入控制", "check-inject-once.mjs"),
    # E-350：search_scoped 的 flag 透传与 L0/L1 名额语义
    ("分层检索", "run_e351_scoped.py"),
    # E-361：插件实际调用链路能拿到 L0/L1（含数据新鲜度诚实检查）
    ("注入链路", "run_e361_chain_reg.py"),
    # E-377：注入次数的正确判据（零注入≠bug，多次须按进程段切分）
    ("注入判据", "run_e377_criteria.py"),
    # E-378：同一 turn 多次 steer 的真实证据（E-371 修复生效证明）
    ("同轮续跑", "run_e378_same_turn.py"),
    # E-386：注入策略实验的可信判据（覆盖率可信、效率不可靠）
    ("注入策略", "run_e386_regression.py"),
    # E-389：话题切换检测的否证（防止重复尝试已失败方案）
    ("话题检测", "run_e389_falsified.py"),
    # E-391：注入观测字段（让 E-348/E-350 的修复可被验证）
    ("注入观测", "run_e391_observability.py"),
    # E-394：注入预算的两种语义（body 预算 vs 总量上限）
    ("注入预算", "run_e394_budget.py"),
    # E-396：架构文档一致性（配置覆盖、决策记录、字段名）
    ("文档一致", "run_e396_doc_check.py"),
    # E-399：环境事件模块（主张6 最小实现，白名单守边界）
    ("环境事件", "run_e399_env_reg.py"),
    # E-400：验证回归自动记录环境事件（闭环成立）
    ("环境闭环", "run_e400_verify.py"),
    # E-408：生产库卫生（无有效测试数据、环境事件默认过滤）
    ("库卫生", "run_e408_hygiene.py"),
    # E-412：实验执行包装器（单跑实验也记录环境事件）
    ("实验记录", "run_e412_exp_run_reg.py"),
    # E-414：记忆↔环境结果关联（人工标注 --ep，不虚构）
    ("结果关联", "run_e414_link_reg.py"),
    # E-416：周期汇报机制 + N 值标定（省 token）
    ("汇报机制", "run_e416_n_calib.py"),
    # E-417：MESH_EP 让回归自动关联记忆（仍要求人工标注，不做自动推断）
    ("自动关联", "run_e417_mesh_ep.py"),
    # E-418：关联覆盖率指标（主张6 的进度条）
    ("关联率", "run_e418_link_ratio.py"),
    # E-419：备份全表校验（R1 红线：全部无损，不是三张表无损）
    ("备份全表", "run_e419_backup_full.py"),
    # E-420：逐字段比对（R1 硬验证，比行数更深一层）
    ("字段比对", "run_e420_field_level.py"),
]


# ============================================================
# git 辅助
# ============================================================

def _git(*args, cwd=None) -> tuple[int, str]:
    try:
        r = subprocess.run(["git"] + list(args), cwd=cwd or _ROOT,
                           capture_output=True, text=True, timeout=120,
                           encoding="utf-8", errors="replace")
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return 1, str(e)


def git_status() -> dict:
    code, out = _git("status", "--porcelain")
    lines = [l for l in out.splitlines() if l.strip()]
    code2, head = _git("rev-parse", "--short", "HEAD")
    code3, tags = _git("tag", "--sort=-creatordate")
    return {
        "clean": len(lines) == 0,
        "changed": len(lines),
        "head": head.strip(),
        "tags": [t for t in tags.splitlines() if t.strip()][:5],
    }


def git_snapshot(tag: str, message: str = "") -> tuple[bool, str]:
    """提交全部改动并打 tag（作为可回退点）"""
    _git("add", "-A")
    code, out = _git("commit", "-m", message or f"snapshot {tag}")
    # 无改动时 commit 会失败，不算错
    ok_tag, out_tag = _git("tag", "-f", "-a", tag,
                           "-m", message or f"snapshot {tag}")
    return ok_tag == 0, out_tag or out


def git_revert(tag: str) -> tuple[bool, str]:
    """回退到指定 tag"""
    code, out = _git("reset", "--hard", tag)
    return code == 0, out


# ============================================================
# 记忆库快照（独立于 git——DB 不进版本库）
# ============================================================

def db_snapshot(label: str) -> str | None:
    """备份记忆库（DB 在 .gitignore 里，必须单独备份）"""
    if not os.path.exists(_DB):
        return None
    os.makedirs(_SNAP_DIR, exist_ok=True)
    dst = os.path.join(_SNAP_DIR, f"{label}.db")
    shutil.copy2(_DB, dst)
    return dst


def db_restore(path: str) -> bool:
    """从快照恢复记忆库"""
    if not os.path.exists(path):
        return False
    # 先备份当前（万一恢复也出问题）
    if os.path.exists(_DB):
        shutil.copy2(_DB, _DB + ".pre_restore")
    shutil.copy2(path, _DB)
    return True


# ============================================================
# 回归验证
# ============================================================

def run_regression(verbose: bool = True) -> dict:
    """跑全部回归，返回 {name: {pass, output}}"""
    env = dict(os.environ)
    env["KMP_DUPLICATE_LIB_OK"] = "TRUE"
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("HF_HOME", os.path.join(_ROOT, ".hf"))

    results = {}
    for name, script, timeout in REGRESSION_SUITE:
        path = os.path.join(_HERE, script)
        if not os.path.exists(path):
            results[name] = {"pass": None, "output": "脚本不存在"}
            continue
        try:
            r = subprocess.run([sys.executable, "-u", path],
                               cwd=_HERE, env=env, capture_output=True,
                               text=True, timeout=timeout,
                               encoding="utf-8", errors="replace")
            out = (r.stdout or "") + (r.stderr or "")
            ok = r.returncode == 0
            # 从输出里提取 "结果：N/M" 或红线结论
            summary = ""
            for line in out.splitlines():
                if "结果：" in line or "四条红线" in line or "未退化" in line:
                    summary = line.strip()
            results[name] = {"pass": ok, "output": out[-2000:],
                             "summary": summary}
            if verbose:
                mark = "✅" if ok else "❌"
                print(f"  {mark} {name}  {summary}")
        except subprocess.TimeoutExpired:
            results[name] = {"pass": False, "output": "超时",
                             "summary": "超时"}
            if verbose:
                print(f"  ❌ {name}  超时 ({timeout}s)")
        except Exception as e:
            results[name] = {"pass": False, "output": str(e),
                             "summary": str(e)}
            if verbose:
                print(f"  ❌ {name}  {e}")

    all_pass = all(v["pass"] is True for v in results.values())
    skipped = [k for k, v in results.items() if v["pass"] is None]
    if skipped:
        all_pass = False

    # ---------- 客户端/针对性检查（node 或 python 脚本）----------
    # E-351：支持 .py，因为有些检查需要读 SQLite / 调 Python 侧 API
    import shutil as _sh
    node = _sh.which("node")
    for name, script in CLIENT_CHECKS:
        path = os.path.join(_HERE, script)
        if not os.path.exists(path):
            results[name] = {"pass": None, "output": "脚本不存在",
                             "summary": "跳过"}
            if verbose:
                print(f"  ⚠ {name}  脚本不存在，跳过")
            all_pass = False
            continue
        is_py = script.endswith(".py")
        runner = _sh.which("python") if is_py else node
        if not runner:
            results[name] = {"pass": None,
                             "output": "python/node 不可用",
                             "summary": "跳过"}
            if verbose:
                print(f"  ⚠ {name}  解释器不可用，跳过")
            continue
        try:
            r = subprocess.run([runner, path], cwd=_HERE, env=env,
                               capture_output=True, text=True, timeout=180,
                               encoding="utf-8", errors="replace")
            out = (r.stdout or "") + (r.stderr or "")
            ok = r.returncode == 0
            summary = ""
            for line in out.splitlines():
                if "全部检查通过" in line:
                    summary = "全部检查通过"
                elif "发现" in line and "问题" in line:
                    summary = line.strip()
            results[name] = {"pass": ok, "output": out[-2000:],
                             "summary": summary}
            if verbose:
                mark = "✅" if ok else "❌"
                print(f"  {mark} {name}  {summary}")
        except Exception as e:
            results[name] = {"pass": False, "output": str(e),
                             "summary": str(e)}
            if verbose:
                print(f"  ❌ {name}  {e}")

    all_pass = all(v["pass"] is True for v in results.values())
    # E-400：把回归结果记成环境事件
    #
    # 《环管群》主张6 要求记录真实环境反馈，
    # 但如果是手工记录，就永远只有测试数据。
    # 这里让每次跑回归都自动落一条真实事件：
    #   · 回归全过   → test_pass，outcome="N/N"
    #   · 有失败     → test_fail，outcome="N/M"，detail 列出失败项
    #
    # 这是客观事实（脚本真的跑了、返回码真的如此），
    # 不是我的主观评价 —— 符合 5.2 的「内部奖励不能冒充环境因果」。
    _record_regression_event(results, all_pass)

    return {"all_pass": all_pass, "items": results,
            "skipped": [k for k, v in results.items() if v["pass"] is None]}


def _record_regression_event(results: dict, all_pass: bool) -> None:
    """
    把一次回归的结果记成环境事件（E-400）。

    失败不影响主流程 —— 环境记录是观测，不是门禁。

    E-417：支持 MESH_EP 环境变量传入关联记忆 id（逗号分隔）。
      用法：
        MESH_EP=79e9614e,1c149c21 python iterate.py check E-418

      ★ 仍然要求**人工标注**，不接受自动推断。
        自动推断 = 模型自己说"这条有用"，违反《环管群》5.2。
        环境变量只是让标注更方便，不改变"必须显式标注"的性质。
    """
    try:
        sys.path.insert(0, _MEM)
        from store_v2 import MemoryStore
        import environment as env

        if not os.path.exists(_DB):
            return

        st = MemoryStore(_DB)
        env.init(st)

        # E-417：读人工标注的关联记忆
        ep_env = os.environ.get("MESH_EP", "").strip()
        ep_ids = [x.strip() for x in ep_env.split(",") if x.strip()]

        total = len(results)
        passed = sum(1 for v in results.values() if v.get("pass") is True)
        failed = [k for k, v in results.items() if v.get("pass") is False]

        if all_pass:
            env.record(
                st, "test_pass",
                source="iterate.py check",
                outcome=f"{passed}/{total}",
                detail="全部回归通过",
                task="regression",
                ep_ids=ep_ids,
            )
        else:
            env.record(
                st, "test_fail",
                source="iterate.py check",
                outcome=f"{passed}/{total}",
                detail=("失败项: " + ", ".join(failed[:8]))[:900],
                task="regression",
                ep_ids=ep_ids,
            )
        st.con.close()
    except Exception:
        # 环境记录失败不阻断回归
        pass


# ============================================================
# 迭代记录
# ============================================================

def _load_log() -> list:
    if not os.path.exists(_LOG):
        return []
    try:
        with open(_LOG, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_log(entries: list) -> None:
    os.makedirs(os.path.dirname(_LOG), exist_ok=True)
    with open(_LOG, "w", encoding="utf-8") as f:
        json.dump(entries[:200], f, ensure_ascii=False, indent=1)


def record(entry: dict) -> None:
    log = _load_log()
    log.insert(0, entry)
    _save_log(log)


# ============================================================
# 迭代上下文管理器（推荐的编程式用法）
# ============================================================

class Iteration:
    """
    迭代上下文：自动建快照、验证、失败回退。

    用法：
        with Iteration("E-230", "尝试 X 优化") as it:
            # 做改动...
            it.verify()      # 不过会抛 RegressionFailed
        # 正常退出 → caller 再调 it.commit("结论") 或手动 commit

    注意：verify() 失败时会自动 revert 并抛异常，
          所以 with 块里的后续代码不会执行。
    """

    def __init__(self, exp_id: str, desc: str,
                 auto_revert: bool = True, run_check: bool = True):
        self.exp_id = exp_id
        self.desc = desc
        self.auto_revert = auto_revert
        self.run_check = run_check
        self.tag = f"iter-{exp_id}"
        self.db_snap = None
        self.verified = False
        self.started_at = time.time()

    def __enter__(self):
        print(f"\n{'='*70}")
        print(f"迭代开始：{self.exp_id} — {self.desc}")
        print("=" * 70)

        # 1. 代码快照（打 tag 作为回退点）
        st = git_status()
        _changed = st["changed"]
        _state = "干净" if st["clean"] else "%d 个改动未提交" % _changed
        print(f"\n① 代码状态: HEAD={st['head']}, {_state}")
        ok, msg = git_snapshot(self.tag, f"pre-{self.exp_id}: {self.desc}")
        print(f"   快照 tag: {self.tag} {'✅' if ok else '⚠ ' + msg[:80]}")

        # 2. 记忆库快照
        self.db_snap = db_snapshot(f"pre-{self.exp_id}")
        if self.db_snap:
            size = os.path.getsize(self.db_snap) / 1e6
            print(f"   DB 快照: {os.path.basename(self.db_snap)} ({size:.2f} MB)")
        return self

    def verify(self) -> bool:
        """跑回归；失败则回退并抛异常"""
        print(f"\n② 回归验证")
        r = run_regression(verbose=True)
        self.verified = r["all_pass"]

        record({
            "exp_id": self.exp_id, "desc": self.desc,
            "phase": "verify", "at": time.time(),
            "all_pass": r["all_pass"],
            "items": {k: v["pass"] for k, v in r["items"].items()},
            "tag": self.tag,
        })

        if not r["all_pass"]:
            failed = [k for k, v in r["items"].items() if v["pass"] is not True]
            print(f"\n❌ 回归失败: {failed}")
            if self.auto_revert:
                print(f"③ 自动回退到 {self.tag}")
                ok, msg = git_revert(self.tag)
                print(f"   代码回退 {'✅' if ok else '❌ ' + msg[:100]}")
                if self.db_snap and db_restore(self.db_snap):
                    print(f"   DB 回退 ✅")
            raise RegressionFailed(f"{self.exp_id} 回归失败: {failed}")

        print(f"\n✅ 回归全过")
        return True

    def commit(self, conclusion: str) -> bool:
        """迭代通过：提交代码 + 打 tag + 记录结论"""
        tag = f"{self.exp_id}"
        ok, msg = git_snapshot(tag, f"{self.exp_id}: {self.desc}\n\n{conclusion}")
        record({
            "exp_id": self.exp_id, "desc": self.desc,
            "phase": "commit", "at": time.time(),
            "conclusion": conclusion, "tag": tag,
        })
        print(f"\n④ 已提交 tag {tag} {'✅' if ok else '⚠ ' + msg[:80]}")
        return ok

    def revert(self) -> bool:
        ok, msg = git_revert(self.tag)
        if self.db_snap:
            db_restore(self.db_snap)
        record({"exp_id": self.exp_id, "desc": self.desc,
                "phase": "revert", "at": time.time(), "tag": self.tag})
        return ok

    def __exit__(self, exc_type, exc, tb):
        if exc_type and self.auto_revert and not self.verified:
            print(f"\n⚠ 迭代异常退出，回退到 {self.tag}")
            self.revert()
        return False


class RegressionFailed(Exception):
    pass


# ============================================================
# 命令行
# ============================================================

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1]

    if cmd == "list":
        log = _load_log()
        print(f"迭代记录（最近 {min(len(log), 20)} 条）\n")
        for e in log[:20]:
            t = time.strftime("%m-%d %H:%M", time.localtime(e.get("at", 0)))
            phase = {"start": "开始", "verify": "验证",
                     "commit": "提交", "revert": "回退"}.get(
                e.get("phase"), e.get("phase"))
            mark = "✅" if e.get("all_pass") else (
                "❌" if e.get("all_pass") is False else "·")
            print(f"  {t}  {mark} {e.get('exp_id','?'):<10} {phase:<4} "
                  f"{e.get('desc','')[:40]}")
        st = git_status()
        print(f"\n当前: HEAD={st['head']}  "
              f"{'干净' if st['clean'] else str(st['changed'])+' 个改动'}")
        print(f"标签: {', '.join(st['tags'])}")
        return

    if cmd == "start":
        if len(sys.argv) < 4:
            print("用法: python iterate.py start <实验号> <描述>")
            return
        it = Iteration(sys.argv[2], sys.argv[3])
        it.__enter__()
        print(f"\n现在可以开始改动了。改完运行：")
        print(f"  python iterate.py check {sys.argv[2]}")
        return

    if cmd == "check":
        exp = sys.argv[2] if len(sys.argv) > 2 else "unknown"
        print(f"回归验证：{exp}\n")
        r = run_regression(verbose=True)
        print(f"\n{'='*60}")
        print(f"结论: {'✅ 全部通过，可以保留' if r['all_pass'] else '❌ 有失败，应回退'}")
        record({"exp_id": exp, "desc": "check", "phase": "verify",
                "at": time.time(), "all_pass": r["all_pass"],
                "items": {k: v["pass"] for k, v in r["items"].items()}})
        sys.exit(0 if r["all_pass"] else 1)

    if cmd == "revert":
        tag = sys.argv[2] if len(sys.argv) > 2 else ""
        if not tag:
            print("用法: python iterate.py revert <tag>")
            return
        ok, msg = git_revert(tag)
        print(f"回退到 {tag}: {'✅' if ok else '❌ ' + msg[:200]}")
        return

    print(f"未知命令: {cmd}")
    print(__doc__)


if __name__ == "__main__":
    main()
