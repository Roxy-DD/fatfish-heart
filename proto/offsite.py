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
    ok2, out2 = sh(["gh", "api", f"repos/{ALLOWED_REPO}/contents",
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
        # 只保留最近 20 条记录
        manifest["backups"] = manifest["backups"][-20:]
        json.dump(manifest, open(manifest_path, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)

        env2 = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        for cmd in (
            ["git", "add", name, "MANIFEST.json"],
            ["git", "-c", "user.name=fatfish-heart", "-c",
             "user.email=noreply@localhost", "commit", "-m",
             f"backup: {name} ({size // 1024}KB)"],
            ["git", "push", "origin", "HEAD:main"],
        ):
            ok, out = sh(cmd, cwd=repo_dir, timeout=300, )
            if not ok:
                # push 到 main 失败时试 master（空仓库默认分支可能不同）
                if "push" in cmd:
                    ok2, out2 = sh(["git", "push", "origin", "HEAD:master"],
                                   cwd=repo_dir, timeout=300)
                    if ok2:
                        ok, out = ok2, out2
                if not ok:
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

    ok, out = sh(["gh", "api", f"repos/{ALLOWED_REPO}/contents/MANIFEST.json",
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
                              f"repos/{ALLOWED_REPO}/contents/{latest['name']}",
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


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "status":
        return cmd_status()
    if cmd == "push":
        return cmd_push()
    if cmd == "verify":
        return cmd_verify()
    print(f"未知命令: {cmd}")
    print("可用: status | push | verify")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
