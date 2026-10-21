# 迁移到七牛云 LAS（香港 2）

> 目标：把 `https://xiaowusu.com/` 从阿里云香港 ECS 迁到七牛云全栈应用服务器 LAS。
> 旧机全程保留，DNS 切回即回滚。

## 0. 已经准备好的东西

旧服务器 `/root/migrate-20261021/`（目录不要删，回滚也用得上）：

| 文件 | 内容 | SHA-256 |
|---|---|---|
| `index.db` | 一致性备份：索引 + 需求板 + 站点配置 + 统计 | `7bb10dca…a71becc` |
| `accel.db` | 加速器账号/口令/配额 | `8c2ae3e0…74906f` |
| `data.tar.gz` | `backend/data` 其余文件（imgcache、加速器 zip、parse_pass.txt） | `2b20b331…93c3b7` |
| `site.tar.gz` | compose、override、后端源码、`frontend/dist` | `fdac331d…25889a` |
| `nginx.tar.gz` | 旧 nginx 配置 + 现有证书 | `914aa261…e74e7` |

线上实际部署目录：`/www/wwwroot/dupansou`；容器：`dupansou-backend`、`pansou`；后端端口 5000（当前只绑 `127.0.0.1`，迁移脚本会改成 `0.0.0.0`）。

## 1. 从七牛控制台拿三样东西

1. 公网 IP；
2. SSH 端口（共享 IP 不是 22，控制台显示为 `IP:端口`）；
3. root 密码（创建时设置的那个）。

另外记下「域名访问」页给出的 **CNAME 目标**。

## 2. 传包（在旧服务器上执行，HK→HK 直传最快）

```bash
# 在旧服务器 root@8.217.147.228 上执行
NEW_IP=七牛公网IP
NEW_PORT=七牛SSH端口

ssh -p "$NEW_PORT" root@"$NEW_IP" 'mkdir -p /root/migrate-20261021'
scp -P "$NEW_PORT" /root/migrate-20261021/* root@"$NEW_IP":/root/migrate-20261021/
```

第一次连新机会提示 `yes/no` 和密码。若旧机连不上新机，退回本机中转：

```bash
# 本机 Git Bash
scp -r root@8.217.147.228:/root/migrate-20261021 ./migrate-bundle
scp -P 七牛SSH端口 -r ./migrate-bundle/* root@七牛IP:/root/migrate-20261021/
```

## 3. 新机初始化（一键脚本）

脚本已随包放在 `/root/migrate-20261021/qiniu_bootstrap.sh`，它会：

- 设时区、建 2G swap（2G 内存跑 PanSou 建议有）；
- 用阿里云镜像装 Docker + compose 插件；
- 还原站点和数据到 `/www/wwwroot/dupansou`；
- 把 5000 端口绑定从 `127.0.0.1` 改成 `0.0.0.0`；
- `docker compose up -d --build` 并等 `/api/health` 通过。

```bash
ssh -p 七牛SSH端口 root@七牛IP 'bash /root/migrate-20261021/qiniu_bootstrap.sh'
```

首次构建 3–8 分钟。看到 `{"status":"ok"...}` 即本机自检通过。

## 4. 七牛控制台配置

1. **端口映射**：添加规则，内网端口 `5000`、TCP。系统会分配一个公网端口，用于切换前测试：
   ```bash
   curl http://七牛IP:分配的公网端口/api/health
   ```
   （单实例最多 10 条映射，SSH 已占一条。）
2. **域名访问**：内网端口填 `5000`，先绑 `www.xiaowusu.com`。
3. **HTTPS**：优先用七牛自动签发；若必须上传，证书在旧机
   `/www/server/panel/vhost/cert/`（已打进 `nginx.tar.gz`），两张证书分别只覆盖 `xiaowusu.com` 和 `www.xiaowusu.com`。
4. 验证 `https://www.xiaowusu.com` 能打开、证书有效，再动 DNS。

> ⚠️ 旧站已经下发 HSTS（`max-age=31536000`）。**证书没就绪就切 DNS，老访客会直接打不开页面**，务必先验证 HTTPS。

## 5. DNS 切换

当前 DNS 在万网（`dns15/16.hichina.com`），A 记录指向 `8.217.147.228`。

- 切换前把 TTL 调到 600，等旧 TTL 过期。
- **`www`**：加 CNAME 到七牛给的目标，直接可切换。
- **apex（`xiaowusu.com`）**：万网不支持顶点 CNAME，三选一：
  1. **推荐**：把 NS 迁到 Cloudflare（免费），apex 用 CNAME 拉平，记录设为 **DNS only（灰云）**，保留 apex 作为主域名，也不改变现有链接；
  2. 只迁 `www`，apex 用万网的「显性 URL 转发」跳到 `https://www.xiaowusu.com`（需实测它是否支持 HTTPS 跳转）；
  3. 后续在七牛买独立 IP，apex 直接用 A 记录。

## 6. 验收清单

```bash
curl -sI https://www.xiaowusu.com/ | head -5
curl -s  https://www.xiaowusu.com/api/health
# SSE 流式搜索：应逐条吐 data:，而不是憋到最后一次性输出
curl -N -s -X POST https://www.xiaowusu.com/api/search/stream \
  -H 'Content-Type: application/json' \
  -d '{"keyword":"庆余年","page":1,"page_size":10}' | head -5
```

再手工过一遍：

- [ ] 搜索有结果、能翻页、能打开详情弹窗
- [ ] 封面图打开（设置里勾「封面图」后）
- [ ] `/api/agent` 能下载 13MB 加速器包
- [ ] 站长后台能登录（密码在 `docker-compose.override.yml`，不要外发）
- [ ] 需求板能提交、能评论
- [ ] 手机流量（非 WiFi）访问一次，确认外网可达

**SSE 如果被网关缓冲**（表现为搜索一直转圈、最后一次性出结果）：在 `docker-compose.override.yml` 的 `backend.environment` 加 `STREAM_SEARCH: "0"`，然后 `docker compose up -d`，先走普通搜索。

**客户端 IP 校验**：项目靠 `X-Forwarded-For` 还原访客 IP。看一次后端日志，如果所有请求的 IP 都是网关地址，说明七牛没传 XFF；当前生产 `ACCEL_GUEST=0`，影响不大，但统计和限流会失真。

## 7. 切换前 10 分钟再做一次增量同步

备份是 10-21 00:20 的，之后的新索引、评论、需求会丢。切 DNS 前在**旧服务器**执行一次（已备好脚本，会自动备份→scp→停新机后端→换库→起后端→健康检查）：

```bash
bash /root/migrate-20261021/final-sync.sh
```

看到 `{"status":"ok"...}` 且打印“同步完成”即可去改 DNS。这一步会覆盖新机自己收割的索引（可再收割），但保住旧机上最新的用户数据。

## 8. 回滚

把 DNS 改回 `8.217.147.228`，旧机容器还在跑，最多等 TTL。确认新站稳定 3–7 天后再释放旧 ECS（按量付费每天约 3.3 元，别忘停）。

## 9. 迁移后的注意事项

- **20GB 系统盘**：Docker 构建缓存 + 镜像约占 2–3GB，留神 `df -h`。需要时 `docker system prune -af`；`downloads/` 不要放系统盘。
- **2GB 内存**：已建 2G swap；若 PanSou 频繁 OOM，把 `ASYNC_MAX_BACKGROUND_WORKERS` 从 8 降到 4。
- **共享 IP**：公网端口由平台分配，不能自己指定；域名必须走 CNAME，A 记录无效。
- **续费**：这个套餐首年 66 元，续费按公开价（约 428 元/年），别按 66 做长期预算。
- **大陆线路**：七牛页面已提示香港 2 区「大陆访问延迟较高或受限」。迁移后第一周重点看晚高峰打开速度，不达标就回滚，旧机先别释放。
