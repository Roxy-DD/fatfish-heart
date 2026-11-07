"""E-266 config 审计：配置变更可追溯

问题（E-245 诊断）：
  config 从未被触发（audit 里 0 条）。
  配置变更没有审计记录，出问题查不到"谁在什么时候改了什么"。

设计：
  配置变更时写一条 audit kind=config，记录：
    · 改了哪些字段
    · 旧值 → 新值
    · 来源（UI / 模型 / 默认）

  这与 R4 可追溯一致：不只记忆内容可追溯，配置也要可追溯。

本实验：
  ① 实现 cmd_config_audit（内核侧）
  ② 验证 diff 计算正确
  ③ 验证敏感字段脱敏
"""
import sys
import os
import json

_HERE = os.path.dirname(os.path.abspath(__file__))
_MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, _MEM)

from store_v2 import MemoryStore
import audit as _audit

DB = os.path.join(_HERE, "work", "test_db", "e266.db")
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
print("E-266 config 审计")
print("=" * 74)

# ============ 设计 ============
SENSITIVE_KEYS = {"apiKey", "token", "password", "secret", "dbPath", "pythonPath"}


def diff_config(old: dict, new: dict) -> dict:
    """
    计算配置差异（纯机械）。

    只记录变化的字段，敏感字段脱敏。
    """
    changed = {}
    all_keys = set(old or {}) | set(new or {})
    for k in all_keys:
        ov = (old or {}).get(k)
        nv = (new or {}).get(k)
        if ov == nv:
            continue
        if k in SENSITIVE_KEYS:
            # 脱敏：只记录"变了"，不记录具体值
            changed[k] = {"old": "***", "new": "***", "sensitive": True}
        else:
            changed[k] = {"old": ov, "new": nv}
    return changed


def log_config_change(st, old: dict, new: dict, source: str = "ui") -> dict:
    """把配置变更写入审计日志"""
    d = diff_config(old, new)
    if not d:
        return {"ok": True, "changed": 0, "note": "无变化"}
    try:
        _audit.log(st, "config",
                   msg=f"配置变更 {len(d)} 项（来源 {source}）："
                       + ", ".join(list(d.keys())[:5]),
                   extra={"changed": d, "source": source,
                          "n_changed": len(d)})
        return {"ok": True, "changed": len(d), "fields": list(d.keys())}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# ============ ① diff 计算 ============
print("\n【1】diff 计算")

old_cfg = {"autoInjectMaxChars": 1500, "minCost": 0.7, "enabled": True}
new_cfg = {"autoInjectMaxChars": 2000, "minCost": 0.7, "enabled": True}
d1 = diff_config(old_cfg, new_cfg)
check("只记录变化的字段", len(d1) == 1, f"{len(d1)} 项")
check("正确识别字段", "autoInjectMaxChars" in d1, str(list(d1.keys())))
check("旧值正确", d1["autoInjectMaxChars"]["old"] == 1500)
check("新值正确", d1["autoInjectMaxChars"]["new"] == 2000)

# 无变化
d2 = diff_config(old_cfg, old_cfg.copy())
check("无变化时返回空", len(d2) == 0)

# ============ ② 敏感字段脱敏 ============
print("\n【2】敏感字段脱敏")
old_s = {"apiKey": "sk-1234567890", "port": 8080}
new_s = {"apiKey": "sk-9999999999", "port": 9090}
d3 = diff_config(old_s, new_s)
check("apiKey 被脱敏", d3.get("apiKey", {}).get("sensitive") is True)
check("apiKey 不暴露原值",
      d3.get("apiKey", {}).get("old") == "***" and
      d3.get("apiKey", {}).get("new") == "***")
check("普通字段正常记录", d3.get("port", {}).get("new") == 9090)
# 关键：原始值不应出现在序列化结果里
serialized = json.dumps(d3, ensure_ascii=False)
check("序列化后不含密钥明文", "sk-1234567890" not in serialized and
      "sk-9999999999" not in serialized)

# ============ ③ 写入审计 ============
print("\n【3】写入审计日志")
r = log_config_change(st, old_cfg, new_cfg, source="ui")
check("写入成功", r.get("ok"))
check("记录 1 项变更", r.get("changed") == 1, f"{r.get('changed')}")

# 验证真的写进 audit_log
rows = st.con.execute(
    "SELECT kind, msg FROM audit_log WHERE kind='config'").fetchall()
check("audit_log 有 config 记录", len(rows) > 0, f"{len(rows)} 条")
if rows:
    print(f"     记录内容: {rows[0][1][:60]}")

# ============ ④ 无变化不应写日志 ============
print("\n【4】无变化时不应写日志")
n_before = st.con.execute(
    "SELECT COUNT(*) FROM audit_log WHERE kind='config'").fetchone()[0]
r2 = log_config_change(st, old_cfg, old_cfg.copy())
n_after = st.con.execute(
    "SELECT COUNT(*) FROM audit_log WHERE kind='config'").fetchone()[0]
check("无变化返回 changed=0", r2.get("changed") == 0)
check("未写入新日志", n_after == n_before, f"{n_before} → {n_after}")

# ============ ⑤ 多次变更可追溯 ============
print("\n【5】多次变更可追溯")
log_config_change(st, {"a": 1}, {"a": 2}, source="model")
log_config_change(st, {"a": 2}, {"a": 3}, source="ui")

logs = st.con.execute(
    "SELECT msg, extra FROM audit_log WHERE kind='config' "
    "ORDER BY seq").fetchall()
check("有 3 条 config 记录", len(logs) == 3, f"{len(logs)} 条")

# 验证 extra 里能查到历史值
if logs:
    extras = [json.loads(e[1]) if e[1] else {} for e in logs]
    a_values = []
    for ex in extras:
        ch = ex.get("changed", {})
        if "a" in ch:
            a_values.append(ch["a"].get("new"))
    check("能追溯 a 的变更历史", len(a_values) >= 2, str(a_values))

# ============ 审计统计 ============
print("\n【6】审计统计")
astats = _audit.stats(st)
check("stats 里 config 有计数", astats["by_kind"].get("config", 0) > 0,
      f"{astats['by_kind'].get('config', 0)}")

st.con.close()

# ============ 汇总 ============
print("\n" + "=" * 74)
n_ok = sum(1 for r in results if r["ok"])
print(f"结果：{n_ok}/{len(results)} 通过")
for r in results:
    if not r["ok"]:
        print(f"  ❌ {r['name']}  {r['detail']}")
print("=" * 74)

out = {"experiment": "E-266", "n_pass": n_ok, "n_total": len(results),
       "results": results}
p = os.path.join(_HERE, "results", "e266_config_audit.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e266_config_audit.json")

sys.exit(0 if n_ok == len(results) else 1)
