"""E-27 钩子保护验证：钩子记忆必须不可失效、不可被合并、永远在场

用户要求：告诉模型"该怎么做"的行为准则类记忆，固定不可删除。

验证项：
  ① invalidate 钩子 → 必须失败，记忆仍有效
  ② supersede 钩子 → 必须失败
  ③ 睡眠整理 invalidate 钩子 → 必须跳过
  ④ 睡眠整理 merge 掉钩子 → 必须跳过
  ⑤ search 结果里钩子永远在场（即使 query 完全不相关）
  ⑥ hook 命令：lock / unlock / list / check
  ⑦ 非钩子记忆仍可正常失效（不能误伤）
"""
import sys
import os
import json

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))

from store_v2 import MemoryStore
import cli_mcp
import hook as _hook
import consolidate as _con
from store_v1 import Embedder

db = os.path.join(_HERE, "work", "e27_hook.db")
for suf in ("", "-wal", "-shm"):
    p = db + suf
    if os.path.exists(p):
        os.remove(p)

st = MemoryStore(db)
results = []


def check(name, cond, detail=""):
    results.append({"name": name, "ok": bool(cond), "detail": detail})
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  {detail}" if detail else ""))


print("=" * 74)
print("E-27 钩子保护验证")
print("=" * 74)

# ===== 准备数据 =====
print("\n【准备】写入钩子记忆 + 普通记忆")
hook_texts = [
    "遇到问题先自己尝试解决，实在解决不了才统一询问用户",
    "一切判断以实验数据为准，不凭主观推测",
    "要判断的地方交给模型，不需要判断的交给系统",
]
normal_texts = [
    "登录密码是 8848",
    "测试服务器密码是 5678",
    "项目 A 已交付",
]

hook_ids = []
for t in hook_texts:
    r = cli_mcp.cmd_add(st, {"text": t})
    hook_ids.append(r["ep_id"])
normal_ids = []
for t in normal_texts:
    r = cli_mcp.cmd_add(st, {"text": t})
    normal_ids.append(r["ep_id"])

# 锁定为钩子
for eid in hook_ids:
    cli_mcp.cmd_hook(st, {"action": "lock", "ep_id": eid, "kind": "principle"})

print(f"  钩子 {len(hook_ids)} 条，普通 {len(normal_ids)} 条")

# ===== ① invalidate 钩子 =====
print("\n【1】invalidate 钩子记忆 → 应失败")
ok = st.invalidate(hook_ids[0])
ep = st.get(hook_ids[0])
check("invalidate 返回 False", ok is False, f"返回 {ok}")
check("钩子仍有效", ep is not None and ep.invalid_at is None,
      f"invalid_at={ep.invalid_at if ep else 'N/A'}")

# ===== ② supersede 钩子 =====
print("\n【2】supersede 钩子记忆 → 应失败")
ok2, new_ep = st.supersede(hook_ids[1], "新的准则内容")
ep2 = st.get(hook_ids[1])
check("supersede 返回 False", ok2 is False, f"返回 {ok2}")
check("钩子仍有效", ep2 is not None and ep2.invalid_at is None)

# ===== ③ 睡眠整理 invalidate 钩子 =====
print("\n【3】睡眠整理尝试 invalidate 钩子 → 应跳过")
r = _con.apply_sleep_verdict(st, [
    {"op": "invalidate", "ep_id": hook_ids[2]},
])
ep3 = st.get(hook_ids[2])
check("被跳过", len(r.get("invalidated", [])) == 0,
      f"invalidated={r.get('invalidated')}")
check("记录 skipped_hooks", hook_ids[2] in r.get("skipped_hooks", []))
check("钩子仍有效", ep3 is not None and ep3.invalid_at is None)

# ===== ④ 睡眠整理 merge 掉钩子 =====
print("\n【4】睡眠整理尝试 merge 掉钩子 → 应跳过")
r2 = _con.apply_sleep_verdict(st, [
    {"op": "merge", "keep_id": normal_ids[0], "drop_id": hook_ids[0],
     "merged_text": "错误合并"},
])
ep_h = st.get(hook_ids[0])
check("merge 被跳过", len(r2.get("merged", [])) == 0,
      f"merged={r2.get('merged')}")
check("钩子仍有效且原文未变",
      ep_h is not None and ep_h.invalid_at is None and ep_h.raw_text == hook_texts[0])

# ===== ⑤ search 里钩子永远在场 =====
print("\n【5】search 钩子永远在场（用完全无关的 query）")
res = st.search("今天天气怎么样", top=3)
got_ids = [ep.ep_id for ep, _, _ in res]
hook_present = all(h in got_ids for h in hook_ids)
check("无关 query 下钩子仍全部在场", hook_present,
      f"命中钩子 {sum(1 for h in hook_ids if h in got_ids)}/{len(hook_ids)}")
# 检查 flag=2
flag2 = [ep.ep_id for ep, _, fl in res if fl == 2]
check("钩子 flag=2 标识", len(flag2) >= len(hook_ids), f"{len(flag2)} 条")

# ===== ⑥ hook 命令 =====
print("\n【6】hook 命令")
lr = cli_mcp.cmd_hook(st, {"action": "list"})
check("list 返回钩子", lr.get("ok") and len(lr.get("hooks", [])) == 3,
      f"{len(lr.get('hooks', []))} 条")
cr = cli_mcp.cmd_hook(st, {"action": "check", "ep_id": hook_ids[0]})
check("check 识别钩子", cr.get("is_hook") is True)
cr2 = cli_mcp.cmd_hook(st, {"action": "check", "ep_id": normal_ids[0]})
check("check 识别非钩子", cr2.get("is_hook") is False)

# unlock 测试
cli_mcp.cmd_hook(st, {"action": "unlock", "ep_id": hook_ids[2]})
check("unlock 后可失效", _hook.is_hook(st, hook_ids[2]) is False)
ok3 = st.invalidate(hook_ids[2])
check("unlock 后 invalidate 成功", ok3 is True)
# 恢复
cli_mcp.cmd_hook(st, {"action": "lock", "ep_id": hook_ids[2]})
st.con.execute("UPDATE episodes SET invalid_at=NULL, superseded_by=NULL WHERE ep_id=?",
               (hook_ids[2],))
st.con.commit()
st.invalidate_vector_cache()

# ===== ⑦ 普通记忆仍可失效（不误伤）=====
print("\n【7】普通记忆仍可正常失效")
ok4 = st.invalidate(normal_ids[2])
ep_n = st.get(normal_ids[2])
check("普通记忆失效成功", ok4 is True and ep_n.invalid_at is not None)

st.con.close()

# ===== 汇总 =====
print("\n" + "=" * 74)
n_ok = sum(1 for r in results if r["ok"])
print(f"结果：{n_ok}/{len(results)} 通过")
for r in results:
    if not r["ok"]:
        print(f"  ❌ {r['name']}  {r['detail']}")
print("=" * 74)

out = {"experiment": "E-27", "n_pass": n_ok, "n_total": len(results),
       "results": results}
os.makedirs(os.path.join(_HERE, "results"), exist_ok=True)
with open(os.path.join(_HERE, "results", "e27_hook.json"), "w",
          encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 results/e27_hook.json")
sys.exit(0 if n_ok == len(results) else 1)
