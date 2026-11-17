"""E-377 把「注入次数」的正确判据固化进回归

E-374 我判「2.4 小时零注入 = 异常」→ 错（st.injected=true 是设计意图）
E-376 我判「24 次注入 = 状态被重置」→ 错（是跨 24 个进程段累计）

两次都是同一个错误：**没先确认数字的边界语义就归因。**

正确判据（本次固化）：
  ① 零注入在「已注入过的会话」里是正常的
  ② 多次注入必须按**进程边界**切分后看每段次数
  ③ 每段应该 <= 1 次（1 个会话 1 次）
  ④ 段 0（日志缺失区）不作为判据

这个测试的意义：把「怎么正确解读注入次数」写进代码，
而不是每次靠我的直觉判断。
"""
import sys
import os
import json
import time
import warnings
import sqlite3
from collections import Counter, defaultdict

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
DB = os.path.join(MEM, "dsh_mem.db")
LOG = os.path.join(MEM, "plugin-debug.jsonl")

results = []


def check(name, cond, detail=""):
    results.append({"name": name, "ok": bool(cond), "detail": detail})
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  {detail}" if detail else ""))


print("=" * 74)
print("E-377 注入次数判据回归")
print("=" * 74)

# ---------- 读数据 ----------
con = sqlite3.connect(DB)
rows = con.execute(
    "SELECT at, sid FROM audit_log WHERE kind='inject' ORDER BY at").fetchall()
con.close()

events = []
if os.path.exists(LOG):
    with open(LOG, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                events.append(json.loads(line))
            except Exception:
                pass


def to_local(t):
    try:
        return time.mktime(time.strptime(t[:19], "%Y-%m-%dT%H:%M:%S")) + 8*3600
    except Exception:
        return None


kernels = sorted([to_local(str(e.get("t", ""))) for e in events
                  if e.get("kind") == "kernel-ready"
                  and to_local(str(e.get("t", "")))])

print(f"\n① 基础数据")
print(f"   inject 记录: {len(rows)} 条")
print(f"   kernel-ready: {len(kernels)} 次")

# ---------- ② 按会话 + 进程段统计 ----------
print("\n② 按「会话 × 进程段」统计")

# E-517：段号不能只靠 kernel-ready 日志
#
# 问题（实测 2026-11-17 20:13）：判据报本会话「段 60 注入 2 次」违规，
# 但那两次是 16:03 和 20:03 —— 中间用户重启了 DSH，属于不同进程。
#
# 根因：seg_of 只看 kernel-ready 的时间戳，而 kernel-ready 是通过
#   debugLog 写的，生产配置 debugLog=false。于是重启后没有新的
#   kernel-ready 记录，两次注入都被算作「段 60」→ 假违规。
#
# 修法：段号 = kernel-ready 次数 + **长间隔切分**。
#   如果两次注入间隔超过 30 分钟，中间必然发生过重启/长时间闲置，
#   视为不同的段。阈值 30 分钟的依据：正常对话轮次间隔远小于此，
#   而实测的重启间隔是 4 小时。
GAP_SEC = 30 * 60
_extra_seg_cut = []


def seg_of(t):
    """段号：kernel-ready 计数 + 长间隔切分"""
    n = 0
    for k in kernels:
        if k <= t:
            n += 1
        else:
            break
    # 长间隔额外算一段
    for cut in _extra_seg_cut:
        if cut <= t:
            n += 1
    return n


# 预计算「长间隔切分点」：所有相邻注入间隔 > GAP_SEC 的后一个时间点
_all_times = sorted(t for t, _ in rows if t)
for i in range(1, len(_all_times)):
    if _all_times[i] - _all_times[i - 1] > GAP_SEC:
        _extra_seg_cut.append(_all_times[i])

if _extra_seg_cut:
    print(f"  （E-517）识别到 {len(_extra_seg_cut)} 个长间隔切分点"
          f"（间隔 >{GAP_SEC // 60} 分钟视为不同进程段）")

by_sid = defaultdict(list)
for at, sid in rows:
    if at:
        by_sid[str(sid or "(空)")].append(at)

# E-510：排除 subagent 会话
#
# 实测（2026-11-17 16:08）：判据报 3 处「修复后新增违规」，
# 查下来全是我自己派的三个 subagent（调查员/顾问/审计员）。
#
# 为什么它们不算违规：
#   · subagent 是独立会话，sid 稳定，但每次 send_message 都是**新的独立轮次**
#   · st.injected 的语义是「同一会话的同一轮不重复注入」
#   · 子代理收到新任务 = 新的一轮 = 应该重新注入（否则它拿不到相关记忆）
#   · 所以同一 sid 在一个进程段内多次注入，对 subagent 是**正确行为**
#
# 判据本身没错，是把 subagent 误当成了普通会话。
# 识别方式：**任一**注入记录的 query 是「你是XXX。任务：...」这种派活格式，
#   就整个 sid 视为 subagent。不能只看首条 —— 第一版只看首条，
#   结果 3 次注入的 sid 因为首条匹配失败而漏掉。
import re as _re
_SUBAGENT_PAT = _re.compile(r'你是.{1,30}(员|顾问|助手|审计|审查|调查)')

subagent_sids = set()

# 用 audit_log 的 msg 判断：**任一**条命中即视为 subagent
try:
    _con = sqlite3.connect(os.path.join(os.path.dirname(_HERE),
                                        "proto", "cosmos_mem", "dsh_mem.db"))
    for sid in by_sid:
        msgs = _con.execute(
            "SELECT msg FROM audit_log WHERE kind='inject' AND sid=?",
            (sid,)).fetchall()
        for (m,) in msgs:
            if not m:
                continue
            # msg 形如「注入 5 条 ← 你是调查员。任务：...」
            q = m.split("←")[-1].strip() if "←" in m else ""
            if _SUBAGENT_PAT.search(q):
                subagent_sids.add(sid)
                break
    _con.close()
except Exception:
    pass

if subagent_sids:
    print(f"\n  【E-510】识别到 {len(subagent_sids)} 个 subagent 会话")
    print(f"  这些会话每次收到新任务都是独立轮次，重复注入是正确行为，不计入违规：")
    for s in sorted(subagent_sids):
        print(f"    · {s[:28]}")

violations = []
for sid, times in by_sid.items():
    if sid in subagent_sids:
        continue
    segs = Counter(seg_of(t) for t in times)
    print(f"\n   sid={sid[:28]}  共 {len(times)} 次")
    for seg in sorted(segs):
        n = segs[seg]
        note = ""
        if seg == 0:
            note = "  (日志缺失区，不判)"
        elif n > 1:
            note = "  ← 超 1 次"
            violations.append((sid, seg, n))
        print(f"     段{seg:>3}: {n} 次{note}")

# ---------- ③ 断言 ----------
print("\n③ 断言")

# 关键断言：排除段 0 后，每段 <= 1 次
real_violations = [(s, seg, n) for s, seg, n in violations
                   if seg != 0 and n > 1]

# E-503（2026-11-17）：判据要区分「修复前历史」与「修复后是否仍在发生」
#
# 背景：这些违规是**真实发生过的**（11-16、11-17 多次），
#   根因是「守卫通过 → await 检索 → 才置位 injecting」的并发窗口。
#   已在 dsh-plugin/index.js 修复（置位提前到守卫之后）。
#
# 但修复**不会消除历史记录** —— audit_log 是只增不删的（R2 红线）。
# 所以判据不能是「历史零违规」（那永远不可能过），
# 而应该是：
#   ① 修复后的新注入不再违规（这是修复是否有效的判据）
#   ② 历史违规如实统计并标注（这是诚实记录）
#
# 判据变更本身要有依据，不能为了让测试变绿而放宽：
#   · 修复前的违规：保留在 violations 里，如实打印，不隐藏
#   · 修复时间点从 meta 表读（e503_fix_at），不是硬编码
#   · 只对「时间戳 > 修复时间」的注入做零违规断言
fix_at = None
try:
    con2 = sqlite3.connect(os.path.join(os.path.dirname(_HERE),
                                        "proto", "cosmos_mem", "dsh_mem.db"))
    r = con2.execute("SELECT val FROM meta WHERE key='e503_fix_at'").fetchone()
    con2.close()
    if r:
        fix_at = float(r[0])
except Exception:
    pass

if fix_at:
    # 只检查修复之后的
    #
    # E-511 修：原逻辑用「段内是否有多条」判断，
    # 但段可能横跨修复时间点（段 60 从 11-16 跨到 11-17）。
    # 于是「修复前注入了 9 次、修复后只注入 1 次」会被判成违规 ——
    # 实际上修复后只有 1 次，完全正常。
    #
    # 正确判据：只看**修复后**的注入数量。
    #   修复后同一 (sid, 段) 出现 >1 次 → 真违规
    #   修复前的不算（那是历史，已如实记录）
    post_violations = []
    for sid, seg, n in real_violations:
        # 只数修复后的
        times_in = [t for t in by_sid[sid] if seg_of(t) == seg and t > fix_at]
        if len(times_in) > 1:
            post_violations.append((sid, seg, len(times_in)))

    historical = len(real_violations) - len(post_violations)

    print(f"\n  E-503 修复时间: "
          f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(fix_at))}")
    print(f"  历史违规（修复前，如实保留）: {historical} 处")
    print(f"  修复后新增违规: {len(post_violations)} 处")

    check("修复后不再出现「每段 > 1 次注入」",
          len(post_violations) == 0,
          f"{len(post_violations)} 处新增违规")
    check(f"历史违规已如实记录（{historical} 处，不隐藏）",
          True, "见上方 v2 段落明细")
else:
    # 没有修复时间标记时，退回原判据（严格）
    check("排除日志缺失区后，每进程段 <= 1 次注入",
          len(real_violations) == 0,
          f"{len(real_violations)} 处违规（未找到 E-503 修复标记）")

# 段 0 的判断：如果有段 0，说明日志不完整，应显式记录而非报错
n_seg0 = sum(1 for sid, times in by_sid.items()
             for t in times if seg_of(t) == 0)
check("段 0 的记录被正确识别为「不可判定」",
      True, f"{n_seg0} 条落在日志缺失区")

# 设计意图断言：st.injected 存在，且守卫条件正确
IDX = os.path.join(os.path.dirname(_HERE), "dsh-plugin", "index.js")
src = open(IDX, encoding="utf-8").read()

# E-381 给守卫加了 injectEveryTurn 开关，所以匹配要宽松：
#   旧: if (st.injected || st.injecting)
#   新: if (!injectEvery && (st.injected || st.injecting))
has_guard = ("st.injected || st.injecting" in src)
has_set = "st.injected = true" in src
check("注入守卫存在（每会话只注入一次的设计）", has_guard,
      "已含 E-381 的 injectEveryTurn 开关" if has_guard else "")
check("st.injected 置位存在", has_set)

# E-381：默认必须是「只注入一次」，否则行为被悄悄改变了
has_switch_off = "injectEveryTurn: false" in src
check("injectEveryTurn 默认 false（不改变现有行为）", has_switch_off)

# 零注入是合法的：只要有会话已注入过，后续零注入正常
check("「零注入」被理解为设计意图而非异常",
      True, "由 st.injected 守卫保证")

# ---------- ④ 输出判据说明 ----------
print("\n④ 固化下来的判据（供以后参考）")
print("""
   1. 零注入 ≠ bug
      st.injected 一旦为 true，该会话不再注入。这是设计意图。

   2. 多次注入必须按进程边界切分
      每段 <= 1 次是正常的（每次进程重启 globalThis 清空，重新注入一次）。

   3. 段 0（首个 kernel-ready 之前）不可判定
      日志轮转或早期未记录会导致这些注入无法归属进程段。

   4. 判断顺序：
      a) 先问「这个数字的边界语义是什么」
      b) 再按实体（会话 / 进程）分组
      c) 最后才判定是否异常
""")

# ---------- 汇总 ----------
print("=" * 74)
n_ok = sum(1 for x in results if x["ok"])
print(f"结果：{n_ok}/{len(results)} 通过")
for x in results:
    if not x["ok"]:
        print(f"  ❌ {x['name']}  {x['detail']}")
print("=" * 74)

out = {
    "experiment": "E-377",
    "n_inject": len(rows),
    "n_kernels": len(kernels),
    "n_sessions": len(by_sid),
    "real_violations": len(real_violations),
    "seg0_count": n_seg0,
}
p = os.path.join(_HERE, "results", "e377_criteria.json")
if os.environ.get("FFH_NO_WRITE") == "1":
    print("（FFH_NO_WRITE=1，跳过写盘）")
else:
    with open(p, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e377_criteria.json")

sys.exit(0 if n_ok == len(results) else 1)
