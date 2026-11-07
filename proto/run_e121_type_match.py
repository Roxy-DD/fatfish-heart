"""
E-121 用「槽位类型匹配」修正目标选择

E-120 暴露的问题：
  新句「客户同意把截止延到20号」（日期型）
  A「这个项目 15 号截止」        cos=0.6949（日期型）← 应选
  B「客户的验收标准是延迟低于5ms」 cos=0.7026（性能型）← 被误选

纯余弦/三路/实词重叠都失败，因为 BGE 认为"截止"和"延迟"语义相近，
但它们的**槽位类型完全不同**：一个是日期，一个是性能指标。

新判据：先提取句子里的"值"，判断值类型，再看候选的值类型是否匹配。
  日期型：15号、20号、3月8号、下个月15号
  数值性能型：5ms、0.85、16核64G、两百万
  ...

只有当**值类型匹配**时，才认为是同一槽位的矛盾。
"""
import sys, os, re
sys.path.insert(0, r"C:\Users\xuee\Desktop\肥鱼之心\proto\cosmos_mem")
import numpy as np
from store_v2 import MemoryStore
from store_v1 import Embedder

DB = r"C:\Users\xuee\Desktop\肥鱼之心\proto\e121.db"
for suf in ("", "-wal", "-shm"):
    p = DB + suf
    if os.path.exists(p):
        os.remove(p)

# ---------- 值类型识别 ----------
DATE_PAT = re.compile(r"(\d{1,2}\s*[号日]|\d{1,2}\s*月|\d{1,2}月\d{1,2}|"
                      r"下周[一二三四五六日]|这周|明天|后天|月底|月初|年底)")
PERF_PAT = re.compile(r"(\d+\.?\d*\s*(ms|毫秒|秒|s|核|G|GB|MB|TB|%|倍))")
MONEY_PAT = re.compile(r"(\d+\.?\d*\s*(元|块|万|块钱|RMB|美元))")
COUNT_PAT = re.compile(r"(\d+\.?\d*\s*(个|件|条|台|人|次|天|周|月|年))")
CODE_PAT = re.compile(r"(\d{3,}|[A-Za-z]{2,}\d+|\d+\.?\d*)")


def value_types(text):
    """返回句子里出现的值类型集合"""
    types = set()
    if DATE_PAT.search(text):
        types.add("date")
    if PERF_PAT.search(text):
        types.add("perf")
    if MONEY_PAT.search(text):
        types.add("money")
    if COUNT_PAT.search(text):
        types.add("count")
    # 纯数字但没归入上面 → generic_numeric
    if CODE_PAT.search(text) and not types:
        types.add("numeric")
    if not types:
        types.add("no_value")
    return types


CASES = [
    ("客户同意把截止延到20号", "这个项目 15 号截止",
     "客户的验收标准是延迟低于5ms"),
    ("门锁密码换成了9512", "家里的门锁密码是 3721",
     "我电脑的登录密码是 8848"),
    ("接口参数改成了1.2", "接口参数定的是0.85",
     "这个项目 15 号截止"),
    ("明天上午十点开会", "明天下午三点开会",
     "我要连续吃两周的药"),
]

st = MemoryStore(DB)
emb = Embedder.get()

print("=" * 92)
print("E-121 槽位类型匹配：能否修正目标选择")
print("=" * 92)

# 写入所有涉及的记忆
all_texts = set()
for new, target, distractor in CASES:
    all_texts.add(target)
    all_texts.add(distractor)
ids = {}
for t in sorted(all_texts):
    ep = st.add(t, resolve_conflict=False)
    ids[t] = ep.ep_id

print(f"\n库中写入 {len(ids)} 条候选\n")
print("-" * 92)
print(f"{'新句':<24}{'值类型':<14}{'纯余弦选':<24}{'类型匹配选':<24}{'结果'}")
print("-" * 92)

n_cos_ok = 0
n_type_ok = 0
details = []

for new, target, distractor in CASES:
    new_vt = value_types(new)
    tgt_vt = value_types(target)
    dis_vt = value_types(distractor)

    nv = emb.encode(new)[0]

    # 候选打分：余弦 + 值类型匹配加成
    scored = []
    for txt, eid in ids.items():
        if txt == new:
            continue
        ov = emb.encode(txt)[0]
        cos = float(np.dot(nv, ov))
        vt = value_types(txt)
        # 类型匹配：交集非空 → 加成
        type_match = bool(new_vt & vt) and "no_value" not in (new_vt & vt)
        scored.append({
            "txt": txt, "cos": cos, "vt": vt,
            "type_match": type_match,
            "combined": cos + (0.25 if type_match else 0),
        })
    scored.sort(key=lambda x: -x["cos"])
    cos_pick = scored[0]["txt"]
    cos_ok = (cos_pick == target)
    n_cos_ok += 1 if cos_ok else 0

    scored.sort(key=lambda x: -x["combined"])
    type_pick = scored[0]["txt"]
    type_ok = (type_pick == target)
    n_type_ok += 1 if type_ok else 0

    mark = "✅" if type_ok else "❌"
    print(f"{new[:22]:<24}{'/'.join(sorted(new_vt)):<14}"
          f"{cos_pick[:22]:<24}{type_pick[:22]:<24}{mark}")

    details.append({
        "new": new, "target": target, "distractor": distractor,
        "new_types": sorted(new_vt), "target_types": sorted(tgt_vt),
        "distractor_types": sorted(dis_vt),
        "cos_pick": cos_pick, "cos_ok": cos_ok,
        "type_pick": type_pick, "type_ok": type_ok,
    })

print("-" * 92)
n = len(CASES)
print(f"\n  纯余弦选对:   {n_cos_ok}/{n}")
print(f"  类型匹配选对: {n_type_ok}/{n}")
print(f"  提升: {n_type_ok - n_cos_ok}")

# ---------- 明细 ----------
print("\n" + "=" * 92)
print("明细")
print("=" * 92)
for d in details:
    print(f"\n新句: {d['new']}")
    print(f"  新句值类型: {d['new_types']}")
    print(f"  正确目标:   {d['target'][:36]}  类型={d['target_types']}")
    print(f"  干扰项:     {d['distractor'][:36]}  类型={d['distractor_types']}")
    print(f"  纯余弦选:   {d['cos_pick'][:36]}  {'✅' if d['cos_ok'] else '❌'}")
    print(f"  类型匹配选: {d['type_pick'][:36]}  {'✅' if d['type_ok'] else '❌'}")

out = {"experiment": "E-121", "n": n,
       "cosine_accuracy": round(n_cos_ok / n, 4),
       "type_match_accuracy": round(n_type_ok / n, 4),
       "details": details}
import json
with open(os.path.join(os.path.dirname(DB), "..", "results", "e121_type_match.json"),
          "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 results/e121_type_match.json")
