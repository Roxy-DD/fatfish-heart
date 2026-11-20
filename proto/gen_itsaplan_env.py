"""从迁移备份生成 itsaplan 的 .env（HTTPS 自签版 / B 路线）

关键：APP_ENCRYPTION_KEY / BETTER_AUTH_SECRET / POSTGRES_PASSWORD
必须与旧环境一致，否则已存凭据无法解密、登录 session 失效。

B 路线（Caddy tls internal 自签 HTTPS）：
· API_URL / APP_URL 用 https://<IP>
· NODE_ENV=production（HTTPS 下 secure cookie 正常工作）
· Caddy 对 IP 签 internal 证书，客户端需导入根证书

用法（IP 每次自己查，显式传入）：
    python gen_itsaplan_env.py --lan-ip 172.20.10.5
"""
import argparse
import os
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ZIP = os.path.join(ROOT, "proto", "archive", "迁移备份_20261120.zip")
OUT = os.path.join(ROOT, "deploy", "itsaplan", ".env")

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
    ap.add_argument("--lan-ip", required=True,
                    help="Windows 主机当前局域网 IP（每次自己查，显式传入）")
    ap.add_argument("--node-env", default="production",
                    help="HTTPS 下用 production；源码 secure=hostPrefixed||production")
    args = ap.parse_args()

    old = read_backup_env()
    missing = [k for k in INHERIT if not old.get(k)]
    if missing:
        print(f"❌ 备份缺这些密钥: {missing}")
        return 1

    ip = args.lan_ip
    # HTTPS + 443 标准端口 → URL 不带端口；/api 路径形式
    api_url = f"https://{ip}/api"
    app_url = f"https://{ip}"

    lines = [
        "# itsaplan .env —— 从迁移备份自动生成（B 路线：Caddy 自签 HTTPS）",
        "# 密钥原样继承旧环境，不要改下面 INHERIT 的项",
        "",
        "# ---- 数据库 ----",
    ]
    for k in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"):
        lines.append(f"{k}={old[k]}")
    lines += [
        "POSTGRES_PORT=5432",
        "",
        "# ---- 公开地址（HTTPS + /api 路径形式）----",
        "# Caddy 在 443 终止 TLS（tls internal 自签），API_URL 走 /api 路径",
        f"API_URL={api_url}",
        f"APP_URL={app_url}",
        "# HTTPS 下源码 hostPrefixedCookies=true，cookie 带 __Host- 前缀 + Secure，",
        "# 必须用 production；且客户端必须先导入 Caddy 根证书，否则证书不受信",
        f"NODE_ENV={args.node_env}",
        "",
        "# ---- 认证与加密（原样继承，改了会出事）----",
        f"BETTER_AUTH_SECRET={old['BETTER_AUTH_SECRET']}",
        f"APP_ENCRYPTION_KEY={old['APP_ENCRYPTION_KEY']}",
        "",
        "# ---- S3 对象存储（RustFS，继承原密钥）----",
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
        "# VERSION=",
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
    print("\n⚠ 部署前必做：")
    print("   1. 每台客户端导入 Caddy 根证书（见 deploy/执行指南.md）")
    print("   2. IP 变了要重新生成本文件 + reload Caddy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
