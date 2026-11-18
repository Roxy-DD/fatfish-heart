"""watch_itsaplan.py —— itsaplan 中间层：把平台上的动静转成我的行动

用户 2026-11-18 明确要求：
「那个平台上面的所有通知都应该能够让你们看到，并且提示你要来处理。
  是一整个中间层啊，就是连接这个平台与你进行自动消息对话的过程」

════════════════════════════════════════════════════════════
四种通知类型与处理策略
════════════════════════════════════════════════════════════
| 类型          | 含义                     | 处理                |
|---------------|--------------------------|---------------------|
| mentioned     | 用户 @我                 | 最高优先，必响      |
| assigned      | 用户派任务给我           | 高优先，必响        |
| commented     | 用户评论（不一定是给我） | 中优先，看内容      |
| state_changed | 状态变更（用户自己动的） | 仅记录，不响应      |

state_changed 不响应：用户自己把任务拖到 Done，不需要我回应，
全响应会刷屏且浪费 token。若同一 issue 有更高级别通知则按高级别处理。

════════════════════════════════════════════════════════════
关键设计
════════════════════════════════════════════════════════════
1. 同一 issue 的多条通知合并成一次处理
   （FISH-25 同时有 mentioned + assigned，只跑一次 headless）

2. 只响应最近 MAX_AGE_HOURS 小时内的通知
   首次启动时历史积压不刷屏

3. headless 没有 MCP 插件，但有 pwsh 工具
   所以让它用 `python itsaplan_call.py <工具> <JSON>` 访问 itaplan

用法：
  python watch_itsaplan.py            # 循环
  python watch_itsaplan.py --once     # 一次
  python watch_itsaplan.py --dry-run  # 只看不动
"""
import argparse
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(HERE, "watch_state.json")
LOG_PATH = os.path.join(HERE, "watch.log")

MCP_URL = "http://127.0.0.1:3000/mcp"
# 我（大肥鱼 dsh@qq.com）的 API key，2027-11 过期
# 存在记忆和这里两处；轮换要两边都改
API_KEY = os.environ.get("ITSAPLAN_KEY") or (
    "itp_tbHAEckPfENHVGJefyICYzCiEvwPXwKoFTKmPXSoUEqLJziJUVxaZdblYLBpGzTC"
)

POLL_ACTIVE = 15
POLL_IDLE_MIN = 30
POLL_IDLE_MAX = 120
MAX_AGE_HOURS = 6

PRIORITY = {"mentioned": 0, "assigned": 1, "commented": 2, "state_changed": 3}


def log(msg, level="INFO"):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [{level}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def find_dsh():
    """Windows 上 dsh 是 .cmd，subprocess 需要完整路径"""
    for name in ("dsh.cmd", "dsh.exe", "dsh"):
        p = shutil.which(name)
        if p:
            return p
    fallback = os.path.expandvars(
        r"%LOCALAPPDATA%\Programs\DeepSeek Harness\resources\runtime\cli\bin\dsh.cmd"
    )
    return fallback if os.path.exists(fallback) else None


# ════════════════════════════════════════════════════════════
# MCP
# ════════════════════════════════════════════════════════════
def mcp_call(tool, arguments, timeout=30):
    body = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }).encode("utf-8")

    req = urllib.request.Request(
        MCP_URL, data=body, method="POST",
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return {"_error": f"HTTP {e.code}"}
    except Exception as e:
        return {"_error": f"{type(e).__name__}: {e}"}

    payload = None
    for line in raw.split("\n"):
        if line.startswith("data: "):
            try:
                payload = json.loads(line[6:])
            except Exception:
                continue
            break
    if payload is None:
        try:
            payload = json.loads(raw)
        except Exception:
            return {"_error": f"解析失败: {raw[:150]}"}

    if "error" in payload:
        return {"_error": str(payload["error"])[:250]}

    try:
        c = payload["result"]["content"]
        if isinstance(c, list) and c:
            txt = c[0].get("text", "")
            return json.loads(txt) if txt else {}
    except Exception:
        pass
    return payload.get("result", {})


def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            st = json.load(f)
        st.setdefault("handled_notifications", {})
        return st
    except Exception:
        return {"handled_notifications": {}}


def save_state(st):
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=1)
    except Exception as e:
        log(f"状态保存失败: {e}", "WARN")


def notif_time(n):
    s = n.get("createdAt")
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


# ════════════════════════════════════════════════════════════
# 核心
# ════════════════════════════════════════════════════════════
def check_once(st, dry_run=False):
    res = mcp_call("list_notifications", {"includeRead": "false", "limit": "30"})
    if "_error" in res:
        log(f"查通知失败: {res['_error']}", "ERROR")
        return 0

    items = res.get("items") or []
    if not items:
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)

    fresh = []
    stale = 0
    for n in items:
        nid = str(n.get("id"))
        if nid in st["handled_notifications"]:
            continue
        t = notif_time(n)
        if t is not None and t < cutoff:
            st["handled_notifications"][nid] = time.time()
            stale += 1
            continue
        fresh.append(n)

    if stale:
        log(f"跳过 {stale} 条超过 {MAX_AGE_HOURS} 小时的历史通知")
        save_state(st)

    if not fresh:
        return 0

    by_type = {}
    for n in fresh:
        by_type[n.get("type")] = by_type.get(n.get("type"), 0) + 1
    log(f"新通知 {len(fresh)} 条: " + ", ".join(f"{k}×{v}" for k, v in by_type.items()))

    # 按 issue 合并
    by_issue = {}
    for n in fresh:
        by_issue.setdefault(n.get("issueId"), []).append(n)

    handled = 0
    for iid, notifs in by_issue.items():
        top = min((n.get("type") for n in notifs),
                  key=lambda t: PRIORITY.get(t, 9))
        seq = notifs[0].get("issueSeq")
        title = notifs[0].get("issueTitle") or ""

        if top == "state_changed":
            log(f"  FISH-{seq} 只有状态变更，仅记录")
            for n in notifs:
                st["handled_notifications"][str(n.get("id"))] = time.time()
            save_state(st)
            continue

        types = sorted({n.get("type") for n in notifs})
        log(f"→ FISH-{seq} [{'+'.join(types)}] {title[:40]}")

        if dry_run:
            log("  (dry-run，不执行)")
            handled += 1
            continue

        ok = run_headless(iid, seq, title, types, notifs)
        for n in notifs:
            st["handled_notifications"][str(n.get("id"))] = time.time()
        save_state(st)

        if ok:
            handled += 1
            log(f"  ✅ FISH-{seq} 完成")
        else:
            log(f"  ❌ FISH-{seq} 失败", "ERROR")

    return handled


def build_task(issue_id, seq, title, types, notifs):
    """构造给 headless 的任务提示"""
    detail = ""
    for n in notifs:
        body = n.get("body")
        if isinstance(body, str) and body.strip():
            detail += f"- {n.get('type')}: {body[:200]}\n"

    call = f'python "{os.path.join(HERE, "itsaplan_call.py")}"'

    lines = [
        "你是大肥鱼。itsaplan 上有新动静需要你处理。",
        "",
        f"任务: FISH-{seq}「{title}」(issueId={issue_id})",
        f"触发类型: {', '.join(types)}",
    ]
    if detail:
        lines.append("通知内容:")
        lines.append(detail.rstrip())

    lines += [
        "",
        "=== 你访问 itaplan 的方式 ===",
        "你没有 mcp__itsaplan__* 工具，但可以用 pwsh 执行下面这个命令：",
        "",
        f"  {call} <工具名> '<JSON参数>'",
        "",
        "常用：",
        f'  读任务详情:  {call} get_issue \'{{"issueId": {issue_id}}}\'',
        f'  读活动评论:  {call} list_issue_activity \'{{"issueId": {issue_id}, "limit": 10}}\'',
        f'  写评论:      {call} add_comment \'{{"issueId": {issue_id}, "body": "你的回复"}}\'',
        f'  改状态:      {call} update_issue \'{{"issueId": {issue_id}, "columnId": 4}}\'',
        "",
        "=== 你要做的 ===",
        "1. 先用 get_issue 和 list_issue_activity 搞清楚任务是什么",
        "2. 判断需不需要回应：只是历史状态变更就说\"无需处理\"并停",
        "3. 如果需要回应，用 add_comment 写回 itaplan（用户在那等）",
        "4. 任务完成了就用 update_issue 移到 Done (columnId=4)",
        "5. 做完就停，不要空转",
        "",
        "注意：身份是大肥鱼，用中文，回复要具体有信息量。",
    ]
    return "\n".join(lines)


def run_headless(issue_id, seq, title, types, notifs):
    dsh = find_dsh()
    if not dsh:
        log("  找不到 dsh", "ERROR")
        return False

    task = build_task(issue_id, seq, title, types, notifs)

    log("  启动 headless")
    t0 = time.time()
    try:
        proc = subprocess.run(
            [dsh, "--profile", "headless", task],
            capture_output=True, text=True, timeout=600,
            encoding="utf-8", errors="replace",
        )
        elapsed = time.time() - t0
        log(f"  headless 退出={proc.returncode} 耗时={elapsed:.0f}s")
        if proc.stdout:
            log(f"  输出: {proc.stdout.strip()[:400]}")
        if proc.returncode != 0 and proc.stderr:
            log(f"  错误: {proc.stderr.strip()[:300]}", "ERROR")
        return proc.returncode == 0
    except subprocess.TimeoutExpired:
        log("  headless 超时（600s）", "ERROR")
        return False
    except Exception as e:
        log(f"  启动失败: {type(e).__name__}: {e}", "ERROR")
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    log("=" * 60)
    log("itsaplan 中间层启动")
    log(f"监听: {MCP_URL}")
    log(f"模式: {'单次' if args.once else '循环'}{'（dry-run）' if args.dry_run else ''}")
    log("响应: mentioned / assigned / commented（state_changed 仅记录）")
    log("=" * 60)

    st = load_state()

    if args.once:
        n = check_once(st, args.dry_run)
        log(f"本次处理 {n} 件")
        return 0

    idle = POLL_IDLE_MIN
    try:
        while True:
            n = check_once(st, args.dry_run)
            if n > 0:
                idle = POLL_IDLE_MIN
                time.sleep(2)
                continue
            log(f"无新任务，{idle}s 后再查")
            time.sleep(idle)
            idle = min(idle * 2, POLL_IDLE_MAX)
    except KeyboardInterrupt:
        log("收到 Ctrl+C，退出")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
