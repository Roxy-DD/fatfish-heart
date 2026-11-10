"""E-370 容灾与无损迁移的完整实测

用户问：是不是实现了记忆容灾？100% 无损迁移？原理一句话？

不能靠读代码断言，必须跑一次完整闭环：
  ① 备份 → 校验
  ② 破坏原库 → 恢复
  ③ 逐字段逐向量对比，看是否真的 100% 一致

关键：用**独立测试库**，不碰生产库。
"""
import sys
import os
import json
import hashlib
import sqlite3
import shutil
import warnings

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, MEM)

import backup as bk
from store_v2 import MemoryStore

WORK = os.path.join(_HERE, "work", "e370")
os.makedirs(WORK, exist_ok=True)
SRC = os.path.join(WORK, "src.db")
for suf in ("", "-wal", "-shm"):
    p = SRC + suf
    if os.path.exists(p):
        os.remove(p)

print("=" * 78)
print("E-370 容灾闭环实测")
print("=" * 78)

# ---------- ① 造一个有代表性的库 ----------
print("\n① 构造测试库")
st = MemoryStore(SRC)
TEXTS = [
    "用户的登录密码是 8848",
    "项目截止日期是下周五",
    "用户偏好简短回答",
    "记忆系统的四条红线必须遵守",
    "热重载时状态要绑定到实例",
]
ids = []
for t in TEXTS:
    r = st.add(t)
    ids.append(r.ep_id if hasattr(r, "ep_id") else r)
print(f"   写入 {len(ids)} 条")

# 加一条修订（产生 history）
st.revise(ids[0], new_text="用户的登录密码是 9496（已更新）")
print(f"   修订 1 条（产生 history）")

# 加一条失效
st.invalidate(ids[1])
print(f"   失效 1 条")

s0 = st.stats()
print(f"   状态: 有效 {s0['valid']} / 失效 {s0['invalid']} / 向量 {s0['vectors']}")

# ---------- ② 全量指纹（这是"无损"的判据）----------
def fingerprint(db_path):
    """对每个字段、每个向量做哈希"""
    con = sqlite3.connect(db_path)
    out = {"episodes": {}, "vectors": {}, "meta": {}, "history": {}}

    cols = [c[1] for c in con.execute(
        "PRAGMA table_info(episodes)").fetchall()]
    for row in con.execute("SELECT * FROM episodes"):
        d = dict(zip(cols, row))
        eid = d["ep_id"]
        # 每个字段单独取指纹
        out["episodes"][eid] = {
            c: hashlib.md5(str(d[c]).encode()).hexdigest()[:12]
            for c in cols
        }

    for eid, dim, vec in con.execute(
            "SELECT ep_id, dim, vec FROM vectors"):
        out["vectors"][eid] = (dim, hashlib.md5(vec).hexdigest()[:16])

    for k, v in con.execute("SELECT key, val FROM meta"):
        # hook:* 的 val 是 JSON，逐字节
        out["meta"][k] = hashlib.md5(str(v).encode()).hexdigest()[:12]

    for row in con.execute("SELECT ep_id, revision, raw_text FROM episodes_history"):
        key = f"{row[0]}#{row[1]}"
        out["history"][key] = hashlib.md5(str(row[2]).encode()).hexdigest()[:12]

    con.close()
    return out


print("\n② 记录全量指纹")
fp_before = fingerprint(SRC)
print(f"   episodes: {len(fp_before['episodes'])} 条")
print(f"   vectors:  {len(fp_before['vectors'])} 条")
print(f"   meta:     {len(fp_before['meta'])} 项")
print(f"   history:  {len(fp_before['history'])} 条")

# ---------- ③ 备份 + 校验 ----------
print("\n③ 备份 + 校验")
b = bk.backup_db(SRC, os.path.join(WORK, "src_backup.db"))
print(f"   backup_db: ok={b.get('ok')}")
bpath = b.get("path")
if not bpath:
    print("   ❌ 备份失败")
    sys.exit(1)
print(f"   备份文件: {os.path.basename(bpath)} "
      f"({os.path.getsize(bpath)//1024}KB)")

v = bk.verify_backup(SRC, bpath)
print(f"   verify_backup: ok={v.get('ok')}")
print(f"   counts: {v.get('counts')}")

# ---------- ④ 破坏原库 ----------
print("\n④ 破坏原库（模拟灾难）")
# 必须先关闭连接，否则 Windows 上文件被占用
try:
    st.con.close()
except Exception:
    pass
import gc
gc.collect()
import time as _t
_t.sleep(0.5)

for suf in ("", "-wal", "-shm"):
    p = SRC + suf
    if os.path.exists(p):
        os.remove(p)
print(f"   原库已删除: exists={os.path.exists(SRC)}")

# ---------- ⑤ 恢复 ----------
print("\n⑤ 从备份恢复")
r = bk.restore_db(bpath, SRC)
print(f"   restore_db: ok={r.get('ok')}")
print(f"   恢复记录: {r.get('restored')}")

# ---------- ⑥ 逐字段对比 ----------
print("\n⑥ 逐字段逐向量对比（判据）")
fp_after = fingerprint(SRC)

def compare(name, a, b):
    keys_a, keys_b = set(a), set(b)
    missing = keys_a - keys_b
    extra = keys_b - keys_a
    diff = [k for k in keys_a & keys_b if a[k] != b[k]]
    ok = not missing and not extra and not diff
    print(f"   {name:<12} {'✅' if ok else '❌'}  "
          f"条目 {len(a)}→{len(b)}  缺失{len(missing)} 多余{len(extra)} 差异{len(diff)}")
    if diff:
        for k in diff[:3]:
            print(f"      差异: {k}")
            if isinstance(a[k], dict):
                for c in a[k]:
                    if a[k][c] != b[k].get(c):
                        print(f"        字段 {c}: {a[k][c]} vs {b[k].get(c)}")
    return ok

ok_ep = compare("episodes", fp_before["episodes"], fp_after["episodes"])
ok_vec = compare("vectors", fp_before["vectors"], fp_after["vectors"])
ok_meta = compare("meta", fp_before["meta"], fp_after["meta"])
ok_hist = compare("history", fp_before["history"], fp_after["history"])

# ---------- ⑦ 检索结果一致性 ----------
print("\n⑦ 检索结果是否一致")
st2 = MemoryStore(SRC)
for q in ["密码", "红线", "重载"]:
    res = st2.search(q, top=3)
    print(f"   查询 '{q}': {len(res)} 条")
    for ep, sc, fl in res[:2]:
        print(f"      cos={sc:>5.2f} [{ep.ep_id[:8]}] {ep.raw_text[:40]}")
st2.con.close()

# ---------- 结论 ----------
print("\n" + "=" * 78)
print("结论")
print("=" * 78)

all_ok = ok_ep and ok_vec and ok_meta and ok_hist
print(f"""
  episodes 逐字段: {'✅ 完全一致' if ok_ep else '❌ 有差异'}
  vectors 逐字节:  {'✅ 完全一致' if ok_vec else '❌ 有差异'}
  meta 逐字节:     {'✅ 完全一致' if ok_meta else '❌ 有差异'}
  history 逐字节:  {'✅ 完全一致' if ok_hist else '❌ 有差异'}

  {'✅ 容灾闭环成立：删除原库后从备份恢复，全部字段/向量/元数据 100% 一致' if all_ok else '❌ 存在不一致'}
""")

out = {
    "experiment": "E-370",
    "n_episodes": len(fp_before["episodes"]),
    "n_vectors": len(fp_before["vectors"]),
    "n_meta": len(fp_before["meta"]),
    "n_history": len(fp_before["history"]),
    "episodes_lossless": ok_ep,
    "vectors_lossless": ok_vec,
    "meta_lossless": ok_meta,
    "history_lossless": ok_hist,
    "all_lossless": all_ok,
    "backup_kb": os.path.getsize(bpath) // 1024,
}
p = os.path.join(_HERE, "results", "e370_dr_test.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e370_dr_test.json")
