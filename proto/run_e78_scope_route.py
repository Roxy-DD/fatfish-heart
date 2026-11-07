"""
store v2.0 —— 能力范围矩阵（capability scope matrix）

依据 E-77（一致率 83.3%，我的标注错 3 条）：
  记忆不该标"完整/不完整"，该标"能回答哪类问题"。
  六个维度：existence / identity / time_exact / time_range / value / location

新能力：
  1. 写入时自动推断能力范围（规则版，先用便宜的）
  2. 检索时按问题维度路由：
       问题问 time_exact → 优先返回 time_exact=✅ 的记忆
       若没命中 → 返回"记得有这事，但记不清具体时间"（诚实边界）
  3. 诚实边界：不再把所有记忆都当作能答

红线：沿用 v1.1 全部（迁移 100%、索引可重建、不丢数据、可塑性）
"""
from __future__ import annotations

import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))

DIMS = ["existence", "identity", "time_exact", "time_range", "value", "location"]

# ---------- 问题维度识别 ----------
Q_TIME_EXACT = re.compile(
    r"(哪一?天|哪一?年|几月几?[日号]|几号|什么时候|何时|几点|具体时间|哪年哪月)")
Q_TIME_RANGE = re.compile(
    r"(大概什么时候|多久以前|那阵子|那段时间|什么阶段|多大|哪段时期)")
Q_VALUE = re.compile(r"(多少|几[个位块元条件]|金额|价格|密码|号码|数量|比例|参数)")
Q_LOCATION = re.compile(r"(在哪|哪里|哪儿|什么地方|哪个地方|地址|地点)")
Q_IDENTITY = re.compile(r"(是谁|谁|哪个|哪一个|哪一个|叫什么|名字)")
Q_EXISTENCE = re.compile(r"(有没有|是不是|是否|真的吗|发生过|记不记得|还记得)")


def detect_question_dims(q: str) -> list[str]:
    """判断问题在问哪个维度（可多选）。time_exact 与 time_range 互斥。"""
    t = q or ""
    out = []
    exact = bool(Q_TIME_EXACT.search(t))
    rng = bool(Q_TIME_RANGE.search(t)) or bool(re.search(r"(大概|大约|差不多|左右)", t))
    # 问"大概/大约" → 只算 time_range，不算 exact
    if rng:
        out.append("time_range")
    elif exact:
        out.append("time_exact")
    if Q_VALUE.search(t):
        out.append("value")
    if Q_LOCATION.search(t):
        out.append("location")
    if Q_IDENTITY.search(t):
        out.append("identity")
    if Q_EXISTENCE.search(t):
        out.append("existence")
    return out or ["existence"]


# ---------- 记忆能力范围推断（规则版）----------
EXACT_DATE = re.compile(r"(\d{4}[-/年]\d{1,2}|\d{1,2}月\d{1,2}[日号])")
RANGE_PHRASE = ["小时候", "童年", "上学时", "大学时", "刚工作那会",
                "那年", "那年冬天", "那年夏天"]
DEICTIC_ONLY = re.compile(r"(那阵子|那天|那次|那时候|当时|上次|前几天)")
VALUE_PAT = re.compile(
    r"(\d{2,}[\u4e00-\u9fa5]{0,2}|密码|号码|金额|价格|数量|比例|参数|地址|号)")
LOC_PAT = re.compile(
    r"([\u4e00-\u9fa5]{2,6}(?:路|街|巷|道|大道)\d*号?|"
    r"(\d+)\s*楼|[东西南北]?[城市]?(?:医院|学校|公司|会议室|办公室|商场))")
PROPER = re.compile(
    r"([\u4e00-\u9fa5]{2,4}(?:工|总|经理|老师|医生|师傅|教授))|"
    r"([\u4e00-\u9fa5]{2,3}(?:的公司|的项目))")
EVENT_WORDS = ["去世", "丢了", "被", "第一次", "否决", "同意", "上线",
               "通宵", "吵架", "生病", "住院", "分手", "迟到", "忘了"]


def infer_scope(text: str) -> dict:
    """
    推断这条记忆能回答哪些维度。
    规则：宁可保守（不乱标能答），因为标错"能答"会导致模型自信地编。
    """
    t = text or ""
    can = {}

    # existence：有明确事件动词 → 能答"有没有发生过"
    can["existence"] = any(w in t for w in EVENT_WORDS) or \
        bool(re.search(r"(了|过)", t))

    # identity：有专名 或 明确主体（我的/我女儿的/我家的）
    can["identity"] = bool(PROPER.search(t)) or \
        bool(re.search(r"(我的|我家的|我女儿|我儿子|我老婆|我老公|妈妈|爸爸|孩子)", t))

    # time_exact：有绝对日期
    can["time_exact"] = bool(EXACT_DATE.search(t))

    # time_range：有人生阶段词（"小时候"）但**不能是纯指代**（"那阵子"）
    has_range_phrase = any(w in t for w in RANGE_PHRASE)
    deictic_only = bool(DEICTIC_ONLY.search(t)) and not can["time_exact"]
    can["time_range"] = has_range_phrase and not deictic_only

    # value：有具体数值（且不是纯时间）
    has_num = bool(re.search(r"\d{2,}|\d\.\d", t))
    stripped = EXACT_DATE.sub("", t)
    stripped = re.sub(r"\d{1,2}点|\d+\s*楼", "", stripped)
    can["value"] = bool(re.search(r"\d{2,}|\d\.\d", stripped)) or \
        bool(re.search(r"(密码|金额|价格|数量)", t))

    # location：有地名/楼/场所
    can["location"] = bool(LOC_PAT.search(t))

    return {k: bool(v) for k, v in can.items()}


class StoreV2:
    """能力范围矩阵版存储（包装 v1.2 的 StoreV12）"""

    def __init__(self, base, v12):
        self.st = base
        self.v12 = v12
        self.scope = {}      # ep_id -> {dim: bool}

    def add(self, text, **kw):
        ep = self.v12.add(text, **kw)
        self.scope[ep.ep_id] = infer_scope(text)
        return ep

    def search(self, query, top=5, respect_scope=False, **kw):
        """
        respect_scope=True 时：
          识别问题维度 → 优先返回该维度=能答 的记忆
          若一条都没有 → 返回 (空, "边界提示")
        """
        base_res = self.v12.search(query, top=top * 2, **kw)
        if not respect_scope:
            return [(ep, sc, None) for ep, sc in base_res[:top]]

        qdims = detect_question_dims(query)
        # 分流：能答全部问到的维度 > 能答部分 > 全不能
        full, partial, none = [], [], []
        for ep, sc in base_res:
            sk = self.scope.get(ep.ep_id, {})
            hit = sum(1 for d in qdims if sk.get(d))
            if hit == len(qdims):
                full.append((ep, sc, sk))
            elif hit > 0:
                partial.append((ep, sc, sk))
            else:
                none.append((ep, sc, sk))

        out = []
        for group in (full, partial):
            for ep, sc, sk in group:
                if len(out) >= top:
                    break
                miss = [d for d in qdims if not sk.get(d)]
                note = None
                if miss:
                    note = f"这条能答{'/'.join(d for d in qdims if sk.get(d))}，" \
                           f"但答不出{'/'.join(miss)}"
                out.append((ep, sc, note))
        return out, qdims, {"full": len(full), "partial": len(partial),
                            "none": len(none)}


# ---------- 测试 ----------
# (记忆, 问题, 该记忆能不能真答这个维度)
CASES = [
    ("爷爷去世了，那阵子我一直在医院和家之间来回跑",
     "我爷爷怎么了？", True),
    ("爷爷去世了，那阵子我一直在医院和家之间来回跑",
     "我爷爷是哪一年去世的？", False),
    ("小时候被邻居家的狗追着咬过，小腿上留了疤",
     "我是被狗咬过吗？", True),
    ("小时候被邻居家的狗追着咬过，小腿上留了疤",
     "被咬是哪年的事？", False),
    ("小时候被邻居家的狗追着咬过，小腿上留了疤",
     "大概什么时候被咬的？", True),
    ("我电脑的登录密码是 8848",
     "我电脑密码是多少？", True),
    ("我电脑的登录密码是 8848",
     "密码是哪天设的？", False),
    ("12月3日下午两点，我和张工在7楼会议室对齐了接口协议",
     "什么时候和张工对的接口？", True),
    ("12月3日下午两点，我和张工在7楼会议室对齐了接口协议",
     "在哪对的接口？", True),
    ("城南路18号是我女儿幼儿园的地址",
     "我女儿幼儿园在哪？", True),
    ("城南路18号是我女儿幼儿园的地址",
     "幼儿园是什么时候搬过去的？", False),
    ("钱包丢了，里面有身份证和所有的卡",
     "我钱包里有什么？", True),
    ("钱包丢了，里面有身份证和所有的卡",
     "钱包是什么时候丢的？", False),
]


def main():
    from store_v1 import MemoryStore
    from run_e71_fields import StoreV12

    print("=" * 96)
    print("E-78 能力范围矩阵：检索时按维度路由")
    print("=" * 96)

    # 1. 先看推断的能力范围是否合理
    texts = sorted(set(c[0] for c in CASES))
    print("\n" + "-" * 96)
    print("推断的能力范围")
    print("-" * 96)
    print(f"{'existence':<11}{'identity':<10}{'time_ex':<9}{'time_rg':<9}"
          f"{'value':<7}{'loc':<6}{'记忆'}")
    print("-" * 96)
    scope_map = {}
    for t in texts:
        s = infer_scope(t)
        scope_map[t] = s
        print(f"{('✓' if s['existence'] else '·'):<11}"
              f"{('✓' if s['identity'] else '·'):<10}"
              f"{('✓' if s['time_exact'] else '·'):<9}"
              f"{('✓' if s['time_range'] else '·'):<9}"
              f"{('✓' if s['value'] else '·'):<7}"
              f"{('✓' if s['location'] else '·'):<6}{t[:34]}")
    print("-" * 96)

    # 2. 维度路由测试（不需要训练，纯用规则做判断）
    print("\n" + "=" * 96)
    print("检索路由测试：该答的答，不该答的老实说不知道")
    print("=" * 96)
    print("-" * 96)
    print(f"{'问题':<34}{'问的维度':<14}{'该答?':<7}{'判能答?':<9}{'一致'}")
    print("-" * 96)

    agree = 0
    wrong = []
    for mem, q, should in CASES:
        s = scope_map[mem]
        qd = detect_question_dims(q)
        # 该记忆能否答这个问题 = 所有问到的维度都得能答
        can = all(s.get(d) for d in qd)
        same = (can == should)
        agree += 1 if same else 0
        if not same:
            wrong.append((mem, q, qd, should, can))
        print(f"{q[:32]:<34}{'/'.join(qd)[:12]:<14}{str(should):<7}"
              f"{str(can):<9}{'✓' if same else '✗'}")
    print("-" * 96)

    n = len(CASES)
    print(f"\n  一致率 {agree}/{n} = {agree/n:.4f}")

    if wrong:
        print("\n不一致：")
        for mem, q, qd, should, can in wrong:
            print(f"  问题: {q}")
            print(f"    记忆: {mem[:50]}")
            print(f"    问的维度 {qd}，我认为{'该' if should else '不该'}答，"
                  f"规则判{'能' if can else '不能'}")

    out = {"experiment": "E-78", "n": n, "agree": agree,
           "agree_rate": round(agree / n, 4),
           "scope_matrix": scope_map,
           "mismatches": [{"memory": m, "question": q, "dims": d,
                           "expected": bool(e), "got": bool(g)}
                          for m, q, d, e, g in wrong]}
    with open(os.path.join(_HERE, "results", "e78_scope_route.json"),
              "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(_HERE, 'results', 'e78_scope_route.json')}")


if __name__ == "__main__":
    main()
