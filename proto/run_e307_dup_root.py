"""E-307 查根因：为什么同一件事被记了多次？

E-306 发现：
  seq=173/178  同一件事（AGI 笔记）记了 2 次
  seq=174/179  同一件事（自重启）记了 2 次
  seq=170/175/180  同一件事（耗token）记了 3 次

而且：
  · 完全重复检查是 0 组（因为措辞略有不同）
  · 说明 autoRemember 的去重失效了

可能原因：
  a) 去重用的是 md5(规范化文本)，措辞不同就绕过了
  b) 每轮对话都跑一次 autoRemember，同一话题在多轮里被反复识别
  c) lifecycle 让回合变多了 → autoRemember 触发次数也变多
"""
import os
import sys
import json
import time
import hashlib

HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(HERE, "cosmos_mem")
sys.path.insert(0, MEM)

from store_v2 import MemoryStore
import audit as _audit

DB = os.path.join(MEM, "dsh_mem.db")
st = MemoryStore(DB)

print("=" * 76)
print("E-307 查 autoRemember 重复记录根因")
print("=" * 76)

# ============ ① 找出近义重复 ============
print("\n① 近义重复检测（措辞不同但意思相同）")

rows = st.con.execute(
    "SELECT ep_id, raw_text, created_at FROM episodes "
    "WHERE invalid_at IS NULL ORDER BY created_at DESC LIMIT 60").fetchall()

# 用简单的字符集合相似度
def similarity(a, b):
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


groups = []
used = set()
for i in range(len(rows)):
    if i in used:
        continue
    eid1, t1, c1 = rows[i]
    group = [(eid1, t1, c1)]
    for j in range(i + 1, len(rows)):
        if j in used:
            continue
        eid2, t2, c2 = rows[j]
        if similarity(t1, t2) > 0.55:
            group.append((eid2, t2, c2))
            used.add(j)
    if len(group) > 1:
        used.add(i)
        groups.append(group)

print(f"\n   发现 {len(groups)} 组近义重复")
for gi, g in enumerate(groups[:8], 1):
    print(f"\n   --- 组 {gi}（{len(g)} 条）---")
    for eid, txt, cat in g:
        t = time.strftime("%H:%M", time.localtime(cat)) if cat else "?"
        print(f"     [{t}] [{eid[:8]}] {txt[:90]}")

# ============ ② 看 autoRemember 的触发频率 ============
print("\n② autoRemember 触发频率")

rem = st.con.execute(
    "SELECT seq, at, msg FROM audit_log WHERE kind='remember' "
    "ORDER BY seq DESC LIMIT 40").fetchall()

print(f"   最近 40 条 remember 的时间分布:")
times = []
for seq, at, msg in rem:
    if at:
        times.append(time.strftime("%H:%M:%S", time.localtime(at)))

# 按分钟聚合
from collections import Counter
if times:
    mins = Counter(t[:5] for t in times)  # HH:MM
    print(f"\n   {'分钟':<8} {'次数':>4}")
    for m, c in sorted(mins.items(), reverse=True)[:15]:
        bar = "█" * min(c, 20)
        print(f"   {m:<8} {c:>4}  {bar}")

# ============ ③ 检查去重逻辑 ============
print("\n③ 当前去重逻辑")

import cli_mcp
import inspect
src = inspect.getsource(cli_mcp.cmd_add)

# 找去重相关
for line in src.split("\n"):
    if any(k in line for k in ["md5", "text_hash", "dup", "已存在相同"]):
        print(f"   {line.strip()[:90]}")

# ============ ④ 检查插件侧的 autoRemember ============
print("\n④ 插件侧 autoRemember 的实现")

plugin = os.path.join(os.path.dirname(HERE), "dsh-plugin", "index.js")
with open(plugin, "r", encoding="utf-8") as f:
    psrc = f.read()

# 找去重 hash
idx = psrc.find("autoRemember")
if idx > 0:
    seg = psrc[max(0, idx - 300):idx + 1500]
    for line in seg.split("\n"):
        if any(k in line for k in ["createHash", "processed", "md5", "hash", "Set"]):
            print(f"   {line.strip()[:95]}")

# ============ ⑤ 关键判断 ============
print("\n⑤ 关键判断")

# 检查 processed Set 是否在重载后丢失
print("""
   如果 processed Set 是模块级的（`const sessions = new Map()`），
   重载后会被重置 → 同一批消息会被重新识别 → 重复记录。

   这正是 E-254 那个教训的另一面：
     模块级状态跨 apply() 保持 → 重载后状态错乱
""")

# 看 sessions 声明
import re
m = re.search(r"const sessions = new Map\(\)", psrc)
if m:
    print("   ⚠ 发现：const sessions = new Map() —— 模块级！")
    print("     重载后 processed 集合会被重置")
    print("     → 同一批 user message 会被重新处理 → 重复记忆")
else:
    print("   sessions 不是模块级 Map")

# 检查 processed 的 key
m2 = re.search(r"processed\.add\(([^)]+)\)", psrc)
if m2:
    print(f"   processed.add 的参数: {m2.group(1)}")

# 看 hash 的计算方式
m3 = re.search(
    r"const h = createHash\('md5'\)([\s\S]{0,300}?)\.digest\('hex'\)", psrc)
if m3:
    print(f"   hash 计算:")
    for line in m3.group(0).split("\n"):
        print(f"     {line.strip()[:85]}")

# ============ 结论 ============
print("\n" + "=" * 76)
print("结论")
print("=" * 76)

print(f"""
  近义重复组: {len(groups)}
  有效记忆: {st.stats()['valid']}
  最近 remember 条数: {len(rem)}
""")

st.con.close()

out = {
    "experiment": "E-307",
    "near_dup_groups": len(groups),
    "groups": [
        [{"ep_id": e, "text": t[:60], "at": c} for e, t, c in g]
        for g in groups[:10]
    ],
}
p = os.path.join(HERE, "results", "e307_dup_root.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e307_dup_root.json")
