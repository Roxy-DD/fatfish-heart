"""E-386 回归：注入策略实验的可信判据

E-382~385 期间踩的坑：
  · E-383 我写「N=3 比 N=1 划算得多」，
    但表格里 N=1 得分 0.125 > N=3 的 0.124 —— 叙述与数据不符
  · 「每千字命中」极差 2.203，指标不可靠

本测试固化两个判据：
  ① 覆盖率是可信指标（两轮实验差异 < 15pp）
  ② 效率指标不可靠（极差大），不能用来选 N
  ③ injectEveryTurn 开关存在且默认 false（不改变现状）
"""
import sys
import os
import json
import warnings

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))

results = []


def check(name, cond, detail=""):
    results.append({"name": name, "ok": bool(cond), "detail": detail})
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  {detail}" if detail else ""))


print("=" * 74)
print("E-386 注入策略实验判据回归")
print("=" * 74)

# ---------- ① 覆盖率一致性 ----------
print("\n【1】覆盖率指标的可信度")

r383 = json.load(open(os.path.join(_HERE, "results", "e383_tradeoff.json"),
                      encoding="utf-8"))
r384 = json.load(open(os.path.join(_HERE, "results", "e384_rigorous.json"),
                      encoding="utf-8"))

cov383 = {r["N"]: r["coverage"] for r in r383["rows"]}
cov384 = {r["N"]: r["coverage"] for r in r384["rows"]}

diffs = []
for N in [1, 2, 3, 5, 10]:
    if N in cov383 and N in cov384:
        diffs.append(abs(cov383[N] - cov384[N]))

check("两轮实验覆盖率差异均 < 15pp",
      all(d < 0.15 for d in diffs),
      f"最大差异 {max(diffs)*100:.0f}pp")

# 覆盖率随 N 单调递减
covs = [(r["N"], r["coverage"]) for r in r384["rows"]
        if r["N"] < 30]
is_mono = all(covs[i][1] >= covs[i+1][1] for i in range(len(covs)-1))
check("覆盖率随 N 单调递减（可信趋势）", is_mono,
      " > ".join(f"{c*100:.0f}%" for _, c in covs))

# ---------- ② 效率指标不可靠 ----------
print("\n【2】效率指标的不可靠性")

effs = [r["eff"] for r in r384["rows"]]
spread = max(effs) - min(effs)
check("效率指标极差较大（证明不可靠）",
      spread > 1.0,
      f"极差 {spread:.3f}（{min(effs):.3f}~{max(effs):.3f}）")

# 明确记录：不可用效率选 N
note = r384.get("verdict", "")
check("实验结论已明确标注不可用效率选 N",
      "效率" in note or "话题切换" in note, note[:50])

# ---------- ③ injectEveryTurn 开关 ----------
print("\n【3】injectEveryTurn 开关")

IDX = os.path.join(os.path.dirname(_HERE), "dsh-plugin", "index.js")
src = open(IDX, encoding="utf-8").read()

has_default = "injectEveryTurn: false" in src
has_schema = "injectEveryTurn: _Schema.boolean()" in src
has_guard = "const injectEvery = cfg.injectEveryTurn === true" in src

check("DEFAULTS 有 injectEveryTurn: false", has_default)
check("Config schema 有对应字段", has_schema)
check("守卫逻辑使用 injectEvery", has_guard)

# 默认 false 意味着行为不变
check("默认不改变现有行为（只注入一次）", has_default,
      "需显式设为 true 才启用每轮注入")

# ---------- ④ 开关不破坏 isFirstTurn 语义 ----------
print("\n【4】isFirstTurn 语义")

has_turncount = "st.turnCount" in src
uses_turncount = "st.turnCount === 1" in src
check("每轮注入模式下用 turnCount 判断真首轮", has_turncount and uses_turncount,
      "避免每轮都发全文")

# ---------- 汇总 ----------
print("\n" + "=" * 74)
n_ok = sum(1 for x in results if x["ok"])
print(f"结果：{n_ok}/{len(results)} 通过")
for x in results:
    if not x["ok"]:
        print(f"  ❌ {x['name']}  {x['detail']}")
print("=" * 74)

out = {
    "experiment": "E-386",
    "coverage_max_diff_pp": round(max(diffs) * 100, 1),
    "coverage_monotonic": is_mono,
    "eff_spread": round(spread, 3),
    "switch_exists": has_default and has_schema and has_guard,
}
p = os.path.join(_HERE, "results", "e386_criteria.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e386_criteria.json")

sys.exit(0 if n_ok == len(results) else 1)
