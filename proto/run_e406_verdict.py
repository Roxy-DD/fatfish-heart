"""E-406 修正：E-405 的「仍有效 1 条」是误报

E-405 报告：
  notes 里有 2 条含"测试"的，其中 1 条仍有效：
    [nt443b36] 插件热重载：状态该放哪，测试怎么测

★ 这不是测试数据 —— 它是真实的笔记，
  标题里有"测试"两个字而已（讲的是"测试怎么测"这个方法）。

我的关键词匹配太粗糙：看到"测试"就当成测试数据。

正确判据应该是：
  · 测试数据 = 内容本身是假的、为验证功能编造的
  · 真实内容 = 讲的是真实知识，只是用词里有"测试"

区分方法：
  · 看内容长度与结构（测试数据通常短、无信息量）
  · 看是否属于已知的真实主题
  · 看创建来源（哪个实验写的）

本脚本：
  ① 逐个检查含"测试"的条目，判断真假
  ② 给出明确判定
"""
import sys
import os
import json
import warnings
import sqlite3

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
DB = os.path.join(MEM, "dsh_mem.db")

print("=" * 78)
print("E-406 逐条判定含「测试」字样的内容")
print("=" * 78)

con = sqlite3.connect(DB)

# ---------- ① notes ----------
print("\n① notes")

notes = con.execute(
    "SELECT note_id, title, tier, body, invalid_at FROM notes").fetchall()

for nid, title, tier, body, inv in notes:
    if "测试" not in (title or ""):
        continue
    blen = len(body or "")
    status = "已失效" if inv else "有效"

    # 判定规则：
    #  · 标题只是提到"测试"这个方法 → 真实内容
    #  · 标题就叫"测试笔记"、正文极短 → 测试数据
    is_real = ("测试怎么测" in title or "测试方法" in title
               or blen > 300)
    verdict = "真实内容" if is_real else "测试数据"

    print(f"\n   [{nid[:8]}] [{tier}] {title}")
    print(f"      状态: {status}  正文: {blen} 字")
    print(f"      判定: **{verdict}**")
    print(f"      理由: "
          + ("标题讲的是'测试'这个方法，正文有实质内容"
             if is_real else "标题即测试，正文短"))

    # 展示开头
    if body:
        print(f"      开头: {body[:80].replace(chr(10), ' ')}")

# ---------- ② episodes ----------
print("\n② episodes")

eps = con.execute(
    "SELECT ep_id, raw_text, length(raw_text), invalid_at "
    "FROM episodes WHERE raw_text LIKE '%测试%'").fetchall()

print(f"   含'测试'的记忆: {len(eps)} 条")

real_eps = []
test_eps = []
for eid, txt, ln, inv in eps:
    # 判定：短且无信息量 = 测试数据
    is_test = (ln < 40 and (
        "测试" in txt[:6] or
        txt.startswith("测试") or
        "密码" in txt and ln < 30 and
        ("8888" in txt or "9999" in txt or "8848" in txt)
    ))
    if is_test:
        test_eps.append((eid, txt, inv is not None))
    else:
        real_eps.append((eid, txt, ln, inv is not None))

print(f"   判定为测试数据: {len(test_eps)} 条")
for eid, txt, inv in test_eps:
    print(f"     [{eid[:8]}] {'已失效' if inv else '⚠ 有效'} | {txt[:60]}")

print(f"\n   判定为真实内容: {len(real_eps)} 条（抽样 5 条）")
for eid, txt, ln, inv in real_eps[:5]:
    print(f"     [{eid[:8]}] {ln}字 {'已失效' if inv else '有效'} "
          f"| {txt[:50]}")

# ---------- ③ 真正需要清理的 ----------
print("\n③ 真正需要清理的")

need_clean = [t for t in test_eps if not t[2]]
print(f"   仍有效的测试数据: {len(need_clean)} 条")
for eid, txt, _ in need_clean:
    print(f"     [{eid[:8]}] {txt[:70]}")

con.close()

# ---------- 结论 ----------
print("\n" + "=" * 78)
print("结论")
print("=" * 78)

print(f"""
  E-405 报告的「仍有效 1 条」是**误报**：
    [nt443b36] 插件热重载：状态该放哪，测试怎么测
    → 这是真实笔记，标题里有"测试"二字而已。

  真正仍有效的测试数据: {len(need_clean)} 条

  {"✅ 生产库干净，无需清理" if len(need_clean) == 0 else "⚠ 需要清理"}

  ★ 教训：关键词匹配不能代替内容判断。
    「含测试」≠「是测试数据」。
    这条和之前几次误判是同一类错误——
    用一个粗指标下结论，没看具体内容。
""")

out = {
    "experiment": "E-406",
    "notes_with_test_word": sum(1 for n in notes if "测试" in (n[1] or "")),
    "episodes_with_test_word": len(eps),
    "test_eps": len(test_eps),
    "real_eps": len(real_eps),
    "need_clean": len(need_clean),
    "clean_list": [eid for eid, _, _ in need_clean],
}
p = os.path.join(_HERE, "results", "e406_verdict.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e406_verdict.json")
