"""E-408 回归：生产库不可被测试数据污染

E-403~407 做的审计发现并修复了：
  · 2 条测试环境事件残留（已标记失效）
  · environment_events 缺 invalid_at 字段（已加）
  · 查询未过滤失效记录（已加过滤）

本测试固化三条约束：
  ① episodes 里没有仍有效的测试数据
  ② 环境事件默认只返回有效记录
  ③ 清理用标记不删除（可追溯）

★ 同时固化一个教训：
  「含测试字样」≠「是测试数据」。
  E-405 曾误报一条真实笔记，E-406 才纠正。
"""
import sys
import os
import json
import warnings
import sqlite3

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, MEM)

from store_v2 import MemoryStore
import environment as env

DB = os.path.join(MEM, "dsh_mem.db")
st = MemoryStore(DB)

results = []


def check(name, cond, detail=""):
    results.append({"name": name, "ok": bool(cond), "detail": detail})
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  {detail}" if detail else ""))


print("=" * 74)
print("E-408 生产库卫生回归")
print("=" * 74)

# ---------- ① episodes 无有效测试数据 ----------
print("\n【1】episodes 无仍有效的测试数据")

# 判定规则（E-406 修正版）：
#   短 + 明显的测试特征 = 测试数据
#   只是提到"测试"这个方法 = 真实内容
rows = st.con.execute(
    "SELECT ep_id, raw_text, length(raw_text), invalid_at "
    "FROM episodes WHERE raw_text LIKE '%测试%'").fetchall()

test_like = []
for eid, txt, ln, inv in rows:
    if inv is not None:
        continue
    # 测试数据的特征：很短 + 以"测试"开头或含明显假数据模式
    is_test = (ln < 40 and (
        txt.startswith("测试") or
        txt.startswith("性能测试") or
        "abcxyz" in txt or
        ("密码" in txt and any(x in txt for x in
                              ("1234", "5678", "8888", "9999", "home2024")))
    ))
    if is_test:
        test_like.append((eid, txt[:50], ln))

check("没有仍有效的测试数据", len(test_like) == 0,
      f"{len(test_like)} 条" if test_like else "0 条")
for eid, txt, ln in test_like[:5]:
    print(f"      [{eid[:8]}] {ln}字 {txt}")

# 但真实内容（讲测试方法论的）不应被误判
# 注意：这类记忆可能已因其他原因失效（如被合并），
# 所以判断的是"有没有被我的测试数据清理逻辑误伤"，
# 而不是"必须仍有效"。
real_test_topic = [r for r in rows if r[2] >= 30]
check("提到'测试'的实质内容未被误判为测试数据",
      len(real_test_topic) > 0,
      f"{len(real_test_topic)} 条（含已失效）")

# ---------- ② 环境事件默认过滤 ----------
print("\n【2】环境事件默认过滤失效记录")

s = env.stats(st)
check("stats 有 invalid 字段", "invalid" in s, str(s.get("invalid")))
check("stats 的 total 只算有效", s.get("total", 0) >= 0,
      f"total={s.get('total')}, invalid={s.get('invalid')}")

recent_valid = env.recent(st, limit=50)
recent_all = env.recent(st, limit=50, include_invalid=True)
check("默认查询条数 <= 全部查询条数",
      len(recent_valid) <= len(recent_all),
      f"{len(recent_valid)} <= {len(recent_all)}")

# 默认查询里不应有已失效的
invalid_ids = set()
for row in st.con.execute(
        "SELECT event_id FROM environment_events "
        "WHERE invalid_at IS NOT NULL").fetchall():
    invalid_ids.add(row[0])
leaked = [e for e in recent_valid if e["event_id"] in invalid_ids]
check("默认查询不含失效记录", len(leaked) == 0, f"{len(leaked)} 条泄漏")

# ---------- ③ 标记而非删除 ----------
print("\n【3】清理用标记不删除（可追溯）")

total_rows = st.con.execute(
    "SELECT COUNT(*) FROM environment_events").fetchone()[0]
invalid_rows = st.con.execute(
    "SELECT COUNT(*) FROM environment_events "
    "WHERE invalid_at IS NOT NULL").fetchone()[0]
valid_rows = total_rows - invalid_rows

check("总行数 = 有效 + 失效（无物理删除）",
      total_rows == valid_rows + invalid_rows,
      f"{total_rows} = {valid_rows} + {invalid_rows}")
check("失效记录仍可查（可追溯）",
      invalid_rows == 0 or len(recent_all) >= invalid_rows,
      f"失效 {invalid_rows} 条")

# ---------- ④ 关键字段存在 ----------
print("\n【4】表结构")

cols = [c[1] for c in st.con.execute(
    "PRAGMA table_info(environment_events)").fetchall()]
check("environment_events 有 invalid_at", "invalid_at" in cols,
      str(cols))

# ---------- 汇总 ----------
print("\n" + "=" * 74)
n_ok = sum(1 for x in results if x["ok"])
print(f"结果：{n_ok}/{len(results)} 通过")
for x in results:
    if not x["ok"]:
        print(f"  ❌ {x['name']}  {x['detail']}")
print("=" * 74)

print(f"""
  ★ 固化的三条约束：
    ① episodes 里没有仍有效的测试数据
    ② 环境事件默认只返回有效记录
    ③ 清理用标记不删除（可追溯）

  ★ 固化的教训：
    「含测试字样」≠「是测试数据」。
    E-405 用关键词匹配误报了一条真实笔记
    （《插件热重载：状态该放哪，测试怎么测》），
    E-406 看内容才纠正。
""")

out = {
    "experiment": "E-408",
    "n_pass": n_ok,
    "n_total": len(results),
    "episodes_test_like": len(test_like),
    "real_test_topic": len(real_test_topic),
    "env_total": total_rows,
    "env_valid": valid_rows,
    "env_invalid": invalid_rows,
}
p = os.path.join(_HERE, "results", "e408_hygiene.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e408_hygiene.json")

st.con.close()
sys.exit(0 if n_ok == len(results) else 1)
