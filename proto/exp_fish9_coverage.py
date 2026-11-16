"""FISH-9 实验：备份触发覆盖率

不是预设方案，而是先测真实数据：
  1. 记忆写入速率（episodes 增长 / 时间）
  2. mem_sleep 实际调用频率
  3. 各触发点下的「最大可能丢失量」和「备份成本」

数据源（都是物理痕迹，不是推测）：
  - episodes.created_at  真实写入时间
  - audit_log            sleep 事件与配置变更
  - plugin-debug.jsonl   运行时日志
  - 备份文件 mtime/大小   成本

输出：proto/results/fish9_backup_coverage.json
"""
import json
import os
import sys
import sqlite3
import time
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "cosmos_mem"))
DB = os.path.join(HERE, "cosmos_mem", "dsh_mem.db")
LOG = os.path.join(HERE, "cosmos_mem", "plugin-debug.jsonl")

out = {"experiment": "FISH-9", "at": time.strftime("%Y-%m-%dT%H:%M:%S")}

# ---------- ① 写入速率 ----------
con = sqlite3.connect(DB)
rows = con.execute(
    "SELECT created_at FROM episodes WHERE created_at IS NOT NULL "
    "ORDER BY created_at").fetchall()
ts = [r[0] for r in rows if isinstance(r[0], (int, float))]
if ts:
    span_days = (max(ts) - min(ts)) / 86400
    out["write_rate"] = {
        "n_episodes": len(ts),
        "first": time.strftime("%Y-%m-%d", time.localtime(min(ts))),
        "last": time.strftime("%Y-%m-%d", time.localtime(max(ts))),
        "span_days": round(span_days, 2),
        "per_day": round(len(ts) / max(span_days, 0.01), 2),
        "avg_gap_hours": round(span_days * 24 / max(len(ts) - 1, 1), 2),
    }
else:
    out["write_rate"] = {"error": "no timestamps"}

# ---------- ② sleep 频率 ----------
sleep_events = con.execute(
    "SELECT at FROM audit_log WHERE kind='sleep' ORDER BY at").fetchall()
sleep_ts = []
for (a,) in sleep_events:
    try:
        sleep_ts.append(float(a))
    except (ValueError, TypeError):
        pass
out["sleep"] = {
    "count_in_audit": len(sleep_ts),
    "times": [time.strftime("%Y-%m-%d %H:%M", time.localtime(t))
              for t in sleep_ts[-10:]],
}

# ---------- ③ audit 里其他可见的写事件 ----------
kinds = con.execute(
    "SELECT kind, COUNT(*) FROM audit_log GROUP BY kind ORDER BY COUNT(*) DESC"
).fetchall()
out["audit_kinds"] = dict(kinds[:12])
con.close()

# ---------- ④ 现有备份成本 ----------
backs = []
d = os.path.dirname(DB)
base = os.path.basename(DB)
for name in os.listdir(d):
    if name.startswith(base + ".backup"):
        p = os.path.join(d, name)
        if os.path.isfile(p):
            backs.append({"size_kb": os.path.getsize(p) // 1024,
                          "name": name})
out["backup_cost"] = {
    "count": len(backs),
    "total_kb": sum(b["size_kb"] for b in backs),
    "avg_kb": round(sum(b["size_kb"] for b in backs) / max(len(backs), 1)),
    "samples": backs[:3],
}

# ---------- ⑤ 各触发策略的模拟 ----------
# 用真实写入时间戳模拟：丢失量 = 最后一次备份到崩溃时的写入数
if ts:
    strategies = {}
    # A: 只在 mem_sleep（用真实 sleep 时间，近似取最后 N 次）
    for label, interval_writes in [
        ("per_1_write", 1),
        ("per_5_writes", 5),
        ("per_20_writes", 20),
        ("per_50_writes", 50),
        ("sleep_only", None),
    ]:
        if interval_writes is None:
            # 用实际 sleep 次数估算：7 天 3 次
            backups_n = out["sleep"]["count_in_audit"]
            max_loss = len(ts) // max(backups_n + 1, 1)
        else:
            backups_n = len(ts) // interval_writes
            max_loss = interval_writes - 1
        strategies[label] = {
            "backup_count": backups_n,
            "max_loss_episodes": max_loss,
            "cost_mb": round(backups_n * out["backup_cost"]["avg_kb"] / 1024, 2),
        }
    out["simulation"] = strategies

# ---------- ⑥ 结论（由数据推出，不预设） ----------
if "write_rate" in out and "per_day" in out["write_rate"]:
    pd = out["write_rate"]["per_day"]
    out["conclusion_hint"] = (
        f"每天约 {pd} 条写入。"
        f"若接受最多丢 5 条，需约 {round(pd/5,1)} 次/天备份；"
        f"若接受最多丢 20 条，需约 {round(pd/20,1)} 次/天。"
        "具体 N 由「丢失代价 vs 备份成本」权衡，"
        "这里只给数据，不预设门槛。"
    )

p = os.path.join(HERE, "results", "fish9_backup_coverage.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1, default=str)

print("=" * 72)
print("FISH-9 备份触发覆盖率实验")
print("=" * 72)
print(json.dumps(out, ensure_ascii=False, indent=1, default=str)[:3000])
print(f"\n写入 {p}")
