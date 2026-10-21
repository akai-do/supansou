# 移动端适配 + 手机 VPN 整站不可达修复计划

## 0. 已确认的结论（先看这一段）

**VPN 问题不是前端 bug。** 证据：
- `xiaowusu.com` 在全球 DNS 都解析到 `8.217.147.228`（Google DNS 实测）。
- 本机/国内直连正常；但助手侧外部出口访问 `https://xiaowusu.com/` 和 `/api/health` 均 60 秒超时，两个公共代理分别返回 500 / 522（连不上源站）。
- 结论：源站在 TCP/网络层对非国内/机房出口做了拦截，最可能是阿里云安全组、宝塔 WAF/云防火墙、主机 iptables 或 nginx geo/deny 规则。前端改任何代码都绕不过 TCP 层拦截，必须先修服务器网络 ACL。

**你已选择的移动端方案：**
- 首页三张能力卡「直链可用 / 有效性三态 / 本地索引」在移动端隐藏；首页「本地」快捷行也去掉；桌面端保持原样。
- 资源卡移动端：标题占满整行，状态/网盘/日期放第二行，同名变体第三行，底部整宽「获取资源」按钮（44px 高）。
- 目标：375×667 和 320×568 首屏内能看到「免责声明 / 版权说明 / 净网声明 + GitHub + 版权」，不需要下滑。

---

## 1. 实施顺序

1. 先修线上 VPN 访问（网络 ACL + 80→443），因为这是可用性问题。
2. 再做移动端首页、资源卡、弹窗和全站适配。
3. 本地构建 + 多设备浏览器验证。
4. 只上传 `frontend/dist` 到生产（前端是只读挂载，不需要重建镜像）；服务器配置改动先备份。

---

## 2. VPN 整站不可达：诊断与修复

### 2.1 SSH 只读诊断（先不改任何东西）

在 `root@8.217.147.228` 上执行并记录：

```bash
# 端口与监听
ss -lntp | grep -E ':80|:443|:5000|:8888'

# nginx 全量配置里的限制
nginx -T 2>/dev/null | grep -nE 'listen|server_name|allow |deny |geo |limit_req|limit_conn|return 444|ssl_reject|Alt-Svc|add_header|ssl_protocols|http3|quic'

# 主机防火墙
iptables -S | head -200
ip6tables -S | head -100
nft list ruleset 2>/dev/null | head -200
ufw status verbose 2>/dev/null
firewall-cmd --list-all 2>/dev/null
fail2ban-client status 2>/dev/null

# 宝塔/WAF 痕迹
ls /www/server/panel/plugin/ 2>/dev/null
ls /www/wwwlogs/ 2>/dev/null | head
tail -n 80 /www/wwwlogs/xiaowusu.com.log 2>/dev/null
tail -n 80 /www/wwwlogs/xiaowusu.com.error.log 2>/dev/null

# 服务器本机回环访问（确认 nginx/后端本身正常）
curl -sS -k -o /dev/null -w 'https local: %{http_code}\n' https://127.0.0.1/ -H 'Host: xiaowusu.com'
curl -sS -o /dev/null -w 'http local: %{http_code}\n' http://127.0.0.1/ -H 'Host: xiaowusu.com'

# 是否能用阿里云 CLI / 实例 RAM 角色改安全组
command -v aliyun || true
curl -s --max-time 2 http://100.100.100.200/latest/meta-data/ram/security-credentials/ || true
```

### 2.2 按拦截层修复（最小改动，先备份）

- **阿里云安全组 / 云防火墙**：检查入方向 443、80 是否被限制为“仅中国 IP”或某个固定 IP。公开站点应放行 `0.0.0.0/0` 的 TCP 443 和 80；如果不想全开，至少把常用 VPN 出口 CIDR 加入允许。
- **宝塔 WAF / 地区限制 / IDC 拦截**：关闭“海外访问限制”或把该 VPN 出口加入白名单；检查 fail2ban 是否误封。
- **主机防火墙**：删除针对非国内网段的 DROP/REJECT；改前 `iptables-save > /root/iptables-backup-$(date +%F-%H%M).txt`，改后持久化。
- **nginx**：移除 `deny`/`geo`/`return 444` 限制；补 80→443 301；确认 `listen 443 ssl`、TLS 1.2/1.3。改前 `cp` 站点配置到 `/root/nginx-backup-*`，`nginx -t` 通过后 `nginx -s reload`。
- **80 端口**：如果安全组未放行 80，先放行；然后在 nginx 加：
  ```nginx
  server {
      listen 80;
      server_name xiaowusu.com www.xiaowusu.com;
      return 301 https://$host$request_uri;
  }
  ```
  这同时解决手机输入不带 `https://` 的地址时首访挂住的问题。
- **HTTP/3（可选）**：如果防火墙修好后手机 VPN 仍有首访卡顿，再去掉 `Alt-Svc` 响应头（保留 TCP/QUIC 监听），避免 VPN 阻断 UDP 时浏览器先试 QUIC。
- **安全组是唯一拦截层且服务器没有 aliyun CLI / RAM 角色**：SSH 改不了安全组。我会把控制台需要加的那一条规则写清楚（入方向 TCP 443/80，源 `0.0.0.0/0`），其余能自动做的先做完，然后停下来等你点控制台。这是唯一可能需要你介入的点。

### 2.3 前端容错（配合，不是替代）

- `frontend/index.html`：加 `viewport-fit=cover`；加一个小脚本，非 localhost/内网地址从 `http:` 自动跳 `https:`（防止 VPN/WebView 不自动升级）。
- `frontend/src/site.js`：`/api/public/settings` 超时从 1.5s 放宽到 3s，避免高延迟 VPN 下配置接口被误杀。
- `frontend/src/pages/SearchPage.vue`：经典搜索重试从 2 次增加到 3 次，退避 400/800/1200ms；流式搜索的 20s 看门狗和回退逻辑保持不变。

### 2.4 验证

- 服务器本机 `https://127.0.0.1` 仍 200。
- 修复后助手侧外部出口再次访问 `https://xiaowusu.com/`，以及两个公共代理，应能拿到 200。
- 本机/国内直连仍 200。
- 你手机开 VPN 做最终确认（这一步只有你能做）。

---

## 3. 移动端首页首屏

### 3.1 文件
- `frontend/src/pages/HomePage.vue`
- `frontend/src/components/QuickDiscovery.vue`
- `frontend/src/style.css`
- `frontend/index.html`

### 3.2 改动
- `QuickDiscovery.vue`：给「本地」行加 `qd-row-local` 类，移动端隐藏；桌面保留。
- `HomePage.vue`：移动端（`max-width: 768px`）跳过 `/api/resource-titles` 请求，避免为隐藏的「本地」行白跑一次接口；桌面仍加载。
- `style.css`（≤768px）：
  - `.home-features { display: none; }`（三张能力卡只在移动端隐藏，桌面不变）。
  - `.qd-row-local { display: none; }`。
  - `.home-page { padding: 8px 0 0; }`（去掉与 `.content` 叠加的 14px，修掉 320px 屏约 2-4px 横向溢出）。
  - `.home-hero { min-height: 0; justify-content: flex-start; padding: 10px 0 6px; }`。
  - 品牌压缩：Logo 44px、站名 24px、副标题 12px；搜索框 50px 高、宽度 100%；热门/历史两行，行高 34px。
  - `.content` 底部 padding 从 80px 降到 12px（原来 80px + 页脚 64px 是双份留白，正是把页脚推下去的主因）。
  - `.site-footer` 压缩：`margin-top: 10px`，`padding-bottom: calc(58px + env(safe-area-inset-bottom) + 6px)`；`sf-inner` 12px；法律链接换行；GitHub 行保持在首屏。
  - `.tabbar` 高度改为 `calc(58px + env(safe-area-inset-bottom))`，`padding-bottom` 用安全区。
  - ≤360px 再收紧一档（Logo 38px、站名 21px、搜索框 46px、行高 30px、页脚 padding 10px），保证 320×568 也尽量一屏。
- `frontend/index.html`：`viewport-fit=cover`、`color-scheme`，让安全区真正生效。

**验证目标**：375×667 与 320×568 下，首页首屏（固定 tabbar 上方）能看到「净网声明 / 版权说明 / 免责声明 + GitHub + 版权」；桌面首页布局与现在完全一致。

---

## 4. 移动端资源卡与弹窗

### 4.1 资源卡 `frontend/src/components/ResourceCard.vue` + `style.css`
- 保留桌面 DOM 和视觉；移动端 `.res-head { display: contents; }`，把 `.res-heading`、`.res-get` 变成 `.res-body` 的直接 flex 子项。
- 移动端顺序：标题（整行、两行截断、14px）→ 状态/网盘/日期 → 同名变体 → 整宽「获取资源」。
- 状态徽章不再放在标题前：移动端隐藏标题行徽章，在 `.res-meta` 里放一个同款徽章；桌面仍显示原位置（保留既有视觉基线）。
- 按钮：`width: 100%`、`min-height: 44px`、14px 字号、圆角 12px。
- 变体数字按钮从 24px 提到 32px，允许换行，避免横向滚动藏内容。
- **修正有效性误导**：删掉 `v-else-if="current.alive === 1"` 显示「有效」的兜底。未检测（`validity === ''`）不再伪装成有效；桌面也一起修正。这是修正错误，不是改版。

### 4.2 资源弹窗 `frontend/src/components/ResourceDialog.vue` + `style.css`
- 打开时锁定 `body` 滚动，关闭/卸载恢复；避免底部 sheet 打开后背景还能滑。
- 移动端 `max-height: 92dvh`（带 `92vh` 回退）。
- 关闭按钮 40×40；操作按钮 `min-height: 44px`、字号 14px。
- 移动端 `.rd-body` 改为横向：二维码 112×112 在左、链接/提取码在右，操作区紧随其后，保证「打开链接 / 复制链接 / 复制提取码」无需下滑就能看到。
- 二维码提示文字 9-10px；弹窗说明 11px。
- 保留现有「直链与复制是主路径」文案，不把直链直接搬到卡片上（按你选的布局）。

### 4.3 触控与 hover
- 新增 `@media (hover: none)`：去掉卡片/按钮/chip 的 hover 位移动画，补 `:active` 缩放反馈。
- 所有次要操作（隐藏失效「查看」、分页、开关）在移动端至少有 32px 可点高度；主操作 44px。

---

## 5. 移动端全站适配

- **搜索页**：筛选 chip 最低 34px；开关行最低 32px、复选框 16px；分页按钮 36×36，允许换行；隐藏失效条链接加 32px 点击区；输入框统一 16px（解决 iOS 聚焦自动放大）。
- **统计页**：4 张统计卡在移动端改 2 列；16 列、`min-width: 1440px` 的来源表在移动端隐藏，改为每来源一张卡片，2 列展示评分/收录/已检测/死链率/疑似率/独家率/曝光/点击/CTR/点击占比/每百条点击/7日新增/30日新增/最近复查。桌面仍用原表格。
- **下载提速页**：增加移动端提示（手机仅可查看，提速需 Windows 电脑）；`.bst-top`、`.bst-targets`、`.bst-controls` 允许换行；滑块宽度 100%；速度大字不再固定 150px；目标进程行加 `min-width: 0` 和省略，消除横向溢出。
- **页脚**：`/search` 的极简页脚补 GitHub 链接（带星标），与首页一致；完整页脚在移动端压缩，法律链接和版权行可换行。
- **主题**：`theme.js` 切换亮/暗时同步 `meta[name=theme-color]`（暗色 `#050608`，亮色 `#0071e3`）。
- **其他公开页**（豆瓣、法律、需求板）：做基础溢出、输入框 16px、按钮触控高度检查；管理后台不在本次移动端适配范围内。

---

## 6. 验证与交付

### 6.1 构建
```bash
export PATH="/c/Users/LX/AppData/Local/Programs/nodejs:$PATH"
cd frontend && npm run build
```
构建产物会更新被 git 跟踪的 `frontend/dist/`；不提交 git。

### 6.2 本地验证
- 确认 `127.0.0.1:5000` 的 Docker 容器在跑；没跑就 `docker compose up -d`（前端是挂载卷，构建后刷新即可）。
- 用浏览器自动化覆盖：
  - 320×568、375×667、390×844、414×896、768×1024，以及 844×390 横屏。
  - 每个页面断言 `document.documentElement.scrollWidth <= window.innerWidth`。
  - 首页断言 `.home-features` 和 `.qd-row-local` 不可见；`.sf-links`/`.sf-bottom`/`.sf-repo` 的 `getBoundingClientRect().bottom` 在首屏可视区内（扣除 tabbar 后）。
  - 搜索页跑一次真实关键词，断言卡片标题宽度、按钮高度 ≥44、同卡无横向溢出；打开弹窗断言操作按钮可见、`body` 已锁定滚动。
  - 统计页断言表格隐藏、来源卡片可见；提速页断言无横向滚动。
- 截图 375 和 390 两档，人工看一眼视觉层级。

### 6.3 生产发布
- 只上传 `frontend/dist` 到 `/opt/dupan/app/frontend/dist`（只读挂载，换文件即生效，不需要重建镜像）。
- 服务器配置改动前备份 nginx / iptables；改完 `nginx -t && nginx -s reload`。
- 外部出口 WebFetch + 公共代理复测；国内直连复测；你手机 VPN 最终确认。

---

## 7. 风险、回滚与非目标

- **回滚**：nginx 配置备份在 `/root/nginx-backup-*`；iptables 备份在 `/root/iptables-backup-*`；前端 dist 保留上一版即可回滚。
- **不影响**：后端搜索管线、本地索引混排逻辑、桌面首页三张能力卡、桌面资源卡布局、管理后台。
- **唯一可能需要你介入**：如果拦截在阿里云安全组且服务器没有 CLI/RAM 角色，需要你在控制台加一条入方向规则（TCP 443/80，源 0.0.0.0/0）。其余诊断、修复、验证我会先做完。
- **不做**：不删除后端本地索引库（它还存 validity/巡检/统计）；不把「本地索引」从搜索结果中彻底移除（你选的是移动端展示层）；不改管理后台移动端。