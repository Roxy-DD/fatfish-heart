"""敏感信息生命周期（FISH-27）

════════════════════════════════════════════════════════════
要解决什么
════════════════════════════════════════════════════════════
E-537 审计发现：库里有条过期 itsaplan API key 记忆仍处有效状态，
从未被检索过。这类数据的问题不是"占空间"，是：

· 过期的 key 被注入上下文 → 模型用错凭据，浪费轮次
· 被复述出来 → 隐私风险
· 看起来和有效信息一样 → 模型无法分辨

════════════════════════════════════════════════════════════
设计（遵守 R2/R4）
════════════════════════════════════════════════════════════
不删除、不改 episodes 表结构：
· 过期时间存在 meta.sensitive_expiry（JSON: {ep_id: {expires_at, kind, note}}）
· 检索时给 Episode 动态挂 _expired / _expiry_note 属性
· 插件渲染时追加「⚠ 可能已失效」提示

这样：
· 数据完全没动（红线安全）
· 模型能看到"这条可能过期了"
· 随时可撤销

════════════════════════════════════════════════════════════
用法
════════════════════════════════════════════════════════════
    python sensitive_lifecycle.py scan        # 扫描疑似敏感信息
    python sensitive_lifecycle.py mark <ep_id> --days 90 --kind api_key
    python sensitive_lifecycle.py list        # 看已标记的
    python sensitive_lifecycle.py check       # 看哪些已过期
    python sensitive_lifecycle.py undo <ep_id>
"""
import json
import os
import re
import sqlite3
import sys
import time

# 用模块自身位置定位数据库，避免从不同目录运行时找不到
_HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(_HERE, "dsh_mem.db")
KEY = "sensitive_expiry"

# 敏感信息识别模式（保守：只识别明确的高风险模式）
# ★ 收紧后的模式（E-538 实测调优）
#
# 第一版过于宽松，8 条里 5 条是误报：
#   · "重要性" / "触发与遗忘机制" 这类哲学文本被 password 模式命中
#   · 含 "key" 字样的设计讨论被 api_key 模式命中
#
# 判据：必须出现「真实的凭据形态」，不能只靠关键词。
#   · api_key: 已知前缀 + 足够长度的随机串
#   · password: 必须紧跟「是/为/:」+ 看起来像真实密码的串
#   · 纯讨论性文本（如"密码学"、"token 开销"）不该命中
PATTERNS = [
    ("api_key", re.compile(
        r"\b(itp_|sk-|ghp_|gho_|github_pat_|AKIA)[A-Za-z0-9_\-]{20,}")),
    ("password", re.compile(
        r"(?:密码|口令)(?:是|为|:|：)\s*([A-Za-z0-9!@#$%^&*_\-]{6,})")),
    ("token", re.compile(
        r"\b(?:bearer\s+)([A-Za-z0-9_\-\.]{20,})", re.I)),
]


def get_conn():
    return sqlite3.connect(DB)


def load_marks(con):
    row = con.execute("SELECT val FROM meta WHERE key=?", (KEY,)).fetchone()
    if not row:
        return {}
    try:
        return json.loads(row[0])
    except Exception:
        return {}


def save_marks(con, marks):
    with con:
        con.execute("INSERT OR REPLACE INTO meta(key,val) VALUES(?,?)",
                    (KEY, json.dumps(marks, ensure_ascii=False)))


def cmd_scan():
    """扫描库里疑似敏感且未失效的记忆"""
    con = get_conn()
    marks = load_marks(con)
    now = time.time()

    rows = con.execute(
        "SELECT ep_id, raw_text, created_at, invalid_at FROM episodes "
        "WHERE invalid_at IS NULL"
    ).fetchall()

    print("=" * 70)
    print("敏感信息扫描（只看未失效的）")
    print("=" * 70)

    found = []
    for eid, text, created, _ in rows:
        for kind, pat in PATTERNS:
            if pat.search(text or ""):
                found.append((eid, kind, text, created))
                break

    print(f"\n共扫到 {len(found)} 条疑似敏感记忆：\n")
    for eid, kind, text, created in found:
        mark = marks.get(eid)
        status = ""
        if mark:
            exp = mark.get("expires_at", 0)
            status = " [已标记 已过期]" if exp and exp < now else " [已标记 未过期]"
        age_days = (now - created) / 86400 if created else 0
        print(f"  {eid[:12]}  {kind:9s}  {age_days:5.0f}天前{status}")
        # 脱敏显示
        t = text[:70].replace("\n", " ")
        if kind == "api_key":
            t = re.sub(r"(itp_|sk-|ghp_)[A-Za-z0-9_\-]{8,}",
                       r"\1<REDACTED>", t)
        print(f"      {t}")

    con.close()
    return 0


def cmd_mark(eid, days, kind):
    con = get_conn()
    marks = load_marks(con)
    row = con.execute(
        "SELECT raw_text, created_at FROM episodes WHERE ep_id=?", (eid,)
    ).fetchone()
    if not row:
        print(f"❌ 找不到 {eid}")
        con.close()
        return 1
    expires = time.time() + days * 86400
    marks[eid] = {
        "expires_at": expires,
        "kind": kind,
        "marked_at": time.time(),
        "days": days,
    }
    save_marks(con, marks)
    con.close()
    print(f"✅ 已标记 {eid[:12]}  类型={kind}  {days}天后过期")
    print(f"   过期时间: {time.strftime('%Y-%m-%d', time.localtime(expires))}")
    return 0


def cmd_list():
    con = get_conn()
    marks = load_marks(con)
    now = time.time()
    print("=" * 70)
    print(f"已标记的敏感信息（{len(marks)} 条）")
    print("=" * 70)
    if not marks:
        print("\n  还没有标记")
    for eid, m in sorted(marks.items(),
                         key=lambda x: x[1].get("expires_at", 0)):
        exp = m.get("expires_at", 0)
        state = "❌ 已过期" if exp < now else "✅ 有效"
        days_left = (exp - now) / 86400
        print(f"\n  {eid[:12]}  {m.get('kind','?'):9s} {state}")
        print(f"     过期: {time.strftime('%Y-%m-%d', time.localtime(exp))}"
              f"  (还有 {days_left:.0f} 天)")
    con.close()
    return 0


def cmd_check():
    """供插件/回归调用：返回已过期的 ep_id 列表"""
    con = get_conn()
    marks = load_marks(con)
    now = time.time()
    expired = {eid: m for eid, m in marks.items()
               if m.get("expires_at", 0) < now}
    print(f"已过期 {len(expired)} / 已标记 {len(marks)}")
    for eid, m in expired.items():
        print(f"  ⚠ {eid[:12]}  {m.get('kind','?')}  "
              f"过期于 {time.strftime('%Y-%m-%d', time.localtime(m['expires_at']))}")
    con.close()
    return 0


def cmd_undo(eid):
    con = get_conn()
    marks = load_marks(con)
    if eid not in marks:
        print(f"  未标记 {eid[:12]}")
        con.close()
        return 0
    del marks[eid]
    save_marks(con, marks)
    con.close()
    print(f"✅ 已撤销 {eid[:12]} 的标记")
    return 0


def apply_expiry_flags(con, episodes):
    """给检索结果挂过期标记（供 store 调用）

    episodes: Episode 对象列表
    返回：标记为过期的数量
    """
    marks = load_marks(con)
    if not marks:
        return 0
    now = time.time()
    n = 0
    for ep in episodes:
        m = marks.get(getattr(ep, "ep_id", ""))
        if not m:
            continue
        exp = m.get("expires_at", 0)
        if exp and exp < now:
            try:
                ep._expired = True
                ep._expiry_note = (
                    f"疑似过期（{m.get('kind','敏感信息')}，"
                    f"标记于 {time.strftime('%Y-%m-%d', time.localtime(m.get('marked_at', now)))}）"
                )
                n += 1
            except Exception:
                pass
    return n


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    cmd = sys.argv[1]
    if cmd == "scan":
        return cmd_scan()
    if cmd == "list":
        return cmd_list()
    if cmd == "check":
        return cmd_check()
    if cmd == "mark":
        if len(sys.argv) < 3:
            print("用法: mark <ep_id> [--days N] [--kind K]")
            return 1
        eid = sys.argv[2]
        days = 90
        kind = "sensitive"
        if "--days" in sys.argv:
            days = int(sys.argv[sys.argv.index("--days") + 1])
        if "--kind" in sys.argv:
            kind = sys.argv[sys.argv.index("--kind") + 1]
        return cmd_mark(eid, days, kind)
    if cmd == "undo":
        if len(sys.argv) < 3:
            print("用法: undo <ep_id>")
            return 1
        return cmd_undo(sys.argv[2])
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
