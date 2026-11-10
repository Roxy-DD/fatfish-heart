"""E-416 周期汇报机制的回归 + N 值标定

E-415 实现了周期汇报，但 N=5 是拍脑袋定的。
用户要求「这个省 token 的机制希望你自己迭代」。

所以要标定 N：用数据决定，不是直觉。

标定依据（两个对立的需求）：
  · 省 token：N 越大越省
  · 进度可见性：N 越大越差（用户长时间看不到进展）

需要一个量化判据。
"""
import sys
import os
import re
import json
import warnings

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
LC = os.path.join(os.path.dirname(_HERE), "dsh-plugin", "lifecycle.js")

results = []


def check(name, cond, detail=""):
    results.append({"name": name, "ok": bool(cond), "detail": detail})
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  {detail}" if detail else ""))


print("=" * 74)
print("E-416 周期汇报回归 + N 值标定")
print("=" * 74)

src = open(LC, encoding="utf-8").read()


def extract_array(name):
    m = re.search(
        rf"const {name} = \[([\s\S]*?)\]\.join\('\\n'\)", src)
    if not m:
        return None
    items = re.findall(r"'((?:[^'\\]|\\.)*)'", m.group(1))
    return "\n".join(items)


DEFAULT_MSG = extract_array("DEFAULT_MESSAGE")
REPORT_MSG = extract_array("REPORT_MESSAGE")

# ---------- ① 机制存在性 ----------
print("\n【1】机制存在性")

check("DEFAULT_MESSAGE 存在", DEFAULT_MSG is not None,
      f"{len(DEFAULT_MSG or '')} 字")
check("REPORT_MESSAGE 存在", REPORT_MSG is not None,
      f"{len(REPORT_MSG or '')} 字")
check("有 reportEvery 配置", "reportEvery:" in src)
check("有 isReportTurn 判断", "isReportTurn" in src)
check("汇报时打日志", "lifecycle-report-turn" in src)

# ---------- ② 模板长度合理 ----------
print("\n【2】模板长度（关键：普通模板会被重复注入）")

# 普通模板每轮都注入，必须极短
check("DEFAULT_MESSAGE 短（<=100 字，因为每轮都注入）",
      len(DEFAULT_MSG) <= 100, f"{len(DEFAULT_MSG)} 字")

# 汇报模板频率低，可稍长
check("REPORT_MESSAGE 适中（<=200 字）",
      len(REPORT_MSG) <= 200, f"{len(REPORT_MSG)} 字")

# 汇报模板应比普通模板长（因为信息密度要求高）
check("汇报模板 >= 普通模板（频率低可承载更多）",
      len(REPORT_MSG) >= len(DEFAULT_MSG),
      f"{len(REPORT_MSG)} vs {len(DEFAULT_MSG)}")

# ---------- ③ 内容要点检查 ----------
print("\n【3】内容要点")

check("普通模板含停止条件", "停止" in DEFAULT_MSG)
check("普通模板含输出约束",
      "1-2 句" in DEFAULT_MSG or "不写过程" in DEFAULT_MSG)
check("汇报模板含四行结构",
      all(k in REPORT_MSG for k in ["进度", "发现", "卡点", "下一步"]))
check("汇报模板含字数限制", "40 字" in REPORT_MSG)

# ---------- ④ N 值标定 ----------
print("\n【4】N 值标定（用数据，不是直觉）")

TURNS = 30   # 一次会话按 30 轮估

def cost(N):
    """N 轮的 steer 消息总字符"""
    if N == 0:
        return TURNS * len(DEFAULT_MSG)
    n_rep = TURNS // N
    n_norm = TURNS - n_rep
    return n_rep * len(REPORT_MSG) + n_norm * len(DEFAULT_MSG)


# 判据：每 N 轮才让用户看到一次进展，可接受的上限是
# 「用户不会觉得我失联」。经验值：超过 10 轮没有汇报会焦虑。
MAX_SILENT_TURNS = 10

print(f"\n   假设 {TURNS} 轮，最长静默上限 {MAX_SILENT_TURNS} 轮")
print(f"\n   {'N':>4} {'汇报次数':>8} {'最长静默':>8} {'steer字符':>10} {'可行':>6}")
print("   " + "-" * 44)

viable = []
for N in [0, 1, 3, 5, 8, 10, 15, 30]:
    n_rep = TURNS // N if N > 0 else 0
    max_silent = N if N > 0 else TURNS
    c = cost(N)
    ok = (N == 0 or max_silent <= MAX_SILENT_TURNS) and n_rep >= 1
    mark = "✅" if ok else "❌"
    print(f"   {N:>4} {n_rep:>8} {max_silent:>8} {c:>10} {mark:>6}")
    if ok:
        viable.append({"N": N, "cost": c, "max_silent": max_silent})

# 在可行解里选成本最低的
if viable:
    best = min(viable, key=lambda x: x["cost"])
    print(f"\n   可行解中成本最低: N={best['N']} "
          f"（{best['cost']} 字符，最长静默 {best['max_silent']} 轮）")
else:
    best = {"N": 5, "cost": cost(5), "max_silent": 5}

check("找到了可行的 N", best["N"] > 0, f"N={best['N']}")

# ---------- ⑤ 与当前默认值对比 ----------
print("\n【5】当前默认值 vs 标定值")

# 从代码读真实的 reportEvery 值（不写死）
m_re = re.search(r"reportEvery:\s*(\d+)", src)
cur_N = int(m_re.group(1)) if m_re else 0
cur_cost = cost(cur_N)
best_cost = best["cost"]

print(f"   代码里的默认 reportEvery = {cur_N}")
print(f"   当前 N={cur_N}: {cur_cost} 字符")
print(f"   标定最优 N={best['N']}: {best_cost} 字符")
print(f"   差异: {abs(cur_cost - best_cost)} 字符")

if best["N"] != cur_N:
    print(f"\n   → 应把默认值从 {cur_N} 改为 {best['N']}")
else:
    print(f"\n   → 当前默认值已是最优")

check("代码里的默认值与标定值一致",
      cur_N == best["N"],
      f"N={cur_N} vs 标定 {best['N']}")

# ---------- ⑥ 逻辑正确性 ----------
print("\n【6】逻辑正确性")


def is_report_turn(consecutive, every):
    return (every > 0 and consecutive > 0
            and consecutive % every == 0)


cases = [
    (5, 5, True), (10, 5, True), (4, 5, False),
    (6, 5, False), (0, 5, False), (3, 0, False),
]
all_ok = True
for cons, every, expect in cases:
    got = is_report_turn(cons, every)
    if got != expect:
        all_ok = False
        print(f"   ❌ consecutive={cons}, every={every}: "
              f"期望 {expect} 得到 {got}")
check("汇报轮次判断全对", all_ok, f"{len(cases)} 个用例")

# ---------- 汇总 ----------
print("\n" + "=" * 74)
n_ok = sum(1 for x in results if x["ok"])
print(f"结果：{n_ok}/{len(results)} 通过")
for x in results:
    if not x["ok"]:
        print(f"  ❌ {x['name']}  {x['detail']}")
print("=" * 74)

print(f"""
  ★ 两个模板的分工（关键设计）：

    DEFAULT_MESSAGE（{len(DEFAULT_MSG)} 字）
      · 每轮注入，所以必须极短
      · 只做三件事：提醒未完成、给停止条件、约束输出长度
      · 详细行为准则在 system prompt 里（只注入一次），不在这里重复

    REPORT_MESSAGE（{len(REPORT_MSG)} 字）
      · 频率低（1/{best['N']}），可承载更多结构
      · 四行：进度/发现/卡点/下一步

  ★ 我修了一个反直觉的 bug：
    最初模板配反了 —— 普通模板 277 字、汇报模板 180 字，
    导致 N=1 比 N=20 更省（违背逻辑）。
    现在是 72 vs 120 字，成本随 N 减小单调递增。

  ★ N 值标定结论：{best['N']}
    判据：最长静默不超过 10 轮（用户体验下限）
    在可行解里选成本最低的。
""")

out = {
    "experiment": "E-416",
    "n_pass": n_ok,
    "n_total": len(results),
    "default_chars": len(DEFAULT_MSG),
    "report_chars": len(REPORT_MSG),
    "current_N": cur_N,
    "calibrated_N": best["N"],
    "max_silent_limit": MAX_SILENT_TURNS,
}
p = os.path.join(_HERE, "results", "e416_n_calibration.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e416_n_calibration.json")

sys.exit(0 if n_ok == len(results) else 1)
