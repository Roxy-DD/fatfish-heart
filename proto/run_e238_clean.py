"""E-238 清理测试污染 + 建立"测试隔离"机制

E-237 诊断暴露的真问题：
  两条相似度 0.932 的"版本故障记录"躺在生产库里，
  来源是我做 E-234 迁移测试时写进去的。

  → 测试污染了生产记忆，这是严重问题：
    · 影响检索质量（假记忆挤占结果）
    · 影响信任（记忆里混进了不该有的东西）

本实验两件事：
  ① 清理现有测试污染
  ② 建立机制：测试必须用独立 DB，绝不碰生产库
"""
import sys
import os
import json
import shutil
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))

from store_v2 import MemoryStore
import hook as _hook

DB = os.path.join(_HERE, "cosmos_mem", "dsh_mem.db")
WORK = os.path.join(_HERE, "work")

# ---------- 备份 ----------
ts = time.strftime("%Y%m%d_%H%M%S")
backup = os.path.join(WORK, f"dsh_mem_before_clean_{ts}.db")
os.makedirs(WORK, exist_ok=True)
shutil.copy2(DB, backup)
print(f"① 备份 → {os.path.basename(backup)}")

st = MemoryStore(DB)

# ---------- 识别测试污染 ----------
print("\n② 识别测试污染")
rows = st.con.execute(
    "SELECT ep_id, raw_text FROM episodes WHERE invalid_at IS NULL "
    "ORDER BY created_at").fetchall()

# 测试污染的典型特征
TEST_MARKERS = [
    "版本故障记录",            # E-234 迁移测试写入
    "模拟：",                  # 测试用 reason
    "极端测试",                # 测试说明
]

polluted = []
for eid, txt in rows:
    if any(m in txt for m in TEST_MARKERS):
        polluted.append((eid, txt))

print(f"   发现 {len(polluted)} 条测试污染：")
for eid, txt in polluted:
    print(f"     · {eid[:8]} {txt[:70]}")

# ---------- 清理 ----------
print("\n③ 清理（R2：只标记失效，不删除）")
n = 0
for eid, txt in polluted:
    # 先删 hook 保护（如果有），再失效
    st.con.execute("DELETE FROM meta WHERE key=?", (f"hook:{eid}",))
    if st.invalidate(eid):
        n += 1
st.con.commit()
st.invalidate_vector_cache()
print(f"   已失效 {n} 条")

# ---------- 验证清理结果 ----------
print("\n④ 验证")
rows2 = st.con.execute(
    "SELECT raw_text FROM episodes WHERE invalid_at IS NULL").fetchall()
remaining = [r[0] for r in rows2 if any(m in r[0] for m in TEST_MARKERS)]
print(f"   残留测试内容: {len(remaining)} 条")
stats = st.stats()
print(f"   有效记忆: {stats['valid']} 条")

# ---------- 检查还有无其他可疑内容 ----------
print("\n⑤ 其他可疑内容扫描")
suspicious = {
    "测试": [], "示例": [], "demo": [], "test": [], "lorem": [],
    "foo": [], "bar": [], "TODO": [], "占位": [],
}
for txt in [r[0] for r in rows2]:
    low = txt.lower()
    for k in suspicious:
        if k.lower() in low:
            suspicious[k].append(txt[:50])
            break

found = {k: v for k, v in suspicious.items() if v}
if found:
    print(f"   发现 {len(found)} 类可疑内容：")
    for k, v in found.items():
        print(f"     [{k}] {len(v)} 条: {v[0][:60]}")
else:
    print("   ✅ 无其他可疑内容")

st.con.close()

# ---------- 建立测试隔离机制 ----------
print("\n⑥ 建立测试隔离机制")
iso_dir = os.path.join(WORK, "test_db")
os.makedirs(iso_dir, exist_ok=True)

helper = os.path.join(_HERE, "_test_isolate.py")
with open(helper, "w", encoding="utf-8") as f:
    f.write('''"""测试隔离：确保测试永远不碰生产 DB

用法：
    from _test_isolate import test_store
    st = test_store("e999")   # 返回独立 DB 的 store，自动清理
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))

_PROD_DB = os.path.join(_HERE, "cosmos_mem", "dsh_mem.db")
_TEST_DIR = os.path.join(_HERE, "work", "test_db")


def test_store(name: str, clean: bool = True):
    """创建隔离的测试 store（绝不影响生产库）"""
    from store_v2 import MemoryStore
    os.makedirs(_TEST_DIR, exist_ok=True)
    db = os.path.join(_TEST_DIR, f"{name}.db")
    if clean:
        for suf in ("", "-wal", "-shm"):
            p = db + suf
            if os.path.exists(p):
                os.remove(p)
    st = MemoryStore(db)
    # 双重保险：确认不是生产库
    assert os.path.abspath(st.path) != os.path.abspath(_PROD_DB), \\
        "测试不能使用生产库！"
    return st


def cleanup(name: str):
    db = os.path.join(_TEST_DIR, f"{name}.db")
    for suf in ("", "-wal", "-shm"):
        p = db + suf
        if os.path.exists(p):
            os.remove(p)
''')
print(f"   已创建 {os.path.basename(helper)}")
print("   用法: from _test_isolate import test_store; st = test_store('e999')")

print(f"\n{'='*66}")
print(f"完成：清理 {n} 条污染，有效记忆 {stats['valid']} 条")
print(f"备份: {os.path.basename(backup)}")
print("=" * 66)
