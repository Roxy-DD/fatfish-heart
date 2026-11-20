# Docker vs Podman 选型分析（针对你的 Windows 机器）

分析日期：2026-11-20
背景：刚清空 WSL2 + Podman 虚拟机（1 GB 残留 + 1920 MB 内存占用），要重新选型。

---

## 你的机器事实（实测）

| 项 | 值 |
|---|---|
| Windows | 25H2 / Build 26200（Win11 最新版）|
| 虚拟化 | Hyper-V 已被检测到（hypervisor 已启用）|
| 物理内存 | 15.71 GB（重置前用了 82%，清空 WSL 后可用 4.15 GB）|
| C 盘剩余 | 65.4 GB |

---

## 核心结论（先说答案）

**选 Docker Desktop。**

理由不是"Docker 比 Podman 技术更好"，而是**针对你的具体使用场景和历史问题**。

---

## 为什么：从你踩过的坑说起

### 你之前用 Podman 遇到的真实问题（有记录）

1. **网络映射极其痛苦**
   - Windows 版 Podman 只绑定 `127.0.0.1`，必须靠 `netsh portproxy` 转发
   - `iphlpsvc` 服务崩过一次，portproxy 全废
   - 容器改端口 → portproxy 要跟着改，你至少改了 3 次（3000→3010、3001→3002）

2. **热点 IP 一变就全断**
   - `172.20.10.2 → .4 → .5`，每次都要重新配
   - 前端写死 IP，导致 `failed to fetch`

3. **WSL 内存占用大**
   - `vmmemWSL` 长期占 1.9 GB
   - Podman machine 残留 1 GB 磁盘

4. **依赖 WSL2 这个额外层**
   - 你明确表达过不想用 WSL2（性能考虑）

### Docker Desktop 能解决什么

| 你的痛点 | Docker Desktop | Podman Windows |
|---|---|---|
| 端口映射 | `-p 3000:3000` 直接生效，localhost 可达 | 只绑 127.0.0.1，要 portproxy |
| IP 变化 | 不影响（localhost 访问）| 热点 IP 变就断 |
| 命令行兼容 | docker compose 原生 | 要适配 podman compose |
| 生态/文档 | 绝大多数教程默认 Docker | 少 |
| 开机自启 | 原生支持 | 服务配置繁琐 |
| 内存 | 也用 WSL2（同样 vmmemWSL）| 也用 WSL2 |

**关键点**：Docker Desktop **现在也用 WSL2**，所以"不想用 WSL2"这条对两者都一样。
但 Docker 的 WSL2 集成更成熟，网络是自动打通的（localhost 直接可达），
不像 Podman 要手动 portproxy。

---

## 但有一个前提要知道

### Docker Desktop 的许可证（重要）

- **个人使用/小团队（<250 员工且非商业）**：免费
- **商业使用**：需要付费订阅（约 $5-9/用户/月）

你是个人项目，符合免费条件。

### 如果你坚持开源免费 + 不要 Docker 的许可顾虑

可以选 **Podman 但改用 Hyper-V 后端**（不是 WSL2）：
```
podman machine init --provider=hyperv
```
这样绕开 WSL，但网络问题依旧（还是要 portproxy）。

---

## 三个方案对比

| 方案 | 网络体验 | 内存 | 许可 | 你的适配成本 |
|---|---|---|---|---|
| **Docker Desktop + WSL2** | ⭐⭐⭐ 开箱即用 | ~2 GB | 个人免费 | 低（教程多）|
| Podman + WSL2 | ⭐ 要 portproxy | ~2 GB | 完全开源 | 高（你已踩坑）|
| Podman + Hyper-V | ⭐ 要 portproxy | ~1.5 GB | 完全开源 | 高 |

---

## 我的建议（具体配置）

**选 Docker Desktop，用它默认的 WSL2 后端**，然后：

1. **限制 WSL 内存**（避免再吃 1.9 GB）：
   在 `C:\Users\xuee\.wslconfig` 写：
   ```ini
   [wsl2]
   memory=4GB
   processors=4
   swap=1GB
   ```

2. **用 compose 管理 itsaplan**（不再手动 podman run）：
   - 端口映射写在 docker-compose.yml 里，一次配好
   - 重启/迁移只改一个文件

3. **访问用 localhost 而非局域网 IP**：
   - Docker Desktop 的 WSL2 集成让 `http://localhost:3001` 直接可达
   - 避开你之前"热点 IP 变化"的坑

4. **如果需要局域网访问**：用 Docker Desktop 的 `host.docker.internal` 或做个固定端口转发，
   但不要再手动改 netsh portproxy

---

## 一句话总结

**你的问题不是"WSL2 不好"，是"Podman 的 WSL2 网络实现让你反复踩坑"。**
Docker Desktop 的 WSL2 集成把网络打通了（localhost 直达），
对个人免费，教程生态最好，**迁移成本最低**。

如果哪天你特别在意"完全开源无许可"，再考虑 Podman + Hyper-V，
但要接受继续手动配网络。
