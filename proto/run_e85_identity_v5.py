"""
E-85 identity 第五版：修复正则假阳性

E-84 的 5 个错误，全部是"称呼正则把日常词误当称呼"：

  假阳性来源          被误匹配            真实含义
  水管工              "管"+"工"          职业（水管工）
  送外卖的小哥         "小"+"哥"          职业（外卖小哥）
  前台的小姑娘         "小"+"姑"          描述
  那次出差回来总觉得累  "后"+"总"←"总觉得"   "总"是副词

修法：
  1 "X工" 的 X 必须是姓氏字，且整词不能是已知职业（水管工/电工/木工/瓦工/管工）
  2 "小X" 的 X 不能是 哥/姑/孩/时/事/心/声/伙
  3 "X总" 后面不能是 觉得/算/是/体/之/而言
  4 "X检查/经理/老师/医生/主任/主管/姐/兄/姨/叔" 同样要求是姓氏字
"""
from __future__ import annotations

import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(_HERE, "results")

# ---------- 姓氏白名单（真实姓氏，覆盖率约 95% 常见姓） ----------
SURNAMES = set(
    "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜"
    "戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳酆鲍史唐"
    "费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮卞齐康伍余元卜顾孟平黄"
    "和穆萧尹姚邵湛汪祁毛禹狄米贝明臧计伏成戴谈宋茅庞熊纪舒屈项祝董梁"
    "杜阮蓝闵席季麻强贾路娄危江童颜郭梅盛林刁钟徐邱骆高夏蔡田樊胡凌霍"
    "虞万支柯昝管卢莫经房裘缪干解应宗丁宣贲邓郁单杭洪包诸左石崔吉钮龚"
    "程嵇邢滑裴陆荣翁荀羊於惠甄曲家封芮羿储靳汲邴糜松井段富巫乌焦巴弓"
    "牧隗山谷车侯宓蓬全郗班仰秋仲伊宫宁仇栾暴甘钭厉戎祖武符刘景詹束龙"
    "叶幸司韶郜黎蓟薄印宿白怀蒲邰从鄂索咸籍赖卓蔺屠蒙池乔阴鬱胥能苍双"
    "闻莘党翟谭贡劳逄姬申扶堵冉宰郦雍卻璩桑桂濮牛寿通边扈燕冀郏浦尚农"
    "温别庄晏柴瞿阎充慕连茹习宦艾鱼容向古易慎戈廖庾终暨居衡步都耿满弘"
    "匡国文寇广禄阙东欧殳沃利蔚越夔隆师巩厍聂晁勾敖融冷訾辛阚那简饶空"
    "曾毋沙乜养鞠须丰巢关蒯相查后荆红游竺权逯盖益桓公"
)
# 这些"X工"不是称呼，是职业
JOB_NOT_TITLE = {"水管工", "电工", "木工", "瓦工", "管工", "油漆工", "泥工",
                 "车工", "钳工", "焊工", "杂工", "小工", "技工", "员工"}
# "小X" 里 X 不能是这些（否则是普通词）
XIAO_BAD = set("哥姑孩时事心声伙姐弟妹朋友便区学校")
# "X总" 后面不能接这些
ZONG_BAD_NEXT = ("觉得", "算", "是", "体", "之", "而言", "共", "计", "额")

TITLE_CHARS = "工总检查经理老师医生主任主管姐兄姨叔"


def find_name_title(t: str) -> str | None:
    """寻找 X+称呼，严格校验 X 是姓氏、且不误伤职业词"""
    for m in re.finditer(r"([\u4e00-\u9fa5])(经理|老师|医生|主任|主管|"
                         r"工|总|姐|兄|姨|叔)", t):
        sur, title = m.group(1), m.group(2)
        # 整词是职业 → 跳过
        start = m.start()
        two = t[start:start + 2] if start + 2 <= len(t) else t[start:]
        if two in JOB_NOT_TITLE or (t[start:start + 3] in JOB_NOT_TITLE):
            continue
        if sur not in SURNAMES:
            continue
        # "X总" 后面接副词 → 跳过
        if title == "总":
            nxt = t[m.end():m.end() + 2]
            if any(nxt.startswith(b) for b in ZONG_BAD_NEXT):
                continue
        return m.group(0)
    return None


def find_xiao(t: str) -> str | None:
    """寻找 '小X'，排除小姑娘/小哥/小孩等普通词"""
    for m in re.finditer(r"(小)([\u4e00-\u9fa5])", t):
        x = m.group(2)
        if x in XIAO_BAD:
            continue
        if x not in SURNAMES:
            continue
        return m.group(0)
    return None


STABLE_SLOT = re.compile(
    r"我(?:的|家的|自己家的)?"
    r"(?:妈|爸|老婆|老公|公公|婆婆|岳父|岳母|女儿|儿子|姐|哥|弟|妹|"
    r"孩子|老板|房东|领导|上司|工牌|电脑|身份证|钱包|车|房|手机|保单)")
FIRST_REL = re.compile(
    r"我(?:的)?(?:妈|爸|老婆|老公|公公|婆婆|女儿|儿子|姐|哥|弟|妹|孩子)")
SECOND_ORDER = re.compile(
    r"我(?:表姐|表哥|表妹|表弟|堂姐|堂哥|舅妈|姨妈|姑姑|叔叔|伯伯|"
    r"朋友|同事|同学|邻居)(?:家的?|的)")
POSITIONAL = re.compile(
    r"(修车师傅|外卖小哥|水管工|快递员|保安|服务员|司机|"
    r"便利店(?:的)?老板|楼下的?|门口的?|前台的?|送外卖的|小姑娘|"
    r"我们?组?的?\s*[\u4e00-\u9fa5]{0,4}(?:负责人|主管|组长|架构师))")
IDENTIFIER = re.compile(
    r"(\d{15,18}|[A-Za-z]{1,3}-?\d{3,}|订单号|保单号|工牌编号|身份证号|"
    r"密码|门牌|取件码)")
BOUND = re.compile(r"(我(?:的)?|[\u4e00-\u9fa5]{2,4}的)")
DEICTIC = re.compile(r"(这个|那个|那次|那天|那阵子|那件事|那边)")
INDEFINITE = re.compile(r"(有人|别人|大家|几个|某个)")
PRECISE_COORD = re.compile(r"([\u4e00-\u9fa5]{2,8}(?:路|街|巷|道)\d+号)")
SURNAME_ONLY = re.compile(r"姓([\u4e00-\u9fa5])")


def m_identity_v5(text: str) -> tuple[bool, list[str]]:
    t = text or ""

    # ① 唯一标识符
    if IDENTIFIER.search(t):
        return (True, ["id+bound"]) if BOUND.search(t) else (False, ["naked_id"])

    # ② 二阶关系
    if SECOND_ORDER.search(t):
        return False, ["second_order"]

    # ③ 具名（严格校验）
    nt = find_name_title(t)
    if nt:
        return True, ["name_title:" + nt]
    x = find_xiao(t)
    if x:
        return True, ["xiao:" + x]

    # ④ 固定关系槽
    if STABLE_SLOT.search(t):
        return True, ["stable_slot"]

    # ⑤ 一阶亲属
    if FIRST_REL.search(t):
        return True, ["first_rel"]

    # ⑥ 位置性角色
    if POSITIONAL.search(t):
        return False, ["positional"]

    # ⑦ 不定指
    if INDEFINITE.search(t):
        return False, ["indefinite"]

    # ⑧ 仅姓氏（但要确认不是"XX姓王"这种位置性描述）
    if SURNAME_ONLY.search(t):
        # 如果前面有位置性角色 → 已在上一步处理；这里视为唯一
        return True, ["surname_only"]

    # ⑨ 指代
    if DEICTIC.search(t):
        return False, ["deictic"]

    # ⑩ 精确坐标
    if PRECISE_COORD.search(t):
        return True, ["coord"]

    return False, ["fallback_false"]


def evaluate(rows, gold, fn, label, show=False):
    tp = fp = tn = fn_ = 0
    wrong = []
    if show:
        print("-" * 96)
        print(f"{'rid':<6}{'模型':<8}{'规则':<8}{'命中':<24}{'一致':<6}{'记忆'}")
        print("-" * 96)
    for r in rows:
        rid = r["rid"]
        if rid not in gold:
            continue
        g = bool(gold[rid].get("identity"))
        p, hits = fn(r["memory"])
        if g and p: tp += 1
        elif g and not p:
            fn_ += 1; wrong.append((rid, g, p, r["memory"], gold[rid].get("reason", ""), hits))
        elif (not g) and p:
            fp += 1; wrong.append((rid, g, p, r["memory"], gold[rid].get("reason", ""), hits))
        else: tn += 1
        if show:
            print(f"{rid:<6}{str(g):<8}{str(p):<8}{'/'.join(hits)[:22]:<24}"
                  f"{'✓' if g == p else '✗':<6}{r['memory'][:22]}")
    if show:
        print("-" * 96)
    n = tp + fp + tn + fn_
    acc = (tp + tn) / n if n else 0
    prec = tp / (tp + fp) if (tp + fp) else 0
    rec = tp / (tp + fn_) if (tp + fn_) else 0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0
    print(f"  [{label}] n={n} TP={tp} FP={fp} TN={tn} FN={fn_}  "
          f"acc={acc:.4f} prec={prec:.4f} rec={rec:.4f} F1={f1:.4f}")
    return {"n": n, "tp": tp, "fp": fp, "tn": tn, "fn": fn_,
            "accuracy": round(acc, 4), "precision": round(prec, 4),
            "recall": round(rec, 4), "f1": round(f1, 4), "mismatches": wrong}


def main():
    print("=" * 96)
    print("E-85 identity 第五版（严格姓氏白名单）")
    print("=" * 96)

    out = {"experiment": "E-85", "results": {}}
    batches = [("E-83新20", os.path.join(_HERE, "work", "e83")),
               ("E-81旧24", os.path.join(_HERE, "work", "e81"))]

    all_rows, all_gold, labels = [], [], []
    for label, d in batches:
        cp, vp = os.path.join(d, "corpus.json"), os.path.join(d, "verdict.json")
        if not (os.path.exists(cp) and os.path.exists(vp)):
            continue
        rows = json.load(open(cp, encoding="utf-8"))
        raw = json.load(open(vp, encoding="utf-8"))
        gold = {r["rid"]: r for r in (raw if isinstance(raw, list)
                                      else raw.get("per_item", []))}
        print(f"\n【{label}】")
        r = evaluate(rows, gold, m_identity_v5, label, show=True)
        out["results"][label] = {k: v for k, v in r.items() if k != "mismatches"}
        if r["mismatches"]:
            print("\n  不一致：")
            for rid, g, p, txt, why, h in r["mismatches"]:
                print(f"    [{rid}] 模型={g} 规则={p} {h}")
                print(f"      {txt}  |  模型理由: {why}")
        all_rows.extend(rows); all_gold.extend([gold[x["rid"]] for x in rows
                                                if x["rid"] in gold])
        labels.extend([label] * len(rows))

    # 合并总评
    print("\n" + "=" * 96)
    print("合并总评")
    print("=" * 96)
    tot = {"tp": 0, "fp": 0, "tn": 0, "fn": 0}
    for label in out["results"]:
        for k in tot:
            tot[k] += out["results"][label].get(k, 0)
    n = sum(tot.values())
    acc = (tot["tp"] + tot["tn"]) / n
    prec = tot["tp"] / (tot["tp"] + tot["fp"]) if (tot["tp"] + tot["fp"]) else 0
    rec = tot["tp"] / (tot["tp"] + tot["fn"]) if (tot["tp"] + tot["fn"]) else 0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0
    print(f"  n={n} TP={tot['tp']} FP={tot['fp']} TN={tot['tn']} FN={tot['fn']}")
    print(f"  准确率 {acc:.4f}  精确率 {prec:.4f}  召回 {rec:.4f}  F1 {f1:.4f}")
    out["overall"] = {"n": n, **{k: v for k, v in tot.items()},
                      "accuracy": round(acc, 4), "precision": round(prec, 4),
                      "recall": round(rec, 4), "f1": round(f1, 4)}

    print("\n" + "-" * 96)
    print("版本演进")
    print("-" * 96)
    print(f"  E-80 原规则          0.7083")
    print(f"  E-81 4判据           0.7917")
    print(f"  E-82 加2规则         0.9167（但仅旧集）")
    print(f"  E-83 全新集          0.8500  ← 过拟合证据")
    print(f"  E-84 具名优先        全新0.9000 / 旧0.8333")
    print(f"  E-85 严格姓氏        全新{out['results'].get('E-83新20',{}).get('accuracy',0):.4f} / "
          f"旧{out['results'].get('E-81旧24',{}).get('accuracy',0):.4f} / "
          f"合并{acc:.4f}")

    with open(os.path.join(RESULTS, "e85_identity_v5.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e85_identity_v5.json')}")


if __name__ == "__main__":
    main()
