"""
cosmos_mem v0.2 修复：三个真实缺陷

缺陷 1（严重）：「我电脑密码」查不到「我电脑的登录密码是 8848」
  原因：FTS5 分字/分词后，"密码"能匹配，但查询里"我电脑密码"作为一个整体串
        去 MATCH 失败。需要：查询词拆解 + 多词 OR。

缺陷 2：「在会议室」被人格抽取为"人"
  原因：PERSON_PAT 的第二个分支（称谓）先把"在会议室"里的字吃了。
        需要：先去掉地点/时间短语，再抽人。

缺陷 3：平静但重要的内容情绪值全 0（唤醒 0.00）
  原因：规则只认情绪词。"妈妈打电话说一切都好" 不触发任何规则。
        但按用户定义，重要性来自"生存代价"——这类内容该有基础权重。
        需要：加入"实体亲密度"与"任务型关键词"作为低唤醒的补充信号。
"""
from __future__ import annotations

import re

# ---------- 修复 2：先剥离地点/时间，再抽人 ----------
_TIME_STRIP = re.compile(
    r"(\d{1,2}月\d{1,2}[日号]?|\d{4}[-/年]\d{1,2}[-/月]\d{1,2}[日]?|"
    r"今天|昨天|前天|明天|后天|上周|本周|这周|下周|上个月|这个月|下个月|"
    r"去年|今年|明年|早上|上午|中午|下午|晚上|凌晨|半夜|刚才|之前|后来)")
_LOC_STRIP = re.compile(
    r"(在公司|在家里|在家|在办公室|在会议室|在学校|在医院|在车上|在路上|"
    r"在咖啡厅|在图书馆|在机场|在车站|在饭店|在卧室|在客厅|在阳台|"
    r"会议室|办公室|客厅|卧室|阳台)")

_KINSHIP = ["妈妈", "爸爸", "老婆", "老公", "儿子", "女儿", "爷爷", "奶奶",
            "姥姥", "姥爷", "哥哥", "姐姐", "弟弟", "妹妹", "孩子"]
_ROLE = ["老板", "领导", "同事", "客户", "朋友", "老师", "医生", "房东",
         "司机", "经理", "主管", "partner"]


def extract_person_fixed(text: str) -> list[str]:
    """先剥离时间/地点，再抽人名与称谓"""
    t = text or ""
    t = _LOC_STRIP.sub(" ", t)
    t = _TIME_STRIP.sub(" ", t)
    out = []
    # 称谓优先（最可靠）
    for w in _KINSHIP + _ROLE:
        if w in t and w not in out:
            out.append(w)
    # "老王""小张"这类（前一个字符不能是"和/跟/与/在/和"等连接词）
    for m in re.finditer(r"(?<![和跟与在的和及同])([老小][\u4e00-\u9fa5]{1,2})", t):
        w = m.group(1)
        # 去掉尾部可能的动词起始字
        w = re.sub(r"[讨说问答告让给觉认讨]向?$", "", w)
        if len(w) >= 2 and w not in out:
            out.append(w)
    # "X说/X问/X告诉" 前的中文名（排除动词短语误抽）
    _BAD_TAIL = ("确", "认", "定", "决", "核", "商", "讨", "论", "评", "审")
    for m in re.finditer(r"([\u4e00-\u9fa5]{2,3})(?=说|问|告诉|让我|跟我|给我|觉得|认为)", t):
        w = m.group(1)
        if w in out:
            continue
        if any(k in w for k in ("在", "的", "了", "是", "最", "终", "已", "经")):
            continue
        if w[-1] in _BAD_TAIL:
            continue
        out.append(w)
    return out[:5]


# ---------- 修复 1：查询词拆解 ----------
_STOP = set("的了吗呢吧啊是我你他她它这那有在个和与及或")

# 英文停用词（E-164 实测的 bug：原实现把所有英文单词都当检索词，
# 导致 what/and/her 这类虚词给无关长文本加分，把真正相关的记忆挤掉）
_EN_STOP = set("""
a an the and or but if then than that this these those
is are was were be been being am do does did doing done
have has had having will would shall should can could may might must
i you he she it we they me him her us them my your his its our their
of in on at to from by with without for about as into over under
what which who whom whose when where why how
not no yes so such very just also too only more most much many
there here all any some each every both few other another
""".split())


def split_query_terms(q: str) -> list[str]:
    """
    把查询拆成可检索的词。

    策略：中文 2-gram + 英文/数字整串 + 去掉停用字/停用词。

    E-164 修正：英文单词原本无停用词过滤，
    LoCoMo 这类英文语料上，what/and/her 等虚词会让无关长文本拿高分，
    把真正相关的记忆挤出去（实测有 6 条余弦排名前 3 却未召回）。
    """
    q = q or ""
    terms = []
    # 英文/数字（过滤英文停用词与单字母）
    for m in re.finditer(r"[A-Za-z]+|\d+\.?\d*", q):
        w = m.group(0).lower()
        if w in _EN_STOP:
            continue
        if len(w) < 2 and not w.isdigit():
            continue
        terms.append(w)
    # 中文 2-gram
    zh = re.sub(r"[^\u4e00-\u9fa5]+", "", q)
    for i in range(len(zh) - 1):
        bg = zh[i:i + 2]
        if bg[0] in _STOP and bg[1] in _STOP:
            continue
        terms.append(bg)
    # 去重保序
    seen, out = set(), []
    for t in terms:
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


# ---------- 修复 3：补充"低唤醒但重要"的信号 ----------
LOW_AROUSAL_IMPORTANT = {
    "密码": 0.7, "账号": 0.6, "地址": 0.5, "电话": 0.5, "身份证": 0.7,
    "生日": 0.5, "约定": 0.4, "承诺": 0.5, "截止": 0.6, "期限": 0.6,
    "必须": 0.5, "一定要": 0.5, "定的是": 0.6, "最后定": 0.6, "结论": 0.5,
}

# 亲密度：不是情绪，但决定"该不该记"
INTIMACY = {"妈妈": 0.6, "爸爸": 0.6, "老婆": 0.7, "老公": 0.7,
            "儿子": 0.7, "女儿": 0.7, "孩子": 0.7, "家": 0.5}


# ---------- 注意 ----------
# 原先这里有一个 estimate_affect_v2（规则法 + 低唤醒补充信号）。
# 它已被 affect_v3（显式情绪词）和 affect_v4（模型判断）取代，
# 且依赖已归档的 store.py。2026-11-07 清理时移除。
# 当前生产链使用：affect_v3.estimate_affect_v3 / affect_v4.estimate_affect_v4

# ---------- v1.0 新增：具体场景词（用于触发匹配，不用粗类）----------
_SCENE_VOCAB = [
    "夕阳", "日落", "晚霞", "橘红", "天空", "院子", "狗", "医院", "消毒水",
    "病房", "毕业", "学位服", "工资", "约会", "深夜", "通宵", "开会", "台上",
    "发言", "商场", "人多", "出分", "考试", "密码", "门口", "家里", "电脑",
]


def scene_words_v2(text: str) -> set:
    t = text or ""
    out = set()
    for w in _SCENE_VOCAB:
        if w in t:
            out.add(w)
    return out
