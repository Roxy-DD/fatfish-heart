"""E-353 执行记忆整理

E-352 体检结果：
  · 近重复 4 对，但**内容是相关而非重复**，不能盲目合并
  · 过期 1 条（健康基准写的 67 条，实际 91 条）
  · 笔记只有 2 条，方法论/去重/红线等主题未沉淀
  · todos 全空

我的判断（逐个看内容后）：
  ① 那 4 对不合并 —— 它们是独立事实：
     · d1738c8c = 记忆自主权
     · 02c11d5c = 版本迁移能力
     · c42dcee0 = 版本故障回滚
     · 173191cd / 264a0109 = 自观要求（这两条确实高度重复，可合并）
     · cfaddf48 = todo 需求
     结论：只合并 173191cd + 264a0109（真重复），其余保留

  ② 过期那条：不改写原文（R4），而是**新增一条更新后的基准**
     因为"E-269 体检"是历史记录，改写它会失真

  ③ 笔记：沉淀 3 个真正有价值的主题
  ④ todos：重建，反映当前真实待办
"""
import sys
import os
import json
import warnings

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, MEM)

from store_v2 import MemoryStore
import cli_mcp
import backup as bk

DB = os.path.join(MEM, "dsh_mem.db")

print("=" * 78)
print("E-353 执行记忆整理")
print("=" * 78)

# ---------- ① 备份 ----------
print("\n① 备份")
b = bk.backup_db(DB)
print(f"   ok={b.get('ok')} {os.path.basename(str(b.get('path')))}")
if not b.get("ok"):
    raise SystemExit(1)

st = MemoryStore(DB)

# ---------- ② 只在真重复时合并 ----------
print("\n② 合并真重复（173191cd + 264a0109）")

# 这两条都是"自观/第三的他者"要求
r = cli_mcp.cmd_sleep_apply(st, {
    "actions": [{
        "op": "merge",
        "keep_id": "173191cd" + "",
        "drop_id": "264a0109",
    }],
    "advance_cursor": False,
})
# ep_id 是完整的，先查真实的
row = st.con.execute(
    "SELECT ep_id FROM episodes WHERE ep_id LIKE '173191cd%'").fetchone()
row2 = st.con.execute(
    "SELECT ep_id FROM episodes WHERE ep_id LIKE '264a0109%'").fetchone()

if row and row2:
    r = cli_mcp.cmd_sleep_apply(st, {
        "actions": [{"op": "merge", "keep_id": row[0], "drop_id": row2[0]}],
        "advance_cursor": False,
    })
    print(f"   ok={r.get('ok')}")
    print(f"   merged={len(r.get('result', {}).get('merged', []))}")
else:
    print("   未找到目标记忆，跳过")

# ---------- ③ 新增更新后的健康基准（不改原文）----------
print("\n③ 新增更新后的健康基准")

s = st.stats()
bench = (f"系统健康基准（E-352 体检，2026-11-10）：有效记忆 {s['valid']} 条、"
         f"失效 {s['invalid']} 条（R2 只增不删）、向量 {s['vectors']} 个且数量匹配；"
         f"受保护 L0=2 L1=9 L2=5；无 L0/L1 层重复组。"
         f"注：E-269 那条基准（67 条）已过时，保留作为历史记录，"
         f"当前以此条为准。")

r2 = cli_mcp.cmd_add(st, {"text": bench})
print(f"   ok={r2.get('ok')} ep_id={r2.get('ep_id', '')[:8]}")
if r2.get("deduped"):
    print(f"   已存在相同记忆")

# ---------- ④ 沉淀笔记 ----------
print("\n④ 沉淀笔记")

NOTES = [
    {
        "title": "记忆系统的注入链路：三次静默失败",
        "topic": "注入/token",
        "tier": "note",
        "body": """# 记忆系统的注入链路：三次静默失败

这条链路的核心是「把 L0/L1 常驻准则 + 检索到的相关记忆，在正确的时机、
用正确的预算注入模型上下文」。2026-11 连续查出三个**不报错、只是行为悄悄错**的 bug。

## Bug 1：search_scoped 丢掉 flag（最严重）

`cmd_search` 的 scoped 分支返回 `{ep_id, text, score, note}`，**没有 flag**。
而 `search_scoped` 内部返回的是 `(ep, sc, note)` 三元组，note 是字符串，
与 flag(int) 语义完全不同。

插件侧：
```js
protectedItems = allItems.filter(it => it.flag === 2)   // 恒为空
items = allItems.filter(it => it.flag !== 2)            // = 全部
```

**后果：L0/L1「永远在场」这个核心设计从未生效过。**
实测 28 次注入，`protected_count` 全部为 0。

**发现方式**：验证别的修复时，顺手看了注入记录，注意到 protected_count=0 这个
异常数字，然后**把插件实际调用的路径（scoped:true）和直接调用路径各跑一遍对比**。

## Bug 2：L0/L1 占用 top 名额

修 Bug 1 时写成 `forced + full + partial` 然后 `[:top]`。
11 条强制记忆吃光 top=5，普通结果一条进不来。
实测现象：查询「Docker 容器」返回 5 条全是 flag=2 且 cos=0.00。

**正确语义**：普通结果占 top，L0/L1 追加末尾（与 store_v1.search 一致）。

## Bug 3：首轮判断顺序错误

```js
st.injected = true                              // 先置位
...
formatProtected(protectedItems, !st.injected)   // 再读，恒为 false
```

**后果：E-231 设计的「首轮给全文」从未生效**，永远走摘要模式，提示语也说反了。

修：`const isFirstTurn = !st.injected` 先记下来，再置位。

## 共同特征

三个 bug 的共同点：**系统表面上一切正常**——有注入、有日志、不报错，
只是行为悄悄地错了。都只能靠「对比实际数据与设计预期」发现。

## 方法论

1. 验证时**同时跑「插件实际调用的路径」和「直接调用的路径」**做对比
2. 看到「恒为 0 / 恒为空」这类数字要警觉，那通常不是巧合
3. 修 A 时引入 B 很常见，修完要重新跑一遍完整语义检查
""",
    },
    {
        "title": "阈值怎么定：三个实测标定案例",
        "topic": "方法论",
        "tier": "note",
        "body": """# 阈值怎么定：三个实测标定案例

原则：**不预设门槛，遇到什么问题看什么造成效果变差或变好，再确定边界。**

## 案例 1：近重复阈值（E-309）

问题：语义去重该用多少余弦阈值？

方法：构造 10 对「确定是同一件事（措辞不同）」+ 12 对「确定是不同的事」，实测。

结果：
- 同一件事：0.629 ~ 0.916（均值 0.775）
- 不同的事：0.349 ~ 0.532（均值 0.430）
- 分界线：0.580，间隔 0.098

阈值 → 识别率 / 误判率：
| 阈值 | 同件事识别率 | 不同事误判率 |
|---|---|---|
| 0.80 | 50% | 0% | ← 选这个
| 0.85 | 20% | 0% |
| 0.95 | **0%** | 0% | ← 最初拍脑袋的值，等于完全失效

**教训**：0.95 是我凭直觉定的「保守值」，实测证明它让整个功能完全失效。
偏差不是 10%，是 100%。

## 案例 2：注入分数门槛（E-321）

问题：低分检索结果要不要注入？

方法：8 个相关查询 + 5 个不相关查询。

结果：
- 相关查询最高分：1.50 ~ 3.81
- 不相关查询最高分：0.64 ~ 2.35
- 有重叠（「React 组件生命周期」2.35 命中了「DSH 生命周期干预」）

最优阈值 1.1，当前用 1.0（差 0.1 在噪声内）。

**教训**：语义相近但主题不同的查询分数可以很高。单靠分数门槛过滤不干净，
这是语义检索的固有边界，不是调参能解决的。

## 案例 3：合并长度上限（E-312）

问题：记忆合并会不会无限膨胀？

实测：一条记忆被合并 139 次后达到 **6637 字**（同一句话的 139 种说法），
远超注入预算 1500 字。

修：
- 长度上限 1200 字
- 内容去重（相似度 > 0.85 不再追加）
- 超出部分折叠为「另有 N 条近重复已折叠」
- 折叠的原文全部进 history 表（R4 可追溯）

效果：合并 20 次后从 20 倍长度降到 113 字。

## 通用结论

1. **阈值必须用「已知正例 + 已知负例」双向标定**，只测一边必然偏
2. **看多个指标**：E-284 曾因为只看 recall@10（已饱和）而误判优化无效，
   实际 recall@1 +2.9pp、召回 0 退化
3. **零误判优先于高召回**：漏判可以后续补救（睡眠整理时合并），
   误杀则数据永久丢失
""",
    },
    {
        "title": "插件热重载：状态该放哪，测试怎么测",
        "topic": "热重载/插件",
        "tier": "note",
        "body": """# 插件热重载：状态该放哪，测试怎么测

## 铁律：状态归属决定重载后是否错乱

| 状态类型 | 存放位置 | 原因 |
|---|---|---|
| 跨 apply() 要**丢弃**的（路由注册记录） | `WeakMap` 按 ctx 隔离 | 宿主重建 webServer 后旧记录必须失效 |
| 跨 apply() 要**保留**的（已处理消息、会话状态） | `globalThis` | 模块重载后仍要接上 |
| 单次 apply 内部的 | 局部变量 | 最安全 |

违反后果（都真实发生过）：
- 模块级 Set 记路由 → 重载后全部 skip → 所有端点 404（E-254）
- 模块级 Map 存 sessions → 重载后清空 → 同一批消息重复写入记忆（E-307）

## 自重启的真实机制（E-303 A/B 对照）

| 操作 | 代码是否重载 |
|---|---|
| 只改 `cordis.patch.yml` 注释 | ❌ 指纹不变 |
| 改 `index.js` 内容再 reload | ✅ 指纹变化 |

**改 patch 文件只是"敲门"（触发 reconcile），
真正让模块重新加载的是「模块内容变化」（HMR 检测）。**

推论：如果只调 reload 不改代码，它会返回 `ok=true` 但什么都不会发生。

所以实现了 `reloadVerified()`：记录重载前指纹 → 触发 → 等 6 秒 → 读新指纹 →
返回 `reloaded: true/false/null`。**不再有"以为重载了其实是旧代码"的情况。**

## 测试必须防污染

E-300 教训：离线测试**真的改了用户的 `cordis.patch.yml`**
（跑测试前 MD5=AA0F...，跑完 450A...，文件里多了 `__reload_stamp__`）。

原因：测试里 4 处 `sr.reload()` 调用没传 `dryRun`。

修：
- `reloadViaPatchFile` 支持 `opts.dryRun`（只算结果不写盘）
- 所有测试调用都传 `{ dryRun: true }`
- **加「改前改后 MD5 一致」的断言**

**这条比"测试通过"更重要：测试如果会改用户文件，必须有副作用断言。**

## 配置文件的正确改法

原做法：每次 reload 覆盖 `cordis.patch.yml.bak` → 连续 3 次后 .bak 里是第 2 次的
内容，不是原件，写坏无法回滚。

改为：
- 时间戳备份 `cordis.patch.yml.cosmos-bak-<ISO 时间>`
- 最多保留 5 份，自动删最旧
- 只动末尾标记行，正文逐字节不变
- 写完读回校验，不一致则自动回滚
""",
    },
]

for n in NOTES:
    r = cli_mcp.cmd_note(st, {
        "action": "add", "title": n["title"],
        "body": n["body"], "topic": n["topic"], "tier": n["tier"],
    })
    print(f"   {'✅' if r.get('ok') else '❌'} {n['title'][:40]} "
          f"({len(n['body'])} 字)")

# ---------- ⑤ 重建 todos ----------
print("\n⑤ 重建待办清单")

TODOS = [
    ("验证 E-348/350 修复在真实会话中的效果", "high", "task"),
    ("更新架构图（scoped 语义已变）", "mid", "task"),
    ("调研「只注入一次 vs 每轮注入」的对照实验", "high", "task"),
    ("环管群主张4：分支与跨谱系重组", "low", "task"),
    ("环管群主张6：生命-环境共演化", "low", "task"),
    ("待用户裁定：cost/affect_peak 是否违背主张5", "mid", "review"),
    ("待用户裁定：是否做多生命周期并存", "mid", "review"),
]

for title, prio, kind in TODOS:
    p = {"high": 3, "mid": 2, "low": 1}[prio]
    r = cli_mcp.cmd_todo(st, {
        "action": "add", "title": title,
        "priority": p, "kind": kind,
    })
    print(f"   {'✅' if r.get('ok') else '❌'} [{prio}] {title[:50]}")

# ---------- ⑥ 结果 ----------
print("\n⑥ 整理后状态")
s2 = st.stats()
print(f"   有效 {s2['valid']} / 失效 {s2['invalid']}")

notes2 = st.con.execute(
    "SELECT COUNT(*) FROM notes WHERE invalid_at IS NULL").fetchone()[0]
todos2 = st.con.execute(
    "SELECT COUNT(*) FROM todos WHERE invalid_at IS NULL").fetchone()[0]
print(f"   笔记 {notes2} 条")
print(f"   待办 {todos2} 条")

out = {
    "experiment": "E-353",
    "before_valid": s["valid"],
    "after_valid": s2["valid"],
    "notes": notes2,
    "todos": todos2,
}
p = os.path.join(_HERE, "results", "e353_tidy_done.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 results/e353_tidy_done.json")

st.con.close()
