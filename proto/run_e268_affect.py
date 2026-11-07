"""E-268 affect 模型版接入 + 修复 valence 类型冲突

问题（E-245 诊断 + 本轮排查）：
 ① affect 从未真实触发（audit 0 条）
    根因：插件 mem_add 从未传 judge_affect，
          系统一直走规则降级路径（affect_v3），模型版从未启用。

 ② 修复过程中发现的真实设计冲突：
    affect_v4 的 parse 强制 valence 为 int 且只允许 -1/0/+1，
    但 episodes.valence 是 REAL 字段，
    且共振判断要求 |valence| >= 0.15。
    → 三值化后 |valence| 只能是 0 或 1，
      绝大多数记忆 valence=0，共振路径永不触发
      （E-149 已实测"只有 3.1% 满足"，三值化让它更糟）

修复：
 ① 插件 mem_add 传 judge_affect=true，
    收到 affect_prompt 后调模型，再发 affect 命令写回。
 ② affect_v4 的 valence 改为连续值 [-1, 1]，
    prompt 同步改为要求连续强度。
    保持向后兼容：三值 -1/0/+1 是连续值的合法特例。
"""
import sys
import os
import json

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))

from store_v2 import MemoryStore
import cli_mcp
import affect_v4 as af
import audit as _audit

DB = os.path.join(_HERE, "work", "test_db", "e268v.db")
os.makedirs(os.path.dirname(DB), exist_ok=True)
for suf in ("", "-wal", "-shm"):
    p = DB + suf
    if os.path.exists(p):
        os.remove(p)

st = MemoryStore(DB)
results = []


def check(name, cond, detail=""):
    results.append({"name": name, "ok": bool(cond), "detail": detail})
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  {detail}" if detail else ""))


print("=" * 74)
print("E-268 affect 模型版 + valence 连续值")
print("=" * 74)

# ============ ① valence 连续值解析 ============
print("\n【1】valence 连续值解析")

cases = [
    ({"affect": 0.92, "valence": -0.85, "reason": "丧亲"}, "连续负值", 0.92, -0.85),
    ({"affect": 0.45, "valence": 0.5, "reason": "温馨"}, "连续正值", 0.45, 0.50),
    ({"affect": 0.5, "valence": -1, "reason": "三值"}, "三值兼容", 0.50, -1.00),
    ({"affect": 0.0, "valence": -0.9, "reason": "x"}, "affect=0 归零", 0.0, 0.0),
    ({"affect": 0.8, "valence": 2.5, "reason": "x"}, "越界钳制", 0.80, 1.00),
    ({"affect": 0.6, "valence": -5, "reason": "x"}, "负向越界", 0.60, -1.00),
]
for payload, desc, exp_a, exp_v in cases:
    r = af.parse_affect_output(json.dumps(payload, ensure_ascii=False))
    ok = abs(r["affect"] - exp_a) < 0.001 and abs(r["valence"] - exp_v) < 0.001
    check(desc, ok, f"affect={r['affect']:.2f} valence={r['valence']:.2f}")

# 关键：连续值不再被截断成 0
r = af.parse_affect_output('{"affect":0.9,"valence":-0.85,"reason":"x"}')
check("连续 -0.85 不再被截断", abs(r["valence"] + 0.85) < 0.001,
      f"得到 {r['valence']}")

# ============ ② 端到端：写入 → 模型判断 → 落库 ============
print("\n【2】端到端：写入 → 模型判断 → 落库")

r1 = cli_mcp.cmd_add(st, {
    "text": "爷爷去世那天我在医院走廊哭了一整夜",
    "judge_affect": True,
})
check("写入成功", r1.get("ok"))
check("返回 need_affect", r1.get("need_affect") is True)
ap = r1.get("affect_prompt") or {}
check("返回 affect_prompt", bool(ap.get("system")) and bool(ap.get("user")),
      f"system {len(ap.get('system',''))} 字")

if r1.get("need_affect"):
    raw = json.dumps({"affect": 0.92, "valence": -0.85,
                      "reason": "亲人离世的强烈悲伤"}, ensure_ascii=False)
    r2 = cli_mcp.cmd_affect(st, {"ep_id": r1["ep_id"], "raw": raw})
    check("cmd_affect 成功", r2.get("ok"))
    check("affect 正确写入", abs(r2.get("affect", 0) - 0.92) < 0.001,
          f"{r2.get('affect')}")
    check("valence 正确写入", abs(r2.get("valence", 0) + 0.85) < 0.001,
          f"{r2.get('valence')}")

    # 验证真的落库
    ep = st.get(r1["ep_id"])
    check("落库 affect_peak", abs(ep.affect_peak - 0.92) < 0.001,
          f"{ep.affect_peak}")
    check("落库 valence", abs(ep.valence + 0.85) < 0.001, f"{ep.valence}")

# ============ ③ 共振路径现在能触发 ============
print("\n【3】共振路径（原三值化时几乎永不触发）")

# 造两条同向情绪记忆
a = cli_mcp.cmd_add(st, {"text": "失去至亲的痛苦至今难忘", "judge_affect": True})
b = cli_mcp.cmd_add(st, {"text": "亲人离世那年的悲伤一直压着我", "judge_affect": True})

if a.get("need_affect") and b.get("need_affect"):
    cli_mcp.cmd_affect(st, {"ep_id": a["ep_id"], "raw":
                            '{"affect":0.85,"valence":-0.8,"reason":"x"}'})
    cli_mcp.cmd_affect(st, {"ep_id": b["ep_id"], "raw":
                            '{"affect":0.80,"valence":-0.75,"reason":"x"}'})

    ea = st.get(a["ep_id"])
    eb = st.get(b["ep_id"])
    print(f"   a: affect={ea.affect_peak} valence={ea.valence}")
    print(f"   b: affect={eb.affect_peak} valence={eb.valence}")

    # 共振条件：|valence| >= 0.15 且同向 且 |affect差| <= 0.25
    res = af.resonance_score(ea.affect_peak, ea.valence,
                             eb.affect_peak, eb.valence)
    check("共振分数 > 0", res > 0, f"{res:.3f}")
    check("|valence| >= 0.15 满足", abs(ea.valence) >= 0.15,
          f"|{ea.valence}|")

    # 对比：若按原三值化，valence=-1 也满足；
    # 但连续值能表达强度差异（-0.8 vs -0.3 都是负面，共鸣程度不同）
    r_strong = af.resonance_score(0.85, -0.8, 0.80, -0.75)
    r_weak = af.resonance_score(0.85, -0.2, 0.80, -0.15)
    check("强负面共振 > 弱负面共振", r_strong > r_weak,
          f"{r_strong:.3f} vs {r_weak:.3f}")

# ============ ④ 审计记录 ============
print("\n【4】审计记录")
astats = _audit.stats(st)
n_affect = astats["by_kind"].get("affect", 0)
check("audit 里有 affect 记录", n_affect > 0, f"{n_affect} 条")

# ============ ⑤ 规则降级仍可用 ============
print("\n【5】无模型时降级路径仍正常")
r_fb = af.fallback_rule("爷爷去世了我很难过")
check("降级返回 affect", "affect" in r_fb, str(r_fb)[:60])
check("降级有 reason", bool(r_fb.get("reason")), r_fb.get("reason", "")[:30])

st.con.close()

# ============ 汇总 ============
print("\n" + "=" * 74)
n_ok = sum(1 for r in results if r["ok"])
print(f"结果：{n_ok}/{len(results)} 通过")
for r in results:
    if not r["ok"]:
        print(f"  ❌ {r['name']}  {r['detail']}")
print("=" * 74)

out = {"experiment": "E-268", "n_pass": n_ok, "n_total": len(results),
       "results": results}
p = os.path.join(_HERE, "results", "e268_affect.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e268_affect.json")

sys.exit(0 if n_ok == len(results) else 1)
