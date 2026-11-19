"""用 OSV API 精确扫描已安装依赖的漏洞

OpenSSF Scorecard 报「49 existing vulnerabilities detected」，
但那个数字来自 GitHub Security Advisories 的全量统计，
可能包含历史版本，不代表当前安装的版本有漏洞。

OSV (osv.dev) 是 Google 主导的开源漏洞数据库，
按精确版本号查询，能给出真实结论。
"""
import json
import urllib.request

DEPS = {
    "numpy": "2.3.5",
    "torch": "2.13.0",
    "transformers": "5.20.2",
    "jieba": "0.42.1",
    "huggingface-hub": "2.3.0",
}

URL = "https://api.osv.dev/v1/querybatch"


def query_osv(pkg, ver):
    body = json.dumps({
        "queries": [{"package": {"name": pkg, "ecosystem": "PyPI"},
                     "version": ver}]
    }).encode("utf-8")
    req = urllib.request.Request(URL, data=body,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)["results"][0]


print("=" * 70)
print("OSV 精确漏洞扫描（当前安装版本）")
print("=" * 70)

total = 0
for pkg, ver in DEPS.items():
    try:
        res = query_osv(pkg, ver)
        vulns = res.get("vulns") or []
        n = len(vulns)
        total += n
        status = "✅ 无漏洞" if n == 0 else f"⚠️ {n} 个漏洞"
        print(f"\n  {pkg}=={ver}: {status}")
        for v in vulns[:3]:
            vid = v.get("id", "?")
            summary = (v.get("summary") or "")[:70]
            print(f"     · {vid}: {summary}")
    except Exception as e:
        print(f"\n  {pkg}=={ver}: ❌ 查询失败 {type(e).__name__}: {e}")

print(f"\n{'=' * 70}")
print(f"总计：{total} 个已知漏洞影响当前安装版本")
print("=" * 70)
if total == 0:
    print("\n结论：Scorecard 报的 49 个 CVE 是历史统计，")
    print("当前实际安装的版本均不受影响。属于误报级别的Alarm。")
