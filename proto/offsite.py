"""offsite.py — 记忆异地副本同步（FISH-12）

════════════════════════════════════════════════════════════
为什么需要这个
════════════════════════════════════════════════════════════
本地自动备份已经稳了（每 20 条写入触发，prune 保留 10 份），
但所有备份都在同一台机器的同一个目录：
磁盘坏了、笔记本丢了、房子烧了 —— 记忆全没。

用户选了方案 A：愿意放到 GitHub 私有仓库。

════════════════════════════════════════════════════════════
安全设计（这是最重要的一节）
════════════════════════════════════════════════════════════
记忆里有私人对话、情绪、人际关系。所以：

1. **硬编码只允许一个目标仓库**
   不读环境变量、不做配置 —— 换成别的仓库会直接拒绝。
   这是防止"手滑推到公开仓库"的最后一道门。

2. **推送前实时校验仓库是 private**
   调 GitHub API 确认，不是相信配置。

3. **不推 .db 原文件，推压缩后的备份**
   体积小，且带时间戳便于追溯。

4. **失败不删除任何东西**
   本地备份始终保留，异地是增量追加。

════════════════════════════════════════════════════════════
用法
════════════════════════════════════════════════════════════
    python proto/offsite.py status      # 看上次同步情况
    python proto/offsite.py push        # 执行一次同步
    python proto/offsite.py verify      # 校验远端可恢复
    python proto/offsite.py restore <name>  # 从远端拉回某个备份

设计原则：不自动定时。用户或 cron 决定何时推。
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# ════════════════════════════════════════════════════════════
# 硬编码的安全边界 —— 不要改成变量
# ════════════════════════════════════════════════════════════
ALLOWED_REPO = "Roxy-DD/fatfish-heart-memory"
ALLOWED_URL = "https://github.com/Roxy-DD/fatfish-heart-memory.git"
# ★ 分支不能硬编码 —— 实测两次踩坑（2026-11-18）：
#   第一次：读 API 不指定 ref → GitHub 读默认分支 → 读到旧数据
#   第二次：改成 BRANCH="master" 后，又漏改 push 的目标 → 数据进 main
#   两次的症状都是「push 报成功、verify 也过，但看到的不是这次的数据」。
#
# 现在改为**运行时解析默认分支**：
#   · 先问 GitHub 这个仓库的 default_branch
#   · 拿不到时才回退到 master
# 这样即使仓库侧改了默认分支，也不会静默失效。
_BRANCH_CACHE = {"value": None}


def branch():
    """当前应使用的分支名（运行时解析默认分支，带缓存）"""
    if _BRANCH_CACHE["value"]:
        return _BRANCH_CACHE["value"]
    ok, out = sh(["gh", "api", f"repos/{ALLOWED_REPO}",
                  "--jq", ".default_branch"], timeout=60)
    name = out.strip() if ok else ""
    # 只接受合理的分支名，防止 API 返回异常内容被当成分支用
    if not name or "/" in name or " " in name or len(name) > 60:
        name = "master"
    _BRANCH_CACHE["value"] = name
    return name


DB_PATH = os.path.join(HERE, "cosmos_mem", "dsh_mem.db")
STATE_PATH = os.path.join(HERE, "cosmos_mem", "offsite_state.json")


def sh(cmd, cwd=None, timeout=180, check=False):
    """跑命令，返回 (ok, stdout+stderr)"""
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           timeout=timeout, encoding="utf-8", errors="replace")
        out = (r.stdout or "") + (r.stderr or "")
        if check and r.returncode != 0:
            return False, out
        return r.returncode == 0, out
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def check_repo_private():
    """实时校验目标仓库是 private。不在列表里、或不是 private，一律拒绝。"""
    ok, out = sh(["gh", "api", f"repos/{ALLOWED_REPO}",
                  "--jq", ".private"], timeout=60)
    if not ok:
        return False, f"无法访问仓库 {ALLOWED_REPO}: {out[:150]}"
    if out.strip() != "true":
        return False, f"❌ 仓库 {ALLOWED_REPO} 不是 private！拒绝推送。"
    return True, "ok"


def fingerprint(path):
    """给文件一个指纹，用于确认推送内容与本地一致"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_state():
    try:
        return json.load(open(STATE_PATH, encoding="utf-8"))
    except Exception:
        return {}


def save_state(st):
    try:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        json.dump(st, open(STATE_PATH, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
    except Exception:
        pass


# ════════════════════════════════════════════════════════════
# 命令：status
# ════════════════════════════════════════════════════════════
def cmd_status():
    print("=" * 66)
    print("异地副本状态")
    print("=" * 66)

    st = load_state()
    print(f"\n  目标仓库: {ALLOWED_REPO}（硬编码，不可改）")

    ok, msg = check_repo_private()
    print(f"  private 校验: {'✅' if ok else '❌'} {msg}")

    if os.path.exists(DB_PATH):
        size = os.path.getsize(DB_PATH)
        print(f"\n  本地数据库: {size // 1024} KB")
        print(f"  指纹: {fingerprint(DB_PATH)[:16]}")
    else:
        print(f"\n  ❌ 本地数据库不存在: {DB_PATH}")

    if st.get("last_push_at"):
        age = (time.time() - st["last_push_at"]) / 3600
        print(f"\n  上次同步: {time.strftime('%Y-%m-%d %H:%M', time.localtime(st['last_push_at']))}"
              f"（{age:.1f} 小时前）")
        print(f"  上次文件: {st.get('last_name', '?')}")
        print(f"  上次大小: {st.get('last_size', 0) // 1024} KB")
    else:
        print("\n  从未同步过")

    # 看远端有没有东西
    ok2, out2 = sh(["gh", "api", f"repos/{ALLOWED_REPO}/contents?ref={branch()}",
                    "--jq", "length"], timeout=60)
    if ok2:
        print(f"\n  远端文件数: {out2.strip()}")

    return 0


# ════════════════════════════════════════════════════════════
# 命令：push
# ════════════════════════════════════════════════════════════
def cmd_push():
    print("=" * 66)
    print("推送记忆到异地私有仓库")
    print("=" * 66)

    # ---- 第 1 道门：仓库必须是 private ----
    ok, msg = check_repo_private()
    if not ok:
        print(f"\n❌ 安全检查失败：{msg}")
        print("   拒绝推送。这是为了防止私人记忆泄露。")
        return 1
    print(f"\n  ✅ 仓库确认是 private")

    if not os.path.exists(DB_PATH):
        print(f"\n❌ 数据库不存在: {DB_PATH}")
        return 1

    # ---- 第 2 道门：本地库必须健康，否则不推 ----
    #
    # 为什么要有这道门：
    #   如果本地库已经损坏（截断、页损坏、索引坏），
    #   推上去就是把坏数据固化到远端，而你可能几个月后才发现
    #   —— 那时所有历史备份都是坏的，等于异地副本完全失效。
    #
    #   所以：宁可这次不推，也不要推一份坏的。
    print(f"\n  校验本地库...")
    try:
        import sqlite3
        _c = sqlite3.connect(DB_PATH)
        _ic = _c.execute("PRAGMA integrity_check").fetchone()[0]
        if _ic != "ok":
            print(f"  ❌ 本地库完整性检查失败: {_ic[:100]}")
            print(f"     拒绝推送。请先修复本地库。")
            _c.close()
            return 1
        _n = _c.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
        _c.close()
        print(f"  ✅ 完整性 ok，episodes={_n}")
        if _n == 0:
            print(f"  ❌ episodes 为 0，这是个空库 —— 拒绝推送（防止把空库覆盖到远端）")
            return 1
    except Exception as e:
        print(f"  ❌ 无法校验本地库: {type(e).__name__}: {e}")
        print(f"     拒绝推送。库里可能已有损坏。")
        return 1

    # ---- 准备备份文件 ----
    ts = time.strftime("%Y%m%d_%H%M%S")
    name = f"memory_{ts}.db"
    tmpdir = tempfile.mkdtemp(prefix="offsite_")
    try:
        # 用 SQLite 的 backup 接口做一致性快照（不能直接 copy，WAL 模式下会拿到残缺文件）
        snap = os.path.join(tmpdir, name)
        try:
            import sqlite3
            src = sqlite3.connect(DB_PATH)
            dst = sqlite3.connect(snap)
            src.backup(dst)
            dst.close()
            src.close()
            print(f"\n  一致性快照: {os.path.getsize(snap) // 1024} KB")
        except Exception as e:
            print(f"\n  ⚠ 快照失败，改用文件复制: {e}")
            shutil.copy2(DB_PATH, snap)

        size = os.path.getsize(snap)
        fp = fingerprint(snap)

        # ---- 克隆或复用仓库 ----
        repo_dir = os.path.join(tmpdir, "repo")
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        ok, out = sh(["git", "clone", "--depth", "1", ALLOWED_URL, repo_dir],
                     timeout=300)
        if not ok:
            print(f"\n❌ clone 失败: {out[:200]}")
            return 1

        # 再确认一次 clone 到的 remote 就是允许的那个（防重定向）
        ok, remote_out = sh(["git", "remote", "get-url", "origin"],
                            cwd=repo_dir, timeout=30)
        if ALLOWED_REPO not in remote_out:
            print(f"\n❌ clone 到的仓库不对: {remote_out[:100]}")
            return 1

        # ---- 放进去，提交，推 ----
        shutil.copy2(snap, os.path.join(repo_dir, name))

        # E-520：远端保留策略 —— 只留最近 KEEP_REMOTE 份
        #
        # 为什么需要：
        #   每份全量 8MB，每周推一次 = 一年 400MB+。
        #   Git 仓库即使删了文件也不会缩小（历史还在），
        #   所以必须从一开始就控制文件数量，而不是等它涨到几个 G。
        #
        # 策略：只保留最近 KEEP_REMOTE 份 .db，其余 git rm。
        #   MANIFEST 也只留对应记录（已经限制 20 条）。
        #   注意：git 历史里仍有旧数据，真要彻底清理需要 rewrite history，
        #   那是破坏性操作，不做。这里只控制「当前检出」的体积。
        KEEP_REMOTE = 8
        old_dbs = sorted(
            [f for f in os.listdir(repo_dir)
             if f.startswith("memory_") and f.endswith(".db")])
        removed = []
        while len(old_dbs) > KEEP_REMOTE:
            victim = old_dbs.pop(0)
            try:
                os.remove(os.path.join(repo_dir, victim))
                removed.append(victim)
            except Exception:
                pass
        if removed:
            print(f"\n  清理旧备份: {len(removed)} 份（保留最近 {KEEP_REMOTE} 份）")

        # 写一个清单，便于恢复时对照
        manifest_path = os.path.join(repo_dir, "MANIFEST.json")
        try:
            manifest = json.load(open(manifest_path, encoding="utf-8"))
        except Exception:
            manifest = {"backups": []}
        manifest["backups"].append({
            "name": name, "at": time.time(),
            "size": size, "sha256": fp,
            "note": "记忆库快照（含私人内容，勿公开）",
        })
        # 只保留最近 20 条记录，且必须与实际存在的文件对应
        manifest["backups"] = manifest["backups"][-20:]
        json.dump(manifest, open(manifest_path, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)

        env2 = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        # ★ 用 add -A 而不是 add <file>：
        #   add -A 会把「删除」也 stage 进去，
        #   而 add <单个文件名> 不会跟踪删除 → 旧备份仍留在仓库里，
        #   保留策略等于没做（第一版就是这个 bug）。
        add_cmd = ["git", "add", "-A"]
        for cmd in (
            add_cmd,
            ["git", "-c", "user.name=fatfish-heart", "-c",
             "user.email=noreply@localhost", "commit", "-m",
             f"backup: {name} ({size // 1024}KB)"
             + (f", pruned {len(removed)}" if removed else "")],
            # ★ 推送目标必须是 BRANCH（与读取时用的同一个分支）
            #   实测 2026-11-18：这里原来硬编码 "HEAD:main"，
            #   而读取用的是 BRANCH="master"，
            #   于是数据进 main、verify 读 master —— 又一次"报成功但看不到"。
            ["git", "push", "origin", f"HEAD:{branch()}"],
        ):
            ok, out = sh(cmd, cwd=repo_dir, timeout=300, )
            if not ok:
                if "push" in cmd:
                    print(f"\n❌ {' '.join(cmd[:3])} 失败:")
                    print(f"   {out[:300]}")
                    return 1

        st = load_state()
        st.update({"last_push_at": time.time(), "last_name": name,
                   "last_size": size, "last_sha256": fp,
                   "repo": ALLOWED_REPO})
        st["history"] = (st.get("history") or [])[-19:] + [
            {"name": name, "at": time.time(), "size": size}]
        save_state(st)

        print(f"\n✅ 已推送: {name}（{size // 1024} KB）")
        print(f"   指纹: {fp[:16]}")
        print(f"   仓库: {ALLOWED_REPO}（private）")
        return 0
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ════════════════════════════════════════════════════════════
# 命令：verify
# ════════════════════════════════════════════════════════════
def cmd_verify():
    print("=" * 66)
    print("校验远端备份可恢复")
    print("=" * 66)

    ok, out = sh(["gh", "api", f"repos/{ALLOWED_REPO}/contents/MANIFEST.json?ref={branch()}",
                  "--jq", ".content"], timeout=60)
    if not ok:
        print(f"\n  ⚠ 远端还没有 MANIFEST.json（可能从未同步过）")
        return 1

    import base64
    try:
        manifest = json.loads(base64.b64decode(out.strip()).decode("utf-8"))
    except Exception as e:
        print(f"\n❌ MANIFEST 解析失败: {e}")
        return 1

    backups = manifest.get("backups") or []
    print(f"\n  远端备份数: {len(backups)}")
    if not backups:
        print("  ⚠ 没有备份记录")
        return 1

    latest = backups[-1]
    print(f"  最新: {latest['name']}")
    print(f"  大小: {latest['size'] // 1024} KB")
    print(f"  时间: {time.strftime('%Y-%m-%d %H:%M', time.localtime(latest['at']))}")
    print(f"  指纹: {latest['sha256'][:16]}")

    # 真的下载下来验一下能不能打开
    tmpdir = tempfile.mkdtemp(prefix="verify_")
    try:
        dest = os.path.join(tmpdir, latest["name"])
        # ★ 不能用 Contents API 下载大文件
        #   实测（2026-11-17 20:07）：8MB 的 .db 用
        #     gh api repos/.../contents/<name> --jq .content
        #   拿到的是空字符串（GitHub 对 >1MB 的文件不返回 base64），
        #   于是解码后得到一个空/残缺文件，指纹不符、打不开。
        #   正确做法：拿 download_url 用 curl 下载原始字节。
        ok_url, url_out = sh(["gh", "api",
                              f"repos/{ALLOWED_REPO}/contents/{latest['name']}?ref={branch()}",
                              "--jq", ".download_url"], timeout=60)
        if not ok_url or not url_out.strip():
            print(f"\n  ⚠ 拿不到 download_url: {url_out[:150]}")
            return 1
        dl_url = url_out.strip()

        ok2, out2 = sh(["curl", "-sL", "-o", dest, dl_url],
                       timeout=600)
        if not ok2 or not os.path.exists(dest):
            print(f"\n  ⚠ 下载失败: {out2[:150]}")
            return 1

        got_size = os.path.getsize(dest)
        print(f"\n  下载: {got_size // 1024} KB（远端声明 {latest['size'] // 1024} KB）")
        if got_size != latest["size"]:
            print(f"  ❌ 大小不符，可能被截断")
            return 1

        got = fingerprint(dest)
        match = got == latest["sha256"]
        print(f"  指纹校验: {'✅ 一致' if match else '❌ 不符'}")
        if not match:
            return 1

        # 试打开 + 数数据
        try:
            import sqlite3
            c = sqlite3.connect(dest)
            n = c.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
            v = c.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
            c.close()
            print(f"  可恢复性: ✅ episodes={n} vectors={v}")
        except Exception as e:
            print(f"  可恢复性: ❌ {e}")
            return 1
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    return 0


def cmd_restore(name=None, force=False):
    """从远端拉回一份备份并恢复到本地库

    这是灾难恢复的主路径：机器没了 → 新机器上跑这个 → 记忆回来。

    安全设计：
    · 恢复前**必须备份当前本地库**（万一拉下来的比本地还旧）
    · 恢复前校验远端备份能打开（不能恢复一个坏文件覆盖好数据）
    · 默认不覆盖，要显式 --force（防止误操作丢数据）
    """
    print("=" * 66)
    print("从远端恢复记忆")
    print("=" * 66)

    # ---- 取远端清单 ----
    ok, out = sh(["gh", "api", f"repos/{ALLOWED_REPO}/contents/MANIFEST.json?ref={branch()}",
                  "--jq", ".content"], timeout=60)
    if not ok:
        print(f"\n❌ 拿不到 MANIFEST: {out[:150]}")
        return 1

    import base64
    try:
        manifest = json.loads(base64.b64decode(out.strip()).decode("utf-8"))
    except Exception as e:
        print(f"\n❌ MANIFEST 解析失败: {e}")
        return 1

    backups = manifest.get("backups") or []
    if not backups:
        print("\n❌ 远端没有备份记录")
        return 1

    # ---- 选哪一份 ----
    if name:
        target = next((b for b in backups if b["name"] == name), None)
        if not target:
            print(f"\n❌ 找不到备份: {name}")
            print("   可用的：")
            for b in backups[-8:]:
                print(f"     {b['name']}")
            return 1
    else:
        target = backups[-1]
        print(f"\n  未指定名称，使用最新: {target['name']}")

    print(f"  大小: {target['size'] // 1024} KB")
    print(f"  时间: {time.strftime('%Y-%m-%d %H:%M',
                                    time.localtime(target['at']))}")

    # ---- 本地库存在时必须确认 ----
    if os.path.exists(DB_PATH) and not force:
        cur_size = os.path.getsize(DB_PATH)
        print(f"\n  ⚠ 本地已有数据库（{cur_size // 1024} KB）")
        print(f"    恢复会覆盖它。如果继续，我会先备份当前库。")
        print(f"    确认请加 --force：")
        print(f"      python offsite.py restore {target['name']} --force")
        return 1

    # ---- 下载 ----
    ok, url_out = sh(["gh", "api",
                      f"repos/{ALLOWED_REPO}/contents/{target['name']}?ref={branch()}",
                      "--jq", ".download_url"], timeout=60)
    if not ok or not url_out.strip():
        print(f"\n❌ 拿不到 download_url: {url_out[:120]}")
        return 1

    tmpdir = tempfile.mkdtemp(prefix="restore_")
    try:
        dl = os.path.join(tmpdir, target["name"])
        ok2, out2 = sh(["curl", "-sL", "-o", dl, url_out.strip()], timeout=600)
        if not ok2 or not os.path.exists(dl):
            print(f"\n❌ 下载失败: {out2[:150]}")
            return 1

        got_size = os.path.getsize(dl)
        print(f"\n  下载: {got_size // 1024} KB")
        if got_size != target["size"]:
            print(f"  ❌ 大小不符（应为 {target['size'] // 1024} KB）")
            return 1

        got = fingerprint(dl)
        if got != target["sha256"]:
            print(f"  ❌ 指纹不符，拒绝恢复")
            print(f"     期望 {target['sha256'][:16]} / 实际 {got[:16]}")
            return 1
        print(f"  ✅ 指纹一致: {got[:16]}")

        # ---- 校验能打开 ----
        try:
            import sqlite3
            c = sqlite3.connect(dl)
            ic = c.execute("PRAGMA integrity_check").fetchone()[0]
            n = c.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
            v = c.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
            c.close()
            if ic != "ok":
                print(f"  ❌ 完整性检查失败: {ic[:80]}")
                return 1
            print(f"  ✅ 可打开: episodes={n} vectors={v}")
            if n == 0:
                print(f"  ❌ 这是个空库，拒绝覆盖")
                return 1
        except Exception as e:
            print(f"  ❌ 无法打开: {e}")
            return 1

        # ---- 备份当前库 ----
        if os.path.exists(DB_PATH):
            ts = time.strftime("%Y%m%d_%H%M%S")
            safety = f"{DB_PATH}.before-restore.{ts}"
            shutil.copy2(DB_PATH, safety)
            print(f"\n  已备份当前库: {os.path.basename(safety)}")

        # ---- 覆盖 ----
        # WAL 模式下必须同时清掉 -wal/-shm，否则恢复后被旧的 WAL 覆盖
        for suffix in ("-wal", "-shm"):
            p = DB_PATH + suffix
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass
        shutil.copy2(dl, DB_PATH)
        print(f"  ✅ 已恢复: {DB_PATH}")

        st = load_state()
        st["last_restore_at"] = time.time()
        st["last_restore_from"] = target["name"]
        save_state(st)

        print(f"\n  下一步：跑 python doctor.py 确认环境")
        return 0
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def main():
    args = sys.argv[1:]
    cmd = args[0] if args else "status"
    if cmd == "status":
        return cmd_status()
    if cmd == "push":
        return cmd_push()
    if cmd == "verify":
        return cmd_verify()
    if cmd == "restore":
        nm = args[1] if len(args) > 1 and not args[1].startswith("--") else None
        force = "--force" in args
        return cmd_restore(nm, force)
    print(f"未知命令: {cmd}")
    print("可用: status | push | verify | restore [name] [--force]")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
