"""部署前一次性配置：把 Caddyfile 的 <IP> 替换成当前局域网 IP

用法：
    python set_lan_ip.py 172.20.10.5
    python set_lan_ip.py              # 不带参数则自动检测当前 WLAN IP

会做：
  1. 读 deploy/caddy/Caddyfile，把 <IP> 替换为实际 IP
  2. 重新生成 deploy/itsaplan/.env（HTTPS + 原密钥）
  3. 打印后续步骤（reload Caddy / 重启 itsaplan / 导根证书）
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CADDYFILE = os.path.join(ROOT, "deploy", "caddy", "Caddyfile")


def detect_ip():
    """从 PowerShell 读 WLAN 的 IPv4"""
    ps = (
        "(Get-NetIPConfiguration -InterfaceAlias 'WLAN' -ErrorAction SilentlyContinue)."
        "IPv4Address.IPAddress"
    )
    p = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, text=True, timeout=30)
    ips = [x.strip() for x in p.stdout.splitlines()
           if re.match(r"^\d+\.\d+\.\d+\.\d+$", x.strip())]
    return ips[0] if ips else None


def main():
    if len(sys.argv) > 1:
        ip = sys.argv[1].strip()
    else:
        ip = detect_ip()
        if not ip:
            print("❌ 自动检测失败，请手动传 IP: python set_lan_ip.py <IP>")
            return 1
        print(f"  自动检测到 WLAN IP: {ip}")

    if not re.match(r"^\d+\.\d+\.\d+\.\d+$", ip):
        print(f"❌ 不是合法 IP: {ip}")
        return 1

    # 1. 替换 Caddyfile
    c = open(CADDYFILE, encoding="utf-8").read()
    n = c.count("<IP>")
    c = c.replace("<IP>", ip)
    open(CADDYFILE, "w", encoding="utf-8", newline="\n").write(c)
    print(f"✅ Caddyfile: 替换 {n} 处 <IP> → {ip}")

    # 2. 重新生成 .env
    gen = os.path.join(HERE, "gen_itsaplan_env.py")
    p = subprocess.run([sys.executable, gen, "--lan-ip", ip],
                       capture_output=True, text=True, encoding="utf-8")
    print(p.stdout.strip())
    if p.returncode != 0:
        print("❌ 生成 .env 失败:", p.stderr[:300])
        return 1

    print("\n下一步：")
    print(f"  1. cd deploy\\caddy && docker compose up -d")
    print(f"  2. docker exec caddy caddy reload --config /etc/caddy/Caddyfile")
    print(f"  3. cd ..\\itsaplan && docker compose -f docker-compose.yml -f docker-compose.override.yml up -d")
    print(f"  4. docker cp caddy:/data/pki/authorities/local/root.crt .\\caddy-root.crt")
    print(f"  5. 每台客户端导入 caddy-root.crt 到『受信任的根证书颁发机构』")
    print(f"  6. 浏览器访问 https://{ip}")
    print(f"\n⚠ IP 变了要重跑本脚本（Caddyfile 已替换过，<IP> 不在了，需从 git 恢复再跑）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
