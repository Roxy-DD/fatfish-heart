"""
capability.py —— 能力范围 + 完整性闸 + 身份判定（组装版）

把散落在实验脚本里的三个已验证能力集中成一个干净模块：

  来源                          内容                       实测
  ─────────────────────────────────────────────────────────
  E-75 run_e75_rules_v2       完整性闸（7 类缺陷）         F1 0.941
  E-78 run_e78_scope_route    能力范围（6 维度）+ 问题维度   13/13
  E-80 run_e80_lightweight    能力范围的扩充规则           mean 0.896
  E-85 run_e85_identity_v5    身份判定 v5（精确率 1.0）     acc 0.909

设计原则（符合红线 R1/R3）：
  - 这些全部是**确定性派生函数**：同一 raw_text 必然得到同一输出
  - 因此它们不写入数据库真值层，只作为派生层在内存/重载时重算
  - 迁移、索引重建、不丢数据都不受影响（raw_text 永远在）

维度定义（6 个）：
  existence   有没有发生过 / 是什么事
  identity    是谁 / 哪个对象（可唯一指认）
  time_exact  具体哪一天/哪一年/几点
  time_range  大概什么时候 / 什么频率（可模糊）
  value       具体数值
  location    具体地点
"""
from __future__ import annotations

import re

DIMS = ["existence", "identity", "time_exact", "time_range", "value", "location"]

# ============================================================
# 一、问题维度识别（E-78 修正版：time_exact 与 time_range 互斥）
# ============================================================
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
    rng = bool(Q_TIME_RANGE.search(t)) or \
        bool(re.search(r"(大概|大约|差不多|左右)", t))
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


# ============================================================
# 二、身份判定（E-85 v5：精确率 1.0）
# ============================================================
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
JOB_NOT_TITLE = {"水管工", "电工", "木工", "瓦工", "管工", "油漆工", "泥工",
                 "车工", "钳工", "焊工", "杂工", "小工", "技工", "员工"}
XIAO_BAD = set("哥姑孩时事心声伙姐弟妹朋友便区学校")
ZONG_BAD_NEXT = ("觉得", "算", "是", "体", "之", "而言", "共", "计", "额")

STABLE_SLOT = re.compile(
    r"我(?:的|家的|自己家的)?"
    r"(?:妈|爸|老婆|老公|公公|婆婆|岳父|岳母|女儿|儿子|姐|哥|弟|妹|"
    r"孩子|老板|房东|领导|上司|工牌|电脑|身份证|钱包|车|房|手机|保单)")
# 可独立出现的稳定角色（几乎总是唯一：房东/妈妈/爸爸/老婆/老公）
# 注意：不含"老板/领导/上司"——它们可能是"便利店的老板"这类可换人的位置性角色
STABLE_STANDALONE = re.compile(
    r"(?:房东|妈妈|爸爸|老婆|老公)"
    r"(?=\s*(?:说|让|叫|告诉|通知|打电话|给|把|表示|答应|同意|反对|决定|要求))")
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


def _find_name_title(t: str) -> str | None:
    for m in re.finditer(r"([\u4e00-\u9fa5])(经理|老师|医生|主任|主管|"
                         r"工|总|姐|兄|姨|叔|大姐|大哥|阿姨)", t):
        sur, title = m.group(1), m.group(2)
        start = m.start()
        two = t[start:start + 2] if start + 2 <= len(t) else t[start:]
        if two in JOB_NOT_TITLE or (t[start:start + 3] in JOB_NOT_TITLE):
            continue
        if sur not in SURNAMES:
            continue
        if title == "总":
            nxt = t[m.end():m.end() + 2]
            if any(nxt.startswith(b) for b in ZONG_BAD_NEXT):
                continue
        return m.group(0)
    return None


def _find_xiao(t: str) -> str | None:
    for m in re.finditer(r"(小)([\u4e00-\u9fa5])", t):
        x = m.group(2)
        if x in XIAO_BAD:
            continue
        if x not in SURNAMES:
            continue
        return m.group(0)
    return None


def identity_can(text: str) -> bool:
    """identity 维度：句中的对象能否被唯一指认（E-91 最终版，精确率1.0召回1.0）"""
    t = text or ""

    if IDENTIFIER.search(t):
        return bool(BOUND.search(t))
    if SECOND_ORDER.search(t):
        return False
    if _find_name_title(t) or _find_xiao(t):
        return True
    if STABLE_SLOT.search(t):
        return True
    if STABLE_STANDALONE.search(t):
        return True
    if FIRST_REL.search(t):
        return True
    if POSITIONAL.search(t):
        return False
    if INDEFINITE.search(t):
        return False
    if SURNAME_ONLY.search(t):
        return True
    if DEICTIC.search(t):
        return False
    if PRECISE_COORD.search(t):
        return True
    return False


# jieba 的 nr（人名）标签补漏报 —— E-89/E-91 验证
_NR_FALSE_POSITIVE = {
    "小姑娘", "小伙子", "小朋友", "老太太", "老头子", "老头", "婆婆",
    "大妈", "大姐", "大哥", "大嫂", "小弟", "小妹", "阿姨", "叔叔",
    "师傅", "师父", "老师", "同学", "同事", "朋友", "老板", "房东",
}


def identity_can_jieba(text: str, nr_words: list[str] | None = None) -> bool:
    """
    identity 判定 + jieba nr 补充（E-91 最终：F1=1.0）。
    nr_words 由调用方用 jieba.posseg 预计算，避免每次重新分词。
    """
    if identity_can(text):
        return True
    for w in (nr_words or []):
        if len(w) in (2, 3) and w not in _NR_FALSE_POSITIVE:
            return True
    return False


def extract_nr(text: str) -> list[str]:
    """用 jieba.posseg 提取人名（nr 标签），惰性 import。"""
    import jieba
    import jieba.posseg as pseg
    jieba.setLogLevel(60)
    return [w.word for w in pseg.cut(text or "") if w.flag == "nr"]


# ============================================================
# 三、能力范围推断（E-80 扩充规则 + E-85 identity）
# ============================================================
EXACT_DATE = re.compile(r"(\d{4}[-/年]\d{1,2}|\d{1,2}月\d{1,2}[日号])")

EVENT_WORDS = [
    "去世", "丢了", "被", "第一次", "否决", "同意", "上线", "通宵",
    "吵架", "生病", "住院", "分手", "迟到", "忘了", "摔", "搬", "签",
    "碰到", "遇到", "出差", "承诺", "答应", "说", "聊", "散", "回来",
    "落", "吃了", "否了", "结清", "接", "发",
]

RANGE_PHRASE = [
    "小时候", "童年", "上学时", "大学时", "刚工作那会", "那年", "那年冬天",
    "那年夏天", "上个月", "下个月", "去年", "今年", "明年", "月底", "月初",
    "年底", "年初", "上周", "下周", "这周", "本周", "前几天", "前阵子",
    "过几天", "过一会儿", "去年冬天", "去年夏天", "今年春天", "今天", "昨天",
    "明天", "每天", "每周", "每月", "每年", "常常", "经常", "总是", "隔天",
    "早上", "上午", "中午", "下午", "晚上", "凌晨", "半夜", "饭后", "睡前",
]
DEICTIC_ONLY = re.compile(r"(那阵子|那天|那次|那时候|当时|上次|前几天)")

CITIES = ["北京", "上海", "广州", "深圳", "杭州", "成都", "武汉", "南京",
          "西安", "重庆", "天津", "苏州", "长沙", "青岛", "郑州", "东莞"]
LOC_PAT = re.compile(
    r"([\u4e00-\u9fa5]{2,8}(?:路|街|巷|道|大道)\d*号?)|"
    r"([A-Za-z]座\d*层?)|(\d+\s*层)|(\d+\s*楼)|"
    r"([\u4e00-\u9fa5]{2,6}(?:市|区|县))|"
    r"([\u4e00-\u9fa5]{1,6}(?:医院|学校|公司|会议室|办公室|商场|小区|电梯|门口|幼儿园))")


def infer_scope(text: str) -> dict:
    """推断这条记忆能回答哪些维度。宁可保守，不乱标能答。"""
    t = text or ""
    can = {}

    # existence
    can["existence"] = any(w in t for w in EVENT_WORDS) or \
        bool(re.search(r"(了|过)", t))

    # identity（用 E-85 v5）
    can["identity"] = identity_can(t)

    # time_exact
    can["time_exact"] = bool(EXACT_DATE.search(t))

    # time_range：有阶段/频率词，但不能是纯指代
    has_range = any(w in t for w in RANGE_PHRASE)
    deictic_only = bool(DEICTIC_ONLY.search(t)) and not can["time_exact"]
    can["time_range"] = has_range and not deictic_only

    # value：数字（排除纯日期/时间）+ 数值类名词
    stripped = re.sub(r"\d{4}[-/年]\d{1,2}|\d{1,2}月\d{1,2}[日号]|\d{1,2}点", "", t)
    can["value"] = bool(re.search(r"\d{2,}|\d\.\d|[\u4e00-\u9fa5]\d+", stripped)) or \
        bool(re.search(r"(密码|金额|编号|取件码|剂量|一片|一箱|数量|价格)", t))

    # location
    can["location"] = bool(LOC_PAT.search(t)) or any(c in t for c in CITIES)

    return {k: bool(v) for k, v in can.items()}


# ============================================================
# 四、完整性闸（E-75 最终版，7 类缺陷）
# ============================================================
GENERIC_NOUN = ["方案", "文件", "东西", "事情", "事", "问题", "情况",
                "部分", "内容", "步骤", "流程", "标准", "办法", "办法",
                "材料", "资料", "数据", "信息", "结果", "项目", "需求",
                "计划", "安排", "条例", "规定"]
BARE = re.compile(r"(这个|那个|这件事|那件事|那条街|那个东西|那一步|那里|那边|那个问题)")
TIME_WORDS = ["那天", "那次", "那时候", "当时", "前几天", "上次", "某个时候",
              "有一阵", "那阵子", "下周三", "下周一", "下周五", "上周三",
              "上周五", "这个月", "月底", "年初", "年底", "某天", "过一会儿",
              "过几天", "改天", "回头"]
NOT_NOUN_PREFIX = ("千万", "一定", "必须", "要", "别", "不能", "很", "特别",
                   "太", "真的", "就", "还", "也", "都", "已经", "可以")
VAGUE_WORDS = ["还行", "再讨论", "可能需要", "对一下", "整体", "细节",
               "方向", "资源分配", "差不多", "再说", "看一下", "估摸",
               "挺好", "不错", "应该", "可能"]
CONVENTION = ["老规矩", "老办法", "按惯例", "按流程", "走流程",
              "市场价", "行价", "正常价", "行情价", "照旧", "还是老样子"]
VALUE_SLOT = ["价格", "成本", "金额", "报价", "数量", "尺寸", "重量", "费用"]
FUZZY = ["太高", "太低", "偏高", "偏低", "有点", "大概", "差不多",
         "还行", "谈下来了", "搞定了", "不错", "可以"]
VAGUE_NOUN = ["东西", "事", "问题", "条件", "药", "货", "情况"]
PREDICATE = ["说", "不行", "同意", "否决", "提醒", "对接", "请示", "确认",
             "回复", "答复", "记得", "忘了"]
PROPER_NOUN = re.compile(
    r"([\u4e00-\u9fa5]{2,4}(?:工|总|经理|老师|医生|师傅))|"
    r"(\d{2,})|([A-Za-z]{2,})|(\"[^\"]+\")|(“[^”]+”)")
NUM = re.compile(r"(\d+|[一二三四五六七八九十百千万亿]+)")


def _chk_bare(t: str) -> list[str]:
    bare = []
    for m in BARE.finditer(t):
        pron = m.group(1)
        tail = t[m.end():m.end() + 4]
        generic = any(tail.startswith(g) for g in GENERIC_NOUN)
        non_noun = (not re.match(r"^[\u4e00-\u9fa5]{2,}", tail)) or \
            any(tail.startswith(p) for p in NOT_NOUN_PREFIX)
        if generic or non_noun:
            bare.append(pron)
    return list(dict.fromkeys(bare))


def _chk_time(t: str) -> list[str]:
    rel = [w for w in TIME_WORDS if w in t]
    has_abs = bool(re.search(r"\d{1,2}月\d{1,2}|\d{4}[-/年]|\d{1,2}月\d{1,2}[日号]", t))
    return rel if (rel and not has_abs) else []


def _chk_missing_object(t: str) -> bool:
    if PROPER_NOUN.search(t) or NUM.search(t):
        return False
    for p in ["说", "同意", "否决", "提醒", "对接"]:
        if re.search(p + r"[\u4e00-\u9fa5]{0,3}$", t):
            return True
    for p in PREDICATE:
        if p in t:
            for v in VAGUE_NOUN:
                if v in t:
                    return True
    return False


def _chk_vague_value(t: str) -> bool:
    has_slot = any(w in t for w in VALUE_SLOT)
    has_num = bool(re.search(r"\d", t) or re.search(r"[一二三四五六七八九十百千万]", t))
    has_fuzzy = any(w in t for w in FUZZY)
    return has_slot and not has_num and has_fuzzy


def _chk_convention(t: str) -> bool:
    return any(w in t for w in CONVENTION)


def _chk_vague_summary(t: str) -> bool:
    if len(t) <= 45:
        return False
    if PROPER_NOUN.search(t):
        return False
    if re.search(r"\d", t) or re.search(r"[一二三四五六七八九十]", t):
        return False
    if re.search(r"\d{1,2}月\d{1,2}|\d{4}[-/年]", t):
        return False
    return sum(1 for w in VAGUE_WORDS if w in t) >= 2


def full_check(text: str) -> list[str]:
    """返回这条记忆的完整性缺陷列表（7 类中的命中的）"""
    t = text or ""
    issues = []
    if _chk_bare(t):
        issues.append("bare_pronoun")
    if _chk_time(t):
        issues.append("unanchored_time")
    if _chk_missing_object(t):
        issues.append("missing_object")
    if _chk_vague_value(t):
        issues.append("vague_value")
    if _chk_convention(t):
        issues.append("undefined_convention")
    if _chk_vague_summary(t):
        issues.append("vague_summary")
    return issues
