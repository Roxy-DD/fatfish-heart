"""E-342 按进程边界分段：24 次注入里，每个进程生命周期内注入了几次？

E-341 拿到「同一 sid 注入 24 次」，但这个数字不能直接证明设计失效。
必须先排除三个混淆因素：

  ① 时间跨度 2805.9 分钟 ≈ 46.8 小时（跨了整夜）
  ② 相邻间隔里有 131396 秒 ≈ 36.5 小时
  ③ Web GUI 端口从 61638 变成 52160 → DSH 重启过

进程一重启，globalThis 必然清空，st.injected 归 false，
重新注入一次是**设计预期行为**，不是 bug。

判定标准：
  · 若每个进程段内 ≈1 次注入  → 设计成立，不动代码
  · 若某进程段内仍多次注入   → 真 bug，继续定位

进程边界怎么识别：
  · selfreload 记录（自己触发的重载）
  · 时间间隔 > 10 分钟（隔夜/空闲后重启）
  · 端口变更（21:xx 之后从 61638 → 52160）
"""
import sys
import os
import json
import time
import warnings
import sqlite3

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
DB = os.path.join(MEM, "dsh_mem.db")
LOG = os.path.join(MEM, "plugin-debug.jsonl")

print("=" * 78)
print("E-342 按进程边界分段统计注入次数")
print("=" * 78)

con = sqlite3.connect(DB)
injects = con.execute(
    "SELECT seq, at, sid, msg FROM audit_log "
    "WHERE kind='inject' ORDER BY at").fetchall()
con.close()

print(f"\n① inject 总条数: {len(injects)}")
if not injects:
    print("   无数据")
    raise SystemExit(0)

# ---------- ② 收集进程边界信号 ----------
print("\n② 收集进程边界信号")

# 2a. selfreload 记录
reloads = []
if os.path.exists(LOG):
    with open(LOG, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                e = json.loads(line)
                if e.get("kind") in ("selfreload-ok", "selfreload-fail"):
                    t = e.get("t")
                    if t:
                        # ISO 转 epoch
                        ts = time.mktime(time.strptime(
                            t[:19], "%Y-%m-%dT%H:%M:%S"))
                        # 日志是 UTC，转本地
                        ts -= time.timezone if not time.daylight else 0
                        reloads.append(ts)
            except Exception:
                pass

print(f"   selfreload 记录: {len(reloads)} 条")

# 2b. 用时间间隔找断点（> 10 分钟视为新进程段）
GAP_SEC = 600
segments = []
cur = [injects[0]]
gaps = []
for i in range(1, len(injects)):
    prev_t = injects[i-1][1]
    cur_t = injects[i][1]
    gap = cur_t - prev_t
    if gap > GAP_SEC:
        gaps.append((prev_t, cur_t, gap))
        segments.append(cur)
        cur = [injects[i]]
    else:
        cur.append(injects[i])
segments.append(cur)

print(f"\n③ 按 >{GAP_SEC}s 间隔切分: {len(segments)} 段")
print(f"   断点 {len(gaps)} 个:")
for a, b, g in gaps:
    print(f"     {time.strftime('%m-%d %H:%M', time.localtime(a))} → "
          f"{time.strftime('%m-%d %H:%M', time.localtime(b))}  "
          f"间隔 {g/60:.0f} 分钟")

# ---------- ④ 每段统计 ----------
print("\n④ 每段注入次数")
print(f"   {'段':>3} {'开始':<16} {'结束':<16} {'次数':>4} {'最长间隔':>10}")
print("   " + "-" * 62)

verdict_rows = []
for i, seg in enumerate(segments, 1):
    t0 = seg[0][1]
    t1 = seg[-1][1]
    n = len(seg)
    gaps_in = [seg[j][1] - seg[j-1][1] for j in range(1, n)]
    max_gap = max(gaps_in) if gaps_in else 0
    print(f"   {i:>3} {time.strftime('%m-%d %H:%M', time.localtime(t0)):<16} "
          f"{time.strftime('%m-%d %H:%M', time.localtime(t1)):<16} "
          f"{n:>4} {max_gap/60:>9.1f}m")
    verdict_rows.append({"seg": i, "n": n, "t0": t0, "t1": t1,
                         "max_gap": max_gap})

# ---------- ⑤ 判定 ----------
print("\n⑤ 判定")

multi = [r for r in verdict_rows if r["n"] > 1]
single = [r for r in verdict_rows if r["n"] == 1]

print(f"   单次注入的段: {len(single)}")
print(f"   多次注入的段: {len(multi)}")

if multi:
    print("\n   多次注入的段详情:")
    for r in multi:
        span = (r["t1"] - r["t0"]) / 60
        print(f"     段{r['seg']}: {r['n']} 次, 跨度 {span:.0f} 分钟, "
              f"最长内部间隔 {r['max_gap']/60:.1f} 分钟")

    # 关键判据：段内是否有 selfreload 或端口变更
    print("\n   分析段内多次注入的原因:")
    for r in multi:
        n_reload_in_seg = sum(1 for x in reloads if r["t0"] <= x <= r["t1"])
        print(f"     段{r['seg']}: {r['n']} 次注入, "
              f"段内 selfreload {n_reload_in_seg} 次")
        if n_reload_in_seg >= r["n"] - 1:
            print(f"       → 由 selfreload 解释，属预期行为")
        elif r["n"] > 1 and n_reload_in_seg == 0:
            print(f"       → ⚠ 无 selfreload 仍多次注入，可能是真 bug")

# ---------- ⑥ 结论 ----------
print("\n" + "=" * 78)
print("结论")
print("=" * 78)

# 判定：段内注入次数是否 <= 1 + 段内 reload 次数
all_explained = True
for r in multi:
    n_reload_in_seg = sum(1 for x in reloads if r["t0"] <= x <= r["t1"])
    if r["n"] > 1 + n_reload_in_seg:
        all_explained = False
        break

if not multi:
    verdict = "design_ok"
    print("""
  ✅ 每个进程段内只注入 1 次

  「只注入一次」的设计成立。E-341 的 24 次是
  跨 46.8 小时、跨多次进程重启的历史累计，不是设计失效。

  → 不改代码。
""")
elif all_explained:
    verdict = "explained_by_reload"
    print(f"""
  ✅ 段内多次注入可由 selfreload 解释（预期行为）

  段内注入次数 <= 1 + 段内 reload 次数，
  说明每次进程重启/重载后重新注入一次，符合设计。
  段间大间隔（>10min）则是 DSH 进程重启。

  → 不改代码。E-341 的「24 次」是历史累计，不是 bug。
""")
else:
    verdict = "real_bug"
    print("""
  ❌ 存在无法解释的多次注入

  某段内注入次数 > 1 + 段内 reload 次数，
  说明 st.injected 在有重载之外的原因被重置。

  → 需要继续查：agent/disposed 是否误删 sessions、
     sessState 是否被重建。
""")

out = {
    "experiment": "E-342",
    "inject_total": len(injects),
    "n_segments": len(segments),
    "segments": [{"seg": r["seg"], "n": r["n"],
                  "span_min": round((r["t1"]-r["t0"])/60, 1)}
                 for r in verdict_rows],
    "reloads": len(reloads),
    "verdict": verdict,
}
p = os.path.join(_HERE, "results", "e342_segments.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e342_segments.json")
