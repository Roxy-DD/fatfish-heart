"""E-390 直接观测：当前进程的注入历史里有没有 L0/L1

之前我反复说「要验证 E-350 修复需要新会话」。
但其实有更直接的路径：**读插件内存里的 injectHistory**。

HTTP 端点 /cosmos-mem/inject-history 会返回它。

如果能读到一条 protectedCount > 0 的记录，
就证明修复在真实运行时生效了——不需要新会话。

同时也暴露一个观测盲点：
  injectHistory 里存了 items（ep_id + text），但**没存 protectedText**。
  所以看不到 L0/L1 的实际注入文本，只能看条数。
"""
import sys
import os
import json
import urllib.request
import warnings

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))

print("=" * 76)
print("E-390 直接读 injectHistory")
print("=" * 76)

# ---------- ① 找当前端口 ----------
print("\n① 探测可用端口")

ports = [52160, 61638]
base = None
for p in ports:
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{p}/cosmos-mem/stats", timeout=5) as r:
            if r.status == 200:
                base = f"http://127.0.0.1:{p}/cosmos-mem"
                print(f"   ✅ 端口 {p} 可用")
                break
    except Exception as e:
        print(f"   端口 {p}: {type(e).__name__}")

if not base:
    print("\n   ❌ 没有可用端口，插件可能未运行")
    sys.exit(0)

# ---------- ② 读 inject-history ----------
print(f"\n② 读 {base}/inject-history")

try:
    with urllib.request.urlopen(f"{base}/inject-history", timeout=10) as r:
        data = json.loads(r.read().decode("utf-8"))
except Exception as e:
    print(f"   请求失败: {type(e).__name__}: {e}")
    sys.exit(1)

history = data.get("history") or []
print(f"   记录数: {len(history)}")

# ---------- ③ 逐条分析 ----------
print("\n③ 逐条分析")

if not history:
    print("""
   ⚠ injectHistory 为空

   这是**正常**的：injectHistory 是内存数组，插件重载后清空。
   最近做过多次 selfreload，所以历史丢了。
""")
else:
    print(f"\n   {'时间':<12} {'sid':<14} {'protectedCount':>14} "
          f"{'items':>6} {'query'}")
    print("   " + "-" * 84)

    for h in history[-10:]:
        import time
        at = h.get("at", 0)
        t = time.strftime("%m-%d %H:%M", time.localtime(at/1000)) if at else "?"
        sid = str(h.get("sid", ""))[:12]
        pc = h.get("protectedCount")
        items = h.get("items") or []
        q = str(h.get("query", ""))[:30]
        print(f"   {t:<12} {sid:<14} {str(pc):>14} "
              f"{len(items):>6} {q}")

    # 关键统计
    with_pc = [h for h in history if (h.get("protectedCount") or 0) > 0]
    print(f"\n   protectedCount > 0 的记录: {len(with_pc)}/{len(history)}")

    if with_pc:
        print("\n   ✅ 找到带 L0/L1 的注入记录：")
        for h in with_pc[:3]:
            print(f"      protectedCount = {h.get('protectedCount')}")
            print(f"      items 数 = {len(h.get('items') or [])}")
            for it in (h.get("items") or [])[:3]:
                print(f"        [{it.get('ep_id','')[:8]}] "
                      f"{str(it.get('text',''))[:40]}")
    else:
        print("""
   ⚠ 所有记录 protectedCount 都是 0

   可能：
     · 这些记录都在 E-350 修复之前（修复是 14:30，重载在 16:03）
     · 或修复后还没有新会话触发注入
""")

# ---------- ④ 观测盲点 ----------
print("\n④ 观测盲点")

print("""
   injectHistory 里存了：
     · items: [{ep_id, text}] —— 普通检索结果
     · protectedCount —— L0/L1 的**条数**
     · content_chars（在 audit_log 里）

   但**没有存 protectedText 的文本内容**。

   所以：
     · 能确认"注入了几条 L0/L1"
     · 不能确认"注入的内容对不对"
     · 不能确认"首轮是否真的给了全文"（E-348 修复的核心）

   → 这是一个真实的观测盲点，值得补。
     补的方式：injectRec 里加 protectedPreview（前 200 字）。
     成本很小，但能验证 E-348 的修复是否真生效。
""")

out = {
    "experiment": "E-390",
    "base": base,
    "n_history": len(history),
    "n_with_protected": len([h for h in history
                             if (h.get("protectedCount") or 0) > 0]),
    "blind_spot": "injectHistory 未保存 protectedText 内容",
}
p = os.path.join(_HERE, "results", "e390_inject_history.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 results/e390_inject_history.json")
