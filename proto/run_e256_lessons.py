"""E-256 把三次前端崩溃的教训写入记忆 + 检查是否还有同类隐患

用户确认界面已正常。

三次同类崩溃（E-253 / E-254 / E-255）都是我自己改出来的：
  · E-253 renderTab 定义在 MemoryPage 外部 → 闭包变量访问不到
  · E-254 模块级 REGISTERED_ROUTES → 宿主重建 webServer 后全部 skip
  · E-255 回调参数 h 遮蔽 React.createElement（两处）

共同特征：
  1. node -c 语法检查通过
  2. 代码看起来"合理"
  3. 只在运行时炸

本实验：
  ① 把教训写入记忆（供未来读取）
  ② 检查是否还有未覆盖的同类隐患
"""
import sys
import os
import json

_HERE = os.path.dirname(os.path.abspath(__file__))
_MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, _MEM)

from store_v2 import MemoryStore

DB = os.path.join(_MEM, "dsh_mem.db")
st = MemoryStore(DB)

print("=" * 74)
print("E-256 记录教训 + 排查剩余隐患")
print("=" * 74)

# ============ ① 写入记忆 ============
print("\n① 把教训写入记忆")

lessons = [
    "前端崩溃教训（E-253/254/255 三次连续）：node -c 语法通过不等于运行时正确。"
    "三次崩溃分别是：①renderTab 定义在 MemoryPage 外部，访问不到闭包变量；"
    "②模块级 REGISTERED_ROUTES 去重集合，在宿主重建 webServer 后导致全部 skip；"
    "③回调参数 h 遮蔽了 React.createElement。"
    "已建立 check-client.mjs 常驻检查（5 项：h 遮蔽/作用域/组件定义/样式/分支完整），"
    "并入 iterate.py 回归套件第 7 项。以后改 client.js 必须先跑它。",

    "插件热重载的铁律：凡是跨 apply() 调用要保持的状态，必须绑定到实例（ctx），"
    "不能放在模块级。因为模块可能被缓存不重新初始化，而宿主会重建 webServer，"
    "两者错位就会导致「记录说已注册、实际却是空的」。"
    "E-254 就是用 WeakMap 按 ctx 隔离修好的。",

    "React.createElement 别名 h 是极短的名字，容易被 .map((h, i) => ...) 这类"
    "回调参数撞上。撞上后 h('div', ...) 会变成把数据对象当函数调用，"
    "抛 TypeError 导致整个面板白屏。检查规则：h 只能作为 createElement 的别名出现。",
]

added = []
for t in lessons:
    dup = st.con.execute(
        "SELECT ep_id FROM episodes WHERE invalid_at IS NULL AND raw_text=?",
        (t,)).fetchone()
    if dup:
        print(f"   （已存在，跳过）{t[:40]}...")
        continue
    ep = st.add(t, resolve_conflict=False)
    added.append(ep.ep_id)
    print(f"   ✅ {t[:50]}...")

# ============ ② 排查剩余隐患 ============
print("\n② 排查剩余同类隐患")

# 检查 index.js 是否还有模块级可变状态
plugin = os.path.join(os.path.dirname(_HERE), "dsh-plugin", "index.js")
src = open(plugin, encoding="utf-8").read()

risks = []

# 模块级 let/var（可变）
import re
for m in re.finditer(r"^(?:const|let|var)\s+(\w+)\s*=", src, re.M):
    name = m.group(1)
    # const 的对象/Map/Set 也算可变
    line_no = src[:m.start()].count("\n") + 1
    line = src.split("\n")[line_no - 1].strip()
    if any(k in line for k in ["new Map", "new Set", "new WeakMap", "= {", "= ["]):
        # 排除不可变配置
        if name not in ("DEFAULTS", "inject", "name"):
            risks.append({
                "name": name, "line": line_no,
                "code": line[:70],
                "risk": "模块级可变状态，热重载时可能错位",
            })

if risks:
    print(f"   发现 {len(risks)} 处模块级可变状态：")
    for r in risks:
        print(f"     [行 {r['line']}] {r['name']}")
        print(f"        {r['code']}")
        print(f"        ⚠ {r['risk']}")
else:
    print("   ✅ 无模块级可变状态风险")

# ============ ③ 验证前端检查覆盖度 ============
print("\n③ 验证 check-client.mjs 覆盖度")
check_script = os.path.join(_HERE, "check-client.mjs")
csrc = open(check_script, encoding="utf-8").read() if os.path.exists(check_script) else ""

checks = {
    "h 遮蔽检查": "SHADOW_RE" in csrc,
    "作用域检查": "findFunctionRange" in csrc,
    "组件定义检查": "使用了未定义的组件" in csrc,
    "样式定义检查": "引用了未定义的样式" in csrc,
    "分支完整性检查": "renderTab 缺少" in csrc,
}
for k, v in checks.items():
    print(f"   {'✅' if v else '❌'} {k}")

st.con.close()

print("\n" + "=" * 74)
print("结论")
print("=" * 74)
print(f"""
  记忆写入: {len(added)} 条
  模块级可变状态风险: {len(risks)} 处
  前端检查覆盖: {sum(checks.values())}/{len(checks)} 项
""")

out = {
    "experiment": "E-256",
    "memories_added": len(added),
    "module_level_risks": risks,
    "check_coverage": checks,
}
p = os.path.join(_HERE, "results", "e256_lessons.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e256_lessons.json")
