"""从迁移备份生成 itsaplan 的 .env（保留原密钥，避免手抄出错）

关键：APP_ENCRYPTION_KEY / BETTER_AUTH_SECRET / POSTGRES_PASSWORD
必须与旧环境一致，否则：
· 已存凭据无法解密
· 登录 session 失效

用法：
    python gen_itsaplan_env.py                    # 生成到 deploy/itsaplan/.env
    python gen_itsaplan_env.py --lan-ip 172.20.10.100
"""
import argparse
import os
import re
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)   # 肥鱼之心根目录
ZIP = os.path.join(ROOT, "proto", "archive", "迁移备份_20261120.zip")
OUT = os.path.join(ROOT, "deploy", "itsaplan", ".env")

# 从备份继承的密钥（必须原样保留）
INHERIT = [
    "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB",
    "BETTER_AUTH_SECRET", "APP_ENCRYPTION_KEY",
    "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY", "S3_BUCKET", "S3_REGION",
]


def read_backup_env():
    with zipfile.ZipFile(ZIP) as z:
        names = [n for n in z.namelist() if n.endswith("itsaplan.env")]
        if not names:
            raise RuntimeError("备份里找不到 itsaplan.env")
        raw = z.read(names[0]).decode("utf-8", "replace")
    d = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        d[k.strip()] = v.strip()
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lan-ip", default="172.20.10.100",
                    help="Windows 主机固定局域网 IP（需你自己先在网卡上设）")
    ap.add_argument("--node-env", default="development",
                    help="纯 HTTP 局域网必须用 development，否则 Secure cookie 导致登录失败")
    args = ap.parse_args()

    old = read_backup_env()
    missing = [k for k in INHERIT if not old.get(k)]
    if missing:
        print(f"❌ 备份缺这些密钥: {missing}")
        return 1

    ip = args.lan_ip
    # API_URL 用路径形式（单域名单端口，cookie 用 __Host- 前缀）
    api_url = f"http://{ip}:3001/api"
    app_url = f"http://{ip}:3001"

    lines = [
        "# itsaplan .env —— 从迁移备份自动生成（2026-11-20）",
        "# 密钥原样继承旧环境，不要改动下面 INHERIT 的项",
        "",
        "# ---- 数据库 ----",
    ]
    for k in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"):
        lines.append(f"{k}={old[k]}")
    lines += [
        f"POSTGRES_PORT=5432",
        "",
        "# ---- 公开地址（纯局域网 HTTP）----",
        "# ★ API_URL 用 /api 路径形式，Caddy 会把 /api/* 转发到 api 并去掉前缀",
        f"API_URL={api_url}",
        f"APP_URL={app_url}",
        "# ★ 纯 HTTP 必须 development：production 会给 cookie 加 Secure，",
        "#   HTTP 下登录成功但 session 立即失效（你之前踩过这个坑）",
        f"NODE_ENV={args.node_env}",
        "",
        "# ---- 认证与加密（原样继承，改了会出事）----",
        f"BETTER_AUTH_SECRET={old['BETTER_AUTH_SECRET']}",
        f"APP_ENCRYPTION_KEY={old['APP_ENCRYPTION_KEY']}",
        "",
        "# ---- S3 对象存储（RustFS，继承原密钥）----",
        "# compose 内 S3_ENDPOINT 由 docker-compose 写死为 http://minio:9000",
        f"S3_ACCESS_KEY_ID={old['S3_ACCESS_KEY_ID']}",
        f"S3_SECRET_ACCESS_KEY={old['S3_SECRET_ACCESS_KEY']}",
        f"S3_BUCKET={old['S3_BUCKET']}",
        f"S3_REGION={old.get('S3_REGION', 'us-east-1')}",
        "",
        "# ---- 端口 ----",
        "API_PORT=3000",
        "WEB_PORT=3001",
        "",
        "# ---- 可选 ----",
        "# VERSION=   # 锁定版本，留空用最新",
        "# TELEMETRY_DISABLED=1",
    ]

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")

    print("✅ 已生成:", OUT)
    print(f"   API_URL={api_url}")
    print(f"   APP_URL={app_url}")
    print(f"   NODE_ENV={args.node_env}")
    print("   继承密钥:", ", ".join(INHERIT))
    print("\n⚠ 部署前请确认：")
    print(f"   1. Windows 网卡 IP 已设为 {ip}（或改 --lan-ip）")
    print("   2. compose 已给 web/api 挂 proxy 网络供 Caddy 反代")
    print("   3. 从备份恢复数据库和附件（见部署规划文档 3.4）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
