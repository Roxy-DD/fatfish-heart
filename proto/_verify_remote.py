"""E-464 用 gh api 验证远端内容一致性

git 443 被阻断，无法 clone。
改用 Contents API 逐个下载关键文件，与本地比对 hash。

这不能替代完整 clone 验证，但能确认：
  · 文件确实在远端
  · 内容与我本地的一致（没有被截断/转码）
"""
import base64
import hashlib
import json
import os
import subprocess

ROOT = r"C:\Users\xuee\Desktop\肥鱼之心"
REPO = "Roxy-DD/fatfish-heart"
BRANCH = "master"

# 关键文件
FILES = [
    "LICENSE",
    "README.md",
    "requirements.txt",
    ".gitignore",
    "proto/doctor.py",
    "proto/cosmos_mem/store_v1.py",
    "dsh-plugin/index.js",
    "dsh-plugin/package.json",
]

print("=" * 72)
print("远端内容一致性验证")
print("=" * 72)

same = 0
diff = 0
missing = 0

for path in FILES:
    r = subprocess.run(
        ["gh", "api", f"repos/{REPO}/contents/{path}?ref={BRANCH}",
         "--jq", ".content"],
        capture_output=True, text=True, encoding="utf-8")

    if r.returncode != 0 or not r.stdout.strip():
        print(f"❌ {path:<34} 远端不存在")
        missing += 1
        continue

    try:
        b64 = r.stdout.strip().replace("\n", "").replace(" ", "")
        remote = base64.b64decode(b64)
    except Exception as e:
        print(f"❌ {path:<34} 解码失败: {e}")
        missing += 1
        continue

    local_path = os.path.join(ROOT, path)
    if not os.path.exists(local_path):
        print(f"⚠  {path:<34} 本地不存在（只比对远端）")
        continue

    local = open(local_path, "rb").read()

    # 归一化换行后比对
    ln = local.replace(b"\r\n", b"\n")
    rn = remote.replace(b"\r\n", b"\n")

    lh = hashlib.md5(ln).hexdigest()[:12]
    rh = hashlib.md5(rn).hexdigest()[:12]

    if lh == rh:
        print(f"✅ {path:<34} {len(local):>7} B  {lh}")
        same += 1
    else:
        print(f"⚠  {path:<34} 本地 {lh} / 远端 {rh}")
        print(f"   本地 {len(local)} B, 远端 {len(remote)} B")
        diff += 1

print(f"\n一致 {same} / 差异 {diff} / 缺失 {missing}")

# 检查远端是否泄漏敏感文件
print(f"\n{'='*72}")
print("远端敏感文件扫描")
print("=" * 72)

r = subprocess.run(
    ["gh", "api", f"repos/{REPO}/git/trees/{BRANCH}?recursive=1",
     "--jq", ".tree[].path"],
    capture_output=True, text=True, encoding="utf-8")

if r.returncode == 0:
    paths = [p for p in r.stdout.split("\n") if p.strip()]
    print(f"远端文件总数: {len(paths)}")

    risky = []
    for p in paths:
        low = p.lower()
        if any(k in low for k in (".db", ".backup", ".seed.json",
                                   ".env", "secret", "credential",
                                   "token", "password")):
            risky.append(p)

    if risky:
        print(f"\n⚠ 发现 {len(risky)} 个可疑文件：")
        for p in risky[:15]:
            print(f"   {p}")
    else:
        print("✅ 无 db / 密钥 / 凭据类文件")
else:
    print("获取文件树失败:", (r.stderr or "")[:200])
