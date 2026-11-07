"""
E-167 系统排查：还有哪些"中文假设"在英文语料上失效

E-164 偶然发现 split_query_terms 没有英文停用词过滤。
这说明代码里可能还有同类问题——按中文语料设计，在英文上失效。

本实验逐个检查所有与"文本处理"相关的函数，用英文样本测试：

检查清单：
  ① split_query_terms        英文停用词（已修）
  ② extract_elements         人名/时间/地点抽取（正则是否只支持中文）
  ③ detect_triggers          触发场景词表（纯中文）
  ④ estimate_cost            代价信号词（纯中文）
  ⑤ estimate_affect_*        情绪词（纯中文）
  ⑥ infer_scope              能力范围推断（正则是否中文）
  ⑦ full_check               缺陷检测（中文规则）
  ⑧ detect_question_dims     问题维度识别（中文问句模式）

方法：对每个函数，用同一含义的中英文样本各测一次，
      看英文输入是否得到"合理"输出（不是空/不是错）。

判定"失效"的标准：
  · 英文输入返回空，而中文返回非空
  · 英文输入返回明显错误的结果
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
RESULTS = os.path.join(_HERE, "results")


def main():
    print("=" * 94)
    print("E-167 系统排查：中文假设在英文语料上的失效点")
    print("=" * 94)

    findings = []

    # ---------- ① split_query_terms ----------
    print("\n① split_query_terms（已修，验证）")
    from fixes import split_query_terms
    zh = split_query_terms("我电脑的登录密码是多少")
    en = split_query_terms("What is my computer login password?")
    print(f"  中文: {zh[:6]}")
    print(f"  英文: {en[:6]}")
    ok1 = len(en) > 0 and not any(
        w in ("what", "is", "my", "the") for w in en)
    print(f"  → {'✅ 英文停用词已过滤' if ok1 else '❌ 仍有停用词'}")
    findings.append({"item": "split_query_terms", "ok": ok1})

    # ---------- ② extract_elements ----------
    print("\n② extract_elements（人名/时间/地点抽取）")
    from store_v1 import extract_elements
    cases = [
        ("张伟明天下午三点在城东花园开会", "中文"),
        ("Melanie went camping at the beach last Sunday", "英文"),
        ("Caroline moved from Boston in 2020", "英文"),
    ]
    ok2 = True
    for txt, lang in cases:
        el = extract_elements(txt)
        n_nonempty = sum(1 for k in ("person", "time", "location")
                         if el.get(k))
        print(f"  [{lang}] {txt[:44]}")
        print(f"    person={el.get('person')} time={el.get('time')} "
              f"location={el.get('location')}")
        if lang == "英文" and n_nonempty == 0:
            ok2 = False
    print(f"  → {'✅ 英文也能抽到' if ok2 else '❌ 英文全空'}")
    findings.append({"item": "extract_elements", "ok": ok2})

    # ---------- ③ detect_triggers ----------
    print("\n③ detect_triggers（触发场景）")
    from store_v1 import detect_triggers
    zh_t = detect_triggers("看到夕阳就想起小时候")
    en_t = detect_triggers("Seeing the sunset reminded me of home")
    print(f"  中文 '看到夕阳就想起小时候' → {zh_t}")
    print(f"  英文 'Seeing the sunset...' → {en_t}")
    ok3 = len(en_t) > 0
    print(f"  → {'✅ 英文也能识别' if ok3 else '❌ 英文为空（词表纯中文）'}")
    findings.append({"item": "detect_triggers", "ok": ok3})

    # ---------- ④ estimate_cost ----------
    print("\n④ estimate_cost（代价信号）")
    from store_v1 import estimate_cost
    zh_c = estimate_cost("我的密码是1234，千万别忘了")
    en_c = estimate_cost("My password is 1234, don't forget it")
    print(f"  中文 → {zh_c}")
    print(f"  英文 → {en_c}")
    ok4 = abs(en_c - zh_c) < 0.5
    print(f"  → {'✅ 中英接近' if ok4 else f'❌ 差异大（中 {zh_c} vs 英 {en_c}）'}")
    findings.append({"item": "estimate_cost", "ok": ok4})

    # ---------- ⑤ estimate_affect ----------
    print("\n⑤ estimate_affect_v3（情绪）")
    from affect_v3 import estimate_affect_v3
    zh_a = estimate_affect_v3("爷爷去世了，我很痛苦", explicit=False)
    en_a = estimate_affect_v3("My grandfather passed away, I am devastated",
                              explicit=False)
    print(f"  中文 → affect={zh_a.get('affect_word')}")
    print(f"  英文 → affect={en_a.get('affect_word')}")
    ok5 = (en_a.get("affect_word") or 0) > 0.3
    print(f"  → {'✅ 英文也能识别情绪' if ok5 else '❌ 英文情绪识别为 0'}")
    findings.append({"item": "estimate_affect_v3", "ok": ok5})

    # ---------- ⑥ infer_scope ----------
    print("\n⑥ infer_scope（能力范围推断）")
    from capability import infer_scope
    sc_zh = infer_scope("我电脑的登录密码是8848")
    sc_en = infer_scope("My computer login password is 8848")
    print(f"  中文 → {sc_zh}")
    print(f"  英文 → {sc_en}")
    ok6 = any(sc_en.values()) if isinstance(sc_en, dict) else bool(sc_en)
    print(f"  → {'✅ 英文也能推断' if ok6 else '❌ 英文全 False'}")
    findings.append({"item": "infer_scope", "ok": ok6})

    # ---------- ⑦ full_check ----------
    print("\n⑦ full_check（缺陷检测）")
    from capability import full_check
    fc_zh = full_check("这个挺好的，到时候再说吧")
    fc_en = full_check("That's fine, we'll see later")
    print(f"  中文 → {fc_zh}")
    print(f"  英文 → {fc_en}")
    ok7 = (isinstance(fc_en, (list, dict)) and len(fc_en) > 0)
    print(f"  → {'✅ 英文也能检出' if ok7 else '❌ 英文无检出'}")
    findings.append({"item": "full_check", "ok": ok7})

    # ---------- ⑧ detect_question_dims ----------
    print("\n⑧ detect_question_dims（问题维度识别）")
    from capability import detect_question_dims
    d_zh = detect_question_dims("我的密码是多少")
    d_en = detect_question_dims("What is my password?")
    print(f"  中文 '我的密码是多少' → {d_zh}")
    print(f"  英文 'What is my password?' → {d_en}")
    ok8 = bool(d_en)
    print(f"  → {'✅ 英文也能识别维度' if ok8 else '❌ 英文识别为空'}")
    findings.append({"item": "detect_question_dims", "ok": ok8})

    # ---------- 汇总 ----------
    print("\n" + "=" * 94)
    print("汇总")
    print("=" * 94)

    n_ok = sum(1 for f in findings if f["ok"])
    print(f"\n  {'检查项':<28}{'状态'}")
    print("  " + "-" * 50)
    for f in findings:
        print(f"  {f['item']:<28}{'✅ 中英都工作' if f['ok'] else '❌ 英文失效'}")

    print(f"\n  通过 {n_ok}/{len(findings)}")

    fails = [f for f in findings if not f["ok"]]
    if fails:
        print(f"\n  失效项（{len(fails)} 个）：")
        for f in fails:
            print(f"    · {f['item']}")
        print(f"\n  → 这些在英文语料（LoCoMo/LongMemEval）上不工作，")
        print(f"    如果要做多语言支持，都需要补英文规则/词表。")
    else:
        print(f"\n  → 全部中英兼容")

    out = {"experiment": "E-167", "findings": findings,
           "n_ok": n_ok, "n_total": len(findings)}
    with open(os.path.join(RESULTS, "e167_i18n_audit.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e167_i18n_audit.json')}")


if __name__ == "__main__":
    main()
