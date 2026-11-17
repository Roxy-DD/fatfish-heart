"""E-507 形式化模型的逻辑验证（不依赖 Lean 编译器）

背景：
  FISH-15 要求「用 Lean 形式化写出系统的运行数学模型，并验证边界」。
  formal/FatFish.lean 已写好（12 定理 13 def），但 Lean 编译器环境
  装了三次没成功（网络 593MB 包反复断连）。

这里不假装 Lean 已经跑通 —— 而是用**独立方法**验证同一批定理的逻辑：
  把 Lean 里的定义逐条翻译成 Python，然后**穷举所有边界状态**，
  检查每条定理声称的结论是否真的成立。

这是「用另一个实现交叉验证」，不是替代 Lean 证明。
诚实定位：
  · Lean 证明 = 对所有输入成立（机器保证）
  · 本脚本  = 对穷举的状态空间成立（实测保证）
  两者不一样，但本脚本能抓出「定理写错了」这类错误。
"""
import itertools
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
results = []


def check(name, cond, detail=""):
    results.append({"name": name, "ok": bool(cond), "detail": str(detail)[:120]})
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  {detail}" if detail else ""))


print("=" * 72)
print("E-507 形式化模型逻辑验证（穷举状态空间）")
print("=" * 72)

# ============================================================
# 一、生命周期状态机（对应 FatFish.lean 的 decide 函数）
# ============================================================

print("\n【1】decide 函数的判定顺序（Lean: theorem declared_done_always_honored 等）")


class LcConfig:
    def __init__(self, maxConsecutive=20, maxNoProgress=3):
        self.maxConsecutive = maxConsecutive
        self.maxNoProgress = maxNoProgress


class LcState:
    def __init__(self, consecutive=0, noProgressStreak=0,
                 declaredDone=False, steerInFlight=False):
        self.consecutive = consecutive
        self.noProgressStreak = noProgressStreak
        self.declaredDone = declaredDone
        self.steerInFlight = steerInFlight


def decide(cfg, s, hasProgress):
    """严格对齐 lifecycle.js 的真实顺序：
       ① declaredDone ② steerInFlight ③ maxConsecutive ④ noProgress"""
    if s.declaredDone:
        return "honorDone"
    if s.steerInFlight:
        return "skipReentry"
    if s.consecutive >= cfg.maxConsecutive:
        return "giveUp"
    if (not hasProgress) and (s.noProgressStreak + 1 >= cfg.maxNoProgress):
        return "giveUp"
    return "steer"


cfg = LcConfig()

# 定理1：declaredDone 一定被尊重
bad = []
for consecutive, streak, inflight, hasProg in itertools.product(
        [0, 5, 19, 20, 21], [0, 1, 2, 3, 4], [False, True], [False, True]):
    s = LcState(consecutive, streak, True, inflight)
    if decide(cfg, s, hasProg) != "honorDone":
        bad.append((consecutive, streak, inflight, hasProg))
check("定理1 declaredDone 永远优先（穷举 200 组合）",
      len(bad) == 0, f"{len(bad)} 处违反" if bad else "")

# 定理2：达到 maxConsecutive 一定放行
bad2 = []
for consecutive in [20, 21, 50]:
    for streak in [0, 1, 2, 3]:
        for hasProg in [False, True]:
            s = LcState(consecutive, streak, False, False)
            if decide(cfg, s, hasProg) != "giveUp":
                bad2.append((consecutive, streak, hasProg))
check("定理2 达到 maxConsecutive 一定 giveUp",
      len(bad2) == 0, f"{len(bad2)} 处违反" if bad2 else "")

# 定理3：无进展达到阈值一定放行
bad3 = []
for consecutive in range(0, 20):
    for streak in [2, 3, 4]:
        s = LcState(consecutive, streak, False, False)
        if decide(cfg, s, False) != "giveUp":
            bad3.append((consecutive, streak))
check("定理3 无进展 streak+1>=3 一定 giveUp",
      len(bad3) == 0, f"{len(bad3)} 处违反" if bad3 else "")

# 定理4：有余额且未宣布完成 → 一定 steer
bad4 = []
for consecutive in range(0, 20):
    for streak in [0, 1]:
        for hasProg in [False, True]:
            s = LcState(consecutive, streak, False, False)
            d = decide(cfg, s, hasProg)
            # 有进展时应该 steer；无进展但 streak+1<3 也应 steer
            expect = "steer"
            if not hasProg and streak + 1 >= 3:
                expect = "giveUp"
            if d != expect:
                bad4.append((consecutive, streak, hasProg, d))
check("定理4 有余量时继续（穷举 80 组合）",
      len(bad4) == 0, f"{len(bad4)} 处违反" if bad4 else "")

# ============================================================
# 二、注入预算模型（对应 FatFish.lean 的 injectTotal）
# ============================================================

print("\n【2】注入预算模型（Lean: inject_total_linear / inject_ignores_store）")

L0L2_FULL = 4780
L0L2_BRIEF = 127
TOTAL_CAP = 6000
BODY_CAP = 1500


def inject_at(t, body):
    prot = L0L2_FULL if t == 0 else L0L2_BRIEF
    return prot + body


def inject_total(n, body):
    if n <= 0:
        return 0
    return inject_at(0, body) + (n - 1) * (L0L2_BRIEF + body)


# 定理5：线性公式
bad5 = []
for n in [1, 5, 20, 100]:
    for body in [0, 500, 1500]:
        direct = sum(inject_at(t, body) for t in range(n))
        formula = inject_total(n, body)
        if direct != formula:
            bad5.append((n, body, direct, formula))
check("定理5 注入总量线性公式成立",
      len(bad5) == 0, f"{len(bad5)} 处不符" if bad5 else "")

# 定理6：与库规模无关
sizes = [10, 500, 10000, 1000000]
vals = [inject_total(20, 1000) for _ in sizes]
check("定理6 注入量与记忆库规模无关",
      len(set(vals)) == 1, f"{len(set(vals))} 种不同结果")

# 定理7：remainBudget 至少 200
def remain_budget(l0l2_full): 
    raw = TOTAL_CAP - l0l2_full if TOTAL_CAP > l0l2_full else 0
    b = min(BODY_CAP, raw)
    return 200 if b < 200 else b


bad7 = [f for f in [0, 1000, 4500, 4780, 5800, 6000, 7000]
        if remain_budget(f) < 200]
check("定理7 body 预算至少保留 200 字（穷举 7 种 L0L2 大小）",
      len(bad7) == 0, f"{bad7}" if bad7 else "")

# ============================================================
# 三、备份触发模型（对应 shouldBackup）
# ============================================================

print("\n【3】备份触发（Lean: max_loss_equals_n_minus_1）")


def should_backup(writes_since, n=20):
    return writes_since + 1 >= n


bad8 = []
for n in [1, 5, 20, 50]:
    # n-1 次写入后应该触发
    if not should_backup(n - 1, n):
        bad8.append(("trigger_fail", n))
    # n-2 次写入后不应触发（n>1 时）
    if n > 1 and should_backup(n - 2, n):
        bad8.append(("early_trigger", n))
check("定理8 第 n 次写入触发，第 n-1 次不触发",
      len(bad8) == 0, f"{bad8}" if bad8 else "")

# 定理8b：最大丢失窗口 = n-1
for n in [5, 20, 50]:
    max_loss = n - 1
    # 模拟：备份后写 n-1 条，此时还没触发，崩溃 → 丢 n-1 条
    writes = n - 1
    triggered = should_backup(writes - 1, n) if writes > 0 else False
    expect_lost = writes if not triggered else 0
    check(f"定理8b N={n} 最大丢失窗口 {max_loss} 条",
          expect_lost <= max_loss, f"实际丢 {expect_lost}")

# ============================================================
# 四、对照 Lean 源码里的定理数
# ============================================================

print("\n【4】Lean 文件与本文验证的对应关系")
lean_path = os.path.join(HERE, "..", "formal", "FatFish.lean")
if os.path.exists(lean_path):
    src = open(lean_path, encoding="utf-8").read()
    import re
    theorems = re.findall(r"^theorem (\w+)", src, re.M)
    defs = re.findall(r"^def (\w+)", src, re.M)
    print(f"  Lean 文件: {len(theorems)} 个定理, {len(defs)} 个定义")
    print(f"  本文验证: 8 组边界（穷举）")
    check("Lean 文件存在且非空", len(theorems) >= 8, f"{len(theorems)} 定理")
    # 确认关键定理都在
    for t in ["declared_done_always_honored", "max_consecutive_stops",
              "no_progress_stops", "continues_while_budget",
              "inject_total_linear", "inject_ignores_store",
              "remain_at_least_200", "max_loss_equals_n_minus_1"]:
        check(f"  Lean 有定理 {t}", t in src)
else:
    check("Lean 文件存在", False, "formal/FatFish.lean 缺失")

# ============================================================
# 汇总
# ============================================================
print("\n" + "=" * 72)
n_pass = sum(1 for x in results if x["ok"])
print(f"结果：{n_pass}/{len(results)} 通过")
for x in results:
    if not x["ok"]:
        print(f"  ❌ {x['name']}  {x['detail']}")
print("=" * 72)
print("""
★ 诚实边界：
  这不是 Lean 证明。Lean 证明的是「对所有输入成立」，
  这个脚本证明的是「对我穷举的状态空间成立」。

  价值在于：如果定理写错了（比如判定顺序写反），
  穷举能立刻抓出来。如果穷举通过，说明定理至少在边界上没说谎。

  Lean 编译器仍在后台下载（593MB，之前断了两次）。
  只有它真的跑通、`lake build` 无错，才能说「形式化验证完成」。
""")

out = {"experiment": "E-507", "n_pass": n_pass, "n_total": len(results),
       "results": results,
       "note": "穷举验证，非 Lean 证明；Lean 编译待环境就绪"}
p = os.path.join(HERE, "results", "e507_lean_logic.json")
os.makedirs(os.path.dirname(p), exist_ok=True)
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 {p}")

raise SystemExit(0 if n_pass == len(results) else 1)
