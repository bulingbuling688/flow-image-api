# Flow 生图 API

## 在线地址

正在部署，预定地址为 `https://flow-image-api.chatapi.fun`。

## 项目简介

Flow 生图 API 把本机已登录的 Google Flow 生图能力封装为带鉴权的公网任务接口。VPS 负责接收请求、保存任务和托管结果，Windows Worker 负责调用 `gflow-cli` 与真实 Chrome 完成图片生成。调用方不需要接触 Google 登录态，也不需要与生成电脑处于同一网络。

## 解决的问题与目标

`gflow-cli` 原生提供 CLI 和 MCP/SSE，但没有适合普通 HTTP 节点直接调用的 REST API，而且 Google Flow 登录依赖真实 Chrome。项目将公网 API 与生成节点分离：API 可以持续接收任务，Google Cookie 只保留在 Windows 生成电脑上。Windows 电脑离线时任务会保存在队列中，恢复上线后继续执行；本项目不承诺 Google 私有接口的生产级 SLA。

## 核心功能

- 使用独立 Bearer Token 保护调用接口和 Worker 内部接口。
- 通过 `POST /v1/images/generations` 创建单张生图任务。
- 使用 SQLite 持久化任务状态，支持 Worker 中断后的租约恢复。
- 支持 `nano2`、`nano-pro`、`image4` 模型与五种画幅。
- 通过鉴权接口查询状态和下载生成图片。
- 串行执行 `gflow`，避免同一 Chrome 配置并发锁冲突。
- 提供 Worker 心跳和公开健康检查。

## 我的工作

- 设计公网 API 与本地生成 Worker 的拆分架构。
- 实现 Bearer 鉴权、SQLite 队列、任务租约、失败状态和图片存储。
- 实现 Windows Worker 对现有 `gflow-cli` 的安全适配，提示词通过标准输入传递。
- 编写接口测试、Windows 自动启动脚本、VPS systemd 与 Nginx 配置。
- 负责 GitHub、VPS、Cloudflare DNS、HTTPS 和公网端到端验证。

## 系统架构

```text
任意电脑 / 扣子 HTTP 节点
        |
        | HTTPS + API Bearer Token
        v
Cloudflare -> Nginx -> FastAPI -> SQLite 队列 / 图片文件
                              ^
                              | HTTPS + Worker Token
                              |
                    Windows Worker -> gflow-cli -> Google Flow
                                            |
                                      真实 Chrome 登录态
```

Google Cookie、Flow 会话和 Worker Token 不进入 GitHub，也不会由公共接口返回。

## 技术栈

| 类别 | 技术 | 用途 |
| --- | --- | --- |
| API | FastAPI | HTTP 接口、鉴权与 OpenAPI 文档 |
| 客户端 | HTTPX | Windows Worker 与公网 API 通信 |
| 存储 | SQLite | 持久化任务队列和状态 |
| 生成 | gflow-cli / Google Flow | 调用 Flow 生图能力 |
| 运行 | systemd | 管理 VPS API 服务 |
| 网关 | Nginx | HTTPS 反向代理 |
| DNS/CDN | Cloudflare | 域名解析与代理 |

## 项目截图

当前仓库暂未提供项目截图；API 状态可通过 `/docs` 和 `/health` 检查。

## 本地运行

需要 Python 3.11 或更高版本：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
set -a
. ./.env
set +a
uvicorn flow_image_api.asgi:app --host 127.0.0.1 --port 18446
```

创建任务：

```bash
curl -X POST https://flow-image-api.chatapi.fun/v1/images/generations \
  -H "Authorization: Bearer $FLOW_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"prompt":"a red apple on a white table","model":"nano2","aspect_ratio":"16:9"}'
```

接口返回 `202` 和任务 ID。随后使用同一 Bearer Token 请求 `/v1/jobs/<job_id>`；状态为 `succeeded` 后访问 `/v1/images/<job_id>` 下载结果。

## 环境变量

| 变量名 | 用途 | 是否必需 | 示例 |
| --- | --- | --- | --- |
| `FLOW_DATABASE_PATH` | SQLite 文件路径 | 是 | `/opt/apps/flow-image-api/shared/flow-image-api.db` |
| `FLOW_IMAGE_DIR` | 服务端图片目录 | 是 | `/opt/apps/flow-image-api/shared/images` |
| `FLOW_API_KEY_SHA256` | 调用者 Token 的 SHA-256 | 是 | `replace-with-sha256` |
| `FLOW_WORKER_TOKEN_SHA256` | Worker Token 的 SHA-256 | 是 | `replace-with-sha256` |
| `FLOW_PUBLIC_BASE_URL` | 公网基础地址 | 是 | `https://flow-image-api.chatapi.fun` |
| `FLOW_API_BASE_URL` | Worker 请求的 API 地址 | Worker 是 | `https://flow-image-api.chatapi.fun` |
| `FLOW_WORKER_TOKEN` | Worker 私密 Token | Worker 是 | `replace-with-private-token` |
| `FLOW_GFLOW_RUNNER_PS1` | D 盘 gflow 启动器 | Worker 是 | `D:\workspace\bin\gflow-run.ps1` |
| `FLOW_GFLOW_PROFILE` | 已验证的 gflow 配置名 | Worker 是 | `your-profile` |

真实 Token 仅保存在 VPS 环境文件和 D 盘私密配置中，不写入仓库。

## 部署信息

| 字段 | 内容 |
| --- | --- |
| 中文项目名 | Flow 生图 API |
| GitHub 仓库名 | `flow-image-api` |
| GitHub 仓库 | `https://github.com/bulingbuling688/flow-image-api` |
| Git 分支 | `main` |
| 包管理器 | `pip` |
| 安装命令 | `python3 -m pip install -r requirements.txt` |
| 上线代码提交 | 正在部署 |
| 文档提交 | 正在部署 |
| VPS 项目目录 | `/opt/apps/flow-image-api` |
| 源码目录 | 不适用（本地归档发布） |
| 版本目录 | `/opt/apps/flow-image-api/releases` |
| 当前版本 | 正在部署 |
| 上一版本 | 不适用 |
| 静态入口 | 不适用 |
| 运行方式 | systemd |
| 构建命令 | 不适用 |
| 启动命令 | `.venv/bin/uvicorn flow_image_api.asgi:app --host 127.0.0.1 --port 18446` |
| 内部端口 | `127.0.0.1:18446` |
| 在线域名 | `https://flow-image-api.chatapi.fun` |
| Nginx 配置 | `/etc/nginx/sites-available/flow-image-api.conf` |
| Cloudflare DNS | 正在部署 |
| HTTPS | 正在部署 |
| 环境文件 | `/etc/flow-image-api.env` |
| 更新方式 | 重新运行 `github-vps-domain-publish` |
| 回滚方式 | 首次发布后补充 |

## 目录结构

```text
flow_image_api/       FastAPI、SQLite 队列和 Windows Worker
tests/                API 生命周期与安全测试
scripts/              Worker 启动、自动运行与公网冒烟脚本
deploy/               systemd、HTTP 引导和 HTTPS Nginx 配置模板
.env.example          VPS 环境变量示例
worker.env.example    Windows Worker 配置示例
```

## 常用命令

```bash
python -m unittest discover -s tests -v
git -C /opt/apps/flow-image-api/source status --short
git -C /opt/apps/flow-image-api/source branch --show-current
readlink -f /opt/apps/flow-image-api/current
journalctl -u flow-image-api -n 80 --no-pager
```

Windows Worker 日志默认位于 `D:\workspace\state\flow-image-api\worker.log`。

## 维护记录

- 2026-07-18：实现首版 API、持久任务队列、Windows gflow Worker 和部署配置，正在发布。

## 开源许可与第三方说明

本项目代码采用 [MIT License](LICENSE)。项目通过外部安装的 `gflow-cli` 调用 Google Flow，不复制其源代码；`gflow-cli` 是非官方的第三方项目，Google Flow 私有接口变化可能影响可用性。使用时需要遵守 Google 的服务条款和生成式 AI 使用政策。
