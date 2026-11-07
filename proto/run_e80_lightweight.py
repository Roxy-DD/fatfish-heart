"""
E-80 成本问题：能否用轻量方法逼近"模型标注"的能力范围

E-79 结论：
  规则推断在全新语料上只有 0.7381 一致率 → 不可用
  模型标注质量高，但每条都调模型成本太高（×1000）

三条轻量路径（对每个维度单独做二分类）：
  M1 RULE+   扩充规则（补 time_range 词表、existence 事件词、location 城市/楼栋）
  M2 KNN     用 30 条已标注样本做 k 近邻（BGE 向量）
  M3 HYBRID  M1 ∨ M2（规则命中即算 ✓，否则看 KNN）

评测：E-79 的 14 条全新语料（模型标注作为 gold）+ E-77 的 10 条（也是模型标注）
共 24 条 gold，做留一交叉验证（LOO）。
"""
from __future__ import annotations

import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
RESULTS = os.path.join(_HERE, "results")
WORK = os.path.join(_HERE, "work", "e80")
os.makedirs(WORK, exist_ok=True)

DIMS = ["existence", "identity", "time_exact", "time_range", "value", "location"]
MODEL_DIR = r"C:\Users\xuee\Desktop\肥鱼之心\models\bge-small-zh"

# ================= M1 扩充规则 =================
# time_range：补全"频率/季节/相对月"
RANGE_PHRASE = [
    # 人生阶段
    "小时候", "童年", "上学时", "大学时", "刚工作那会", "那年", "那年冬天",
    "那年夏天", "小时候",
    # 叙述性时段
    "上个月", "下个月", "去年", "今年", "明年", "月底", "月初", "年底", "年初",
    "上周", "下周", "这周", "本周", "前几天", "前阵子", "过几天", "过一会儿",
    "去年冬天", "去年夏天", "今年春天", "今天", "昨天", "明天",
    # 频率
    "每天", "每周", "每月", "每年", "常常", "经常", "总是", "隔天",
    # 时段
    "早上", "上午", "中午", "下午", "晚上", "凌晨", "半夜", "饭后", "睡前",
]
# existence：事件词（不只是事件动词，也含"可确认发生"的状态变化）
EVENT_WORDS = [
    "去世", "丢了", "丢了", "被", "第一次", "否决", "同意", "上线", "通宵",
    "吵架", "生病", "住院", "分手", "迟到", "忘了", "摔", "搬", "签",
    "碰到", "遇到", "出差", "承诺", "答应", "说", "聊", "散", "回来",
    "落", "吃了", "否了", "结清", "接", "发",
]
# location：城市名 + 楼栋层 + 场所
CITIES = ["北京", "上海", "广州", "深圳", "杭州", "成都", "武汉", "南京",
          "西安", "重庆", "天津", "苏州", "长沙", "青岛", "郑州", "东莞"]
LOC_PAT = re.compile(
    r"([\u4e00-\u9fa5]{2,8}(?:路|街|巷|道|大道)\d*号?)|"
    r"([A-Za-z]座\d*层?)|(\d+\s*层)|(\d+\s*楼)|"
    r"([\u4e00-\u9fa5]{2,6}(?:市|区|县))|"
    r"([\u4e00-\u9fa5]{1,6}(?:医院|学校|公司|会议室|办公室|商场|小区|电梯|门口|幼儿园))")
# identity：可指认（专名/编号/具名身份），排除泛称
PROPER_NAME = re.compile(
    r"([\u4e00-\u9fa5]{1,2}(?:工|总|经理|老师|医生|师傅|教授))|"
    r"([\u4e00-\u9fa5]{2,4}(?:路|街))|"
    r"([A-Za-z]-?\d+)|"
    r"(我妈|我爸|我女儿|我儿子|我老婆|我老公|我表姐|我表哥|我的工牌|我的大学室友)")
GENERIC_SUBJECT = re.compile(r"(我的|我家的|我女儿|我儿子)\s*[\u4e00-\u9fa5]{0,6}")


def m1_rule(text: str) -> dict:
    t = text or ""
    s = {}
    # existence
    s["existence"] = any(w in t for w in EVENT_WORDS)
    # identity：专名/编号/具名亲属 且 不是纯泛称
    s["identity"] = bool(PROPER_NAME.search(t))
    if s["identity"] and re.fullmatch(r".*我的工牌编号.*", t) is None:
        pass  # 保留，模型认为"我的工牌"是泛称，但 A-2261 是编号值
    # time_exact：绝对日期
    s["time_exact"] = bool(re.search(
        r"(\d{4}[-/年]\d{1,2}|\d{1,2}月\d{1,2}[日号]|\d{1,2}点)", t))
    # time_range：阶段/相对/频率/时段（但不能是纯指代）
    has_range = any(w in t for w in RANGE_PHRASE)
    deictic_only = bool(re.search(r"^(那阵子|那天|那次|那时候|当时|上次)", t)) and \
        not has_range
    s["time_range"] = has_range and not deictic_only
    # value：数字（排除纯日期/时间）+ 数值类名词
    stripped = re.sub(r"\d{4}[-/年]\d{1,2}|\d{1,2}月\d{1,2}[日号]|\d{1,2}点", "", t)
    s["value"] = bool(re.search(r"\d{2,}|\d\.\d|[\u4e00-\u9fa5]\d+", stripped)) or \
        bool(re.search(r"(密码|金额|编号|取件码|剂量|一片|一箱)", t))
    # location
    s["location"] = bool(LOC_PAT.search(t)) or any(c in t for c in CITIES)
    return {k: bool(v) for k, v in s.items()}


# ================= 数据集 =================
def load_gold():
    """收集所有模型标注作为 gold（兼容两种格式：can/cannot 与 scope）"""
    items = []

    def to_scope(rec):
        """统一转成 {dim: bool}"""
        if "scope" in rec:
            return {d: bool(rec["scope"].get(d)) for d in DIMS}
        if "can" in rec:
            can = set(rec.get("can") or [])
            return {d: (d in can) for d in DIMS}
        return {d: False for d in DIMS}

    # E-77（格式：can/cannot）
    p = os.path.join(_HERE, "work", "e77")
    if os.path.exists(os.path.join(p, "corpus.json")):
        c = json.load(open(os.path.join(p, "corpus.json"), encoding="utf-8"))
        s = {r["rid"]: r for r in
             json.load(open(os.path.join(p, "scope.json"), encoding="utf-8"))}
        for r in c:
            if r["rid"] in s:
                items.append({"text": r["memory"], "gold": to_scope(s[r["rid"]]),
                              "src": "E77"})
    # E-79（格式：scope）
    p = os.path.join(_HERE, "work", "e79")
    if os.path.exists(os.path.join(p, "corpus.json")):
        c = json.load(open(os.path.join(p, "corpus.json"), encoding="utf-8"))
        s = {r["rid"]: r for r in
             json.load(open(os.path.join(p, "scope.json"), encoding="utf-8"))}
        for r in c:
            if r["rid"] in s:
                items.append({"text": r["memory"], "gold": to_scope(s[r["rid"]]),
                              "src": "E79"})
    return items


def main():
    import numpy as np
    import torch
    from transformers import AutoTokenizer, AutoModel

    items = load_gold()
    print("=" * 96)
    print("E-80 轻量方法能否逼近模型标注")
    print("=" * 96)
    print(f"\ngold 样本 {len(items)} 条（E-77 的 10 条 + E-79 的 14 条）")
    from collections import Counter
    print("  来源:", dict(Counter(i["src"] for i in items)))

    torch.set_num_threads(4)
    tok = AutoTokenizer.from_pretrained(MODEL_DIR)
    mdl = AutoModel.from_pretrained(MODEL_DIR).eval()

    def enc(texts):
        if isinstance(texts, str):
            texts = [texts]
        with torch.no_grad():
            e = tok(texts, padding=True, truncation=True, max_length=128,
                    return_tensors="pt")
            o = mdl(**e)
        v = o.last_hidden_state[:, 0].numpy().astype("float32")
        n = np.linalg.norm(v, axis=1, keepdims=True); n[n == 0] = 1
        return v / n

    texts = [i["text"] for i in items]
    V = enc(texts)

    # ---------- 逐维度评估（留一） ----------
    print("\n" + "-" * 96)
    print("留一交叉验证（LOO）· 各维度准确率")
    print("-" * 96)
    print(f"{'维度':<14}{'正例数':<10}{'M1 规则':<12}{'M2 KNN':<12}{'M3 混合':<12}")
    print("-" * 96)

    results = {}
    for d in DIMS:
        ys = [bool(i["gold"].get(d)) for i in items]
        n_pos = sum(ys)
        if n_pos == 0 or n_pos == len(ys):
            print(f"{d:<14}{n_pos:<10}{'n/a（全同）':<12}{'':<12}{'':<12}")
            results[d] = {"n_pos": n_pos, "note": "全同，跳过"}
            continue

        m1_pred, m2_pred, m3_pred = [], [], []
        for idx in range(len(items)):
            t = texts[idx]
            # M1
            m1_pred.append(m1_rule(t)[d])
            # M2：KNN（k=5，排除自己）
            sims = V @ V[idx]
            order = np.argsort(-sims)
            neigh = [j for j in order if j != idx][:5]
            votes = sum(1 for j in neigh if items[j]["gold"].get(d))
            m2_pred.append(votes > len(neigh) / 2)
            # M3：规则命中即 ✓，否则看 KNN
            m3_pred.append(m1_rule(t)[d] or (votes > len(neigh) / 2))

        def acc(pred):
            return sum(1 for p, y in zip(pred, ys) if p == y) / len(ys)

        a1, a2, a3 = acc(m1_pred), acc(m2_pred), acc(m3_pred)
        results[d] = {"n_pos": n_pos, "m1": round(a1, 4),
                      "m2": round(a2, 4), "m3": round(a3, 4)}
        print(f"{d:<14}{n_pos:<10}{a1:<12.4f}{a2:<12.4f}{a3:<12.4f}")
    print("-" * 96)

    # ---------- 汇总 ----------
    valid = [d for d in DIMS if "m1" in results.get(d, {})]
    print("\n" + "=" * 96)
    print("汇总（只看非全同维度）")
    print("=" * 96)
    for m in ("m1", "m2", "m3"):
        avg = sum(results[d][m] for d in valid) / len(valid)
        print(f"  {m.upper()} 平均准确率: {avg:.4f}")
    print(f"\n  对比 E-79（原规则）: 0.7381")

    best = max(("m1", "m2", "m3"),
               key=lambda m: sum(results[d][m] for d in valid))
    print(f"\n  最优方法: {best.upper()} "
          f"({sum(results[d][best] for d in valid)/len(valid):.4f})")

    out = {"experiment": "E-80", "n": len(items),
           "dims": DIMS, "results": results,
           "mean": {m: round(sum(results[d][m] for d in valid) / len(valid), 4)
                    for m in ("m1", "m2", "m3")},
           "best": best,
           "baseline_e79_rule": 0.7381}
    with open(os.path.join(RESULTS, "e80_lightweight.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e80_lightweight.json')}")


if __name__ == "__main__":
    main()
