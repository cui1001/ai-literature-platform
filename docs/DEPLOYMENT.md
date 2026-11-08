# 在线演示部署指南

> 目标：给评审提供一个**评审期间持续可用**的公网地址，且数据（`data/`）在重启后不丢。
> 本项目是单进程 FastAPI + SQLite + 静态前端，最小 1 核 1G 即可运行，推荐 1 核 2G。

## 方案对比

| 方案 | 需要什么 | 地址形式 | 成本 | 评审期可靠性 | 适合 |
| --- | --- | --- | --- | --- | --- |
| A. 云服务器 + 域名 + HTTPS（推荐） | 服务器、域名（大陆需备案） | `https://agentlit.example.com` | 约 ¥60–120/年（学生机/轻量） | 高（7×24 常驻） | 正式提交 |
| B. Cloudflare Tunnel | 一台常开的电脑（你现在的机器即可） | `https://xxx.trycloudflare.com` 或自有域名 | 0 元 | 中（电脑关机/断网即失效；quick 隧道地址会变） | 临时演示、录屏前验证 |
| C. Render / Railway 等 PaaS | GitHub 仓库 + 账号（可能绑卡） | `https://xxx.onrender.com` | 免费档会休眠；付费约 $7/月 | 中 | 不想管服务器 |
| D. Hugging Face Spaces | GitHub 账号（**无需信用卡**） | `https://用户名-space.hf.space` | 0 元 | 中（48 小时无访问休眠，唤醒 30–60s） | 学生、找不到云额度时 |

> 竞赛要求「评审期间保持服务可用」，**建议 A**；时间紧可先用 B 打通，再把同一镜像迁到 A。

## 方案 A0：学生零成本路线（GitHub Student Pack → 海外小机器 + 免费域名）

> 已提供一键脚本：`sudo bash deploy/bootstrap.sh [域名]`，会一次完成依赖安装、systemd 服务与 Caddy HTTPS 配置（幂等，可重复执行）。加评审口令：`sudo AUTH_USER=judge AUTH_PASS='口令' bash deploy/bootstrap.sh`。手工步骤仍保留在下方，便于逐步排查。

如果你有学生身份，这条路线可以做到**不买域名、不备案、不花钱**，地址还是 HTTPS：

1. **申请 GitHub Student Developer Pack**（<https://education.github.com/pack>）：用学校邮箱 + 学生证/学信网截图，通常 1–3 天通过。
2. 在 Pack 里领取 **DigitalOcean** 额度（约 $200，以页面显示为准；另有 Azure $100、Namecheap 免费域名等）。
3. DigitalOcean 新建 **Droplet**：Ubuntu 24.04，**Singapore** 机房，$12/月档（2G 内存）最稳；$6/月 1G 档需加 2G swap（见下）。开启防火墙只放行 22/80/443。
4. **没有域名也能上 HTTPS**：用 `sslip.io` 把 IP 变成主机名。假设服务器 IP 是 `203.0.113.5`，主机名就是 `203-0-113-5.sslip.io`（把点换成短横线）。
5. 按下面「方案 A」第 2–6 步安装，Caddy 配置用现成模板：

```bash
sudo cp deploy/Caddyfile.sslip /etc/caddy/Caddyfile
sudo sed -i 's/203-0-113-5/你的IP点换短横线/g' /etc/caddy/Caddyfile
sudo systemctl reload caddy
curl -I https://203-0-113-5.sslip.io      # Let's Encrypt 自动签发
```

6. 有免费域名（Namecheap `.me` 一年）时更稳：域名 A 记录指向 IP，按方案 A 第 5 步即可。`sslip.io` 是第三方公共服务，仅建议评审期临时用。

1G 内存加 swap（可选）：

```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

> 海外机器不需要 ICP 备案，评审可直接访问；代价是延迟略高（新加坡到国内约 60–90ms），页面是服务端渲染好的 PNG，实际体验仍流畅。

## 方案 A：云服务器 + 域名（推荐）

> 最快路径：`sudo bash deploy/bootstrap.sh demo.example.com`，脚本会完成第 2–5 步；域名需先解析到本机，安全组放行 22/80/443。

### 1. 准备

- 服务器：Ubuntu 22.04/24.04，1 核 2G，20G 磁盘；安全组放行 22、80、443。
- 域名：A 记录指向服务器公网 IP（如 `agentlit.example.com`）。
  - 大陆服务器需 ICP 备案；不想备案可选香港/新加坡节点（延迟略高，但可访问）。
- 不需要 Docker；下面用 venv + systemd + Caddy（自动 HTTPS）。

### 2. 安装依赖并拉取代码

```bash
sudo apt update && sudo apt install -y git python3-venv python3-pip caddy
sudo useradd -r -s /bin/bash -d /opt/agentlit agentlit
sudo -u agentlit git clone https://github.com/cui1001/ai-literature-platform.git /opt/agentlit
cd /opt/agentlit
sudo -u agentlit python3 -m venv .venv
sudo -u agentlit .venv/bin/pip install -r backend/requirements.txt
sudo mkdir -p /var/lib/agentlit && sudo chown agentlit:agentlit /var/lib/agentlit
```

### 3. 先手工验证一次

```bash
sudo -u agentlit DATA_DIR=/var/lib/agentlit .venv/bin/python scripts/run_server.py --demo --host 127.0.0.1 --port 8000
# 另开终端：curl -s http://127.0.0.1:8000/api/health
```

首次会把 14 篇论文 + 1 份扫描件解析入库（约 60–90 秒），之后启动是秒级。

### 4. 注册为系统服务

```bash
sudo cp deploy/agentlit.service /etc/systemd/system/agentlit.service
sudo systemctl daemon-reload
sudo systemctl enable --now agentlit
sudo systemctl status agentlit --no-pager
```

### 5. 反向代理 + HTTPS

```bash
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile
sudo nano /etc/caddy/Caddyfile          # 把 competition.example.com 换成你的域名
sudo systemctl reload caddy
curl -I https://你的域名                 # 期望 200
```

如需评审口令，在 Caddyfile 中启用 `basic_auth`（`caddy hash-password` 生成哈希），并把账号写进 README「在线演示」。

### 6. 更新与备份

```bash
cd /opt/agentlit && sudo -u agentlit git pull
sudo systemctl restart agentlit        # data 在 /var/lib/agentlit，不受影响
sudo tar czf /root/agentlit-data-$(date +%F).tgz -C /var/lib agentlit
```

## 方案 D：Hugging Face Spaces（免费、无需信用卡）

GitHub Student Pack 里的 DigitalOcean 名额会随地区/时间调整，找不到时这是最省事的 0 成本方案：
免费 CPU 档、自带公网 HTTPS、不需要域名和备案、不需要绑卡。

1. 打开 <https://huggingface.co/new-space>，SDK 选 **Docker** → Blank，命名如 `agentlit-demo`，可见性 Public。
2. 把仓库里的 `deploy/huggingface/` 两个文件推到该 Space（网页 *Files → Add file* 上传即可）：
   - `README.md`（含 `sdk: docker`、`app_port: 8000` 元数据）
   - `Dockerfile`（启动时从 GitHub 拉 `master` 并跑 `run_server.py --demo`）
3. 等待 Building → Running（首次构建 3–6 分钟，启动后解析 15 个 PDF 再花 60–90 秒）。
4. 地址是 `https://<用户名>-<space名>.hf.space`，直接写进 README「在线演示」。

限制：免费 CPU 档 48 小时无访问会休眠（首次访问唤醒 30–60 秒）；容器重建后 `/data` 重置，演示数据会自动重新导入，不影响功能。若评审要求 7×24 不休眠，用方案 A 的服务器。

## 方案 B：Cloudflare Tunnel（无需服务器，5 分钟）

适合「现在就想给别人一个地址」，但**电脑必须保持开机联网**，quick 隧道每次重启地址会变。

### 临时地址（quick tunnel）

```powershell
winget install --id Cloudflare.cloudflared -e
cd D:\AI-Project\ai-literature-platform
python scripts/run_server.py --demo          # 终端 1：保持运行
cloudflared tunnel --url http://127.0.0.1:8000   # 终端 2：输出 https://xxx.trycloudflare.com
```

把输出的 `https://xxx.trycloudflare.com` 填进 README「在线演示」即可。

### 固定地址（命名隧道，需自有域名托管在 Cloudflare）

```powershell
cloudflared tunnel login
cloudflared tunnel create agentlit
cloudflared tunnel route dns agentlit agentlit.example.com
# 按 deploy/cloudflared.yml 填写 tunnel id / 凭据路径后：
cloudflared tunnel run agentlit
```

设置开机自启：`cloudflared service install`（Linux）或任务计划程序（Windows）。

## 方案 C：Render / Railway（Docker 托管）

仓库已带 `Dockerfile` 与 `docker-compose.yml`，容器内 `DATA_DIR=/data`。

以 Render 为例：
1. New → Web Service → 连接 GitHub 仓库 `cui1001/ai-literature-platform`；
2. Runtime 选 **Docker**，健康检查路径 `/api/health`；
3. 添加 **Disk**，挂载路径 `/data`（否则重启数据丢失）；
4. 环境变量：`LLM_MODE=offline`、`EMBED_MODE=hashing`、`ARXIV_MODE=offline`（有密钥再改 `auto`）；
5. 部署完成后把 `https://xxx.onrender.com` 写入 README。
   免费档 15 分钟无访问会休眠，首次唤醒约 30–60 秒，**不建议直接用于评审**。

本地验证镜像：

```bash
docker compose up -d --build
curl http://127.0.0.1:8000/api/health
```

## 安全与运维要点

- **当前无登录**：公网暴露后任何人可上传/删除文献。正式评审建议在 Caddy 加 `basic_auth`，或启用 Cloudflare Access；账号写入 README「在线演示」。
- **上传大小**：默认单文件 ≤130 MB（`MAX_UPLOAD_MB`），公网演示建议保持默认。
- **数据备份**：`data/`（或 `/var/lib/agentlit`）含 SQLite、解析 JSON、页面图，定期打包即可。
- **健康检查**：`GET /api/health` 返回 `{"status":"ok"}`；`GET /api/system/status` 查看 provider 与索引状态。
- **离线优先**：不配密钥也能完整演示；配置 `DEEPSEEK_API_KEY` / `SILICONFLOW_API_KEY` 并设 `LLM_MODE=auto` 才走在线增强。
- **日志**：systemd 用 `journalctl -u agentlit -f`；Caddy 日志见 `/var/log/caddy/agentlit.log`。

## 验收清单（部署后逐条确认）

```bash
curl -s https://你的域名/api/health                 # status=ok
curl -s https://你的域名/api/system/status | head    # papers≥14、index coverage=1.0
curl -s -o /dev/null -w "%{http_code}\n" https://你的域名/   # 200
```

浏览器打开首页 → 文献库应显示 14 篇论文（含 1 篇 OCR 扫描件）→ 阅读/问答/图谱各点一次 → 把地址填入 README「在线演示」。
