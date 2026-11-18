# 局域网访问 itsaplan

> 让同一 WiFi / iPhone 热点下的其他设备也能打开 itsaplan。

## 问题

itsaplan 部署在本机，`http://127.0.0.1:3001` 本机能开，
但另一台电脑访问 `http://<你的IP>:3001` 连不上。

## 原因

Podman 6.x 在 Windows/WSL 上做端口转发时，**只绑定 `127.0.0.1`**：

```
TCP    127.0.0.1:3001    LISTENING
TCP    [::1]:3001        LISTENING
```

即使容器内部映射写的是 `0.0.0.0:3001->3001/tcp`，
Windows 侧的实际监听仍然只在回环地址上。

**判定方法**：从本机用局域网 IP 访问自己

```powershell
Invoke-WebRequest http://172.20.10.2:3001
# 如果报「目标计算机积极拒绝」→ 就是这个原因
```

注意这**不是**防火墙问题，也不是 iPhone 热点的问题——
服务根本没监听在那上面。

## 解决

用 Windows 的 portproxy 加一层转发：`0.0.0.0:3001` → `127.0.0.1:3001`。

### 一键脚本

```powershell
# 以管理员身份打开 PowerShell，然后：
cd C:\Users\xuee\Desktop\肥鱼之心\scripts
.\enable-lan-access.ps1
```

脚本做的事：
1. 自动检测局域网 IP（优先识别 iPhone 热点的 `172.20.10.x`）
2. 检查 3000 / 3001 端口是否在监听
3. 添加 portproxy（幂等，重复跑不会出错）
4. 添加防火墙放行规则（只放这两个端口）
5. 从本机用局域网 IP 验证一遍
6. 打印出另一台电脑该访问的地址

### 手动执行（如果不想跑脚本）

```powershell
netsh interface portproxy add v4tov4 listenport=3001 listenaddress=0.0.0.0 connectport=3001 connectaddress=127.0.0.1
netsh interface portproxy add v4tov4 listenport=3000 listenaddress=0.0.0.0 connectport=3000 connectaddress=127.0.0.1

New-NetFirewallRule -DisplayName "itsaplan port 3001" -Direction Inbound -Protocol TCP -LocalPort 3001 -Action Allow
New-NetFirewallRule -DisplayName "itsaplan port 3000" -Direction Inbound -Protocol TCP -LocalPort 3000 -Action Allow
```

## 撤销

```powershell
netsh interface portproxy reset
Get-NetFirewallRule -DisplayName 'itsaplan port *' | Remove-NetFirewallRule
```

不影响 itsaplan 自身，随时可撤。

## 常见问题

### 配好了还是连不上

按顺序排查：

1. **两台设备确实在同一网段吗**
   ```powershell
   # 两台机器分别执行，看 IPv4 地址
   ipconfig
   ```
   iPhone 热点下应该都是 `172.20.10.x`。

2. **iPhone 热点开了「客户端隔离」**
   部分 iOS 版本的热点会隔离客户端，那样同一热点下的设备互相看不到。
   换普通路由器 WiFi 试试，如果通了就是这个原因。

3. **Windows 网络位置是「公用」**
   公用网络下防火墙更严。改成「专用」：
   设置 → 网络和 Internet → 属性 → 网络配置文件 → 专用

4. **IP 变了**
   iPhone 热点的地址每次重连可能变，重跑脚本即可。

### 端口 3000 和 3001 的区别

| 端口 | 用途 |
|---|---|
| 3001 | 网页界面（浏览器打开这个） |
| 3000 | API / MCP 接口 |

日常用只需要 3001。只有要给别的 AI 工具接 MCP 时才用 3000。

## 安全提醒

开了这个之后，**同一局域网内的任何设备**都能访问 itsaplan。
在自家 WiFi / 自己热点下没问题，但：

- 不要在咖啡厅、公司的公共 WiFi 下开
- 用完可以 `netsh interface portproxy reset` 关掉
