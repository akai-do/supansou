# aliso.vip 前端技术画像

- 分析日期：2026-10-19
- 方法：公开页面、公开打包脚本静态分析、浏览器运行时观察
- 边界：未获取 source map、未登录、未访问管理后台、未执行页面中的非公开逻辑
- 说明：以下为观察和有限推断，不等同于对方构建配置或源码

## 1. 技术栈结论

| 维度 | 观察结果 | 证据 | 置信度 |
|---|---|---|---|
| 框架 | Vue 3（`data-v-app`、Vue runtime 指纹） | 首页 DOM、`index-BjJvZwZ3.js` | 高 |
| 构建工具 | Vite（Vite runtime 指纹、`/assets/` 哈希产物） | `analyze_web_bundle` 推断、HTML 引用 | 高 |
| 路由 | vue-router，history 模式 | 路由表、`/search?ai=` | 高 |
| 状态管理 | 未观察到 Pinia/Vuex；组件内状态 + localStorage | bundle、运行时存储 | 中高 |
| UI 框架 | 未观察到 Element Plus/Ant Design 等；使用原生 CSS + scoped style | DOM class、CSS 变量 | 中高 |
| 样式方案 | Vue SFC scoped CSS（`data-v-*`）、CSS 变量主题 | 首页/需求页 DOM、CSS | 高 |
| 二维码 | 前端 canvas/二维码库生成 DataURL | `Home.js` 中 `toDataURL`、二维码逻辑 | 高 |
| 分析/统计 | 百度统计 + MXA SDK | 首页 HTML | 高 |
| Source map | 未观察到可用 source map | `fetch_source_maps` 未返回映射 | 中（不能证明不存在） |

## 2. 构建产物与代码分割

首页加载的公开脚本（2026-10-19）：

| 文件 | 大小 | 作用（观察/推断） |
|---|---:|---|
| `/assets/index-BjJvZwZ3.js` | 103,191 B | 应用入口、路由、共享依赖 |
| `/assets/index-CWn9xag7.js` | 54,794 B | 共享 API 客户端/管理端请求封装 |
| `/assets/Home-Bv-pxTTG.js` | 82,742 B | 首页 + 搜索结果页逻辑 |
| `/assets/useSiteInfo-2Y6wwuGv.js` | 8,520 B | 站点信息/配置组合式函数 |
| 内联脚本（首页） | 642 B / 254 B / 31 B | 域名白名单、统计初始化等 |
| `/assets/index-Hw2riHaB.css` | 1,281 B | 基础样式（主样式可能由懒加载 chunk 注入） |

懒加载 chunk（从路由表观察）：

```text
Home-Bv-pxTTG.js
Demand-BspQUzDB.js
Admin-DzWDQEnr.js
Dashboard-BPmxIMNh.js
Resources-BALAGUmT.js
CloudDrive-B-NWTHNf.js
SourceCenter-CNHOLBVV.js
ApiConfig-BsmGVuvz.js
PansouManage-DVYMol_p.js
SearchRanking-hhQA81PV.js
Settings-4tIGL6BI.js
Demands-BbhyQkAu.js
```

样式：
- `Demand-Dcl1U1Br.css`：需求页 scoped 样式，含 768px / 380px 断点。
- 其他页面按需加载各自 CSS（Vite 代码分割）。

## 3. 路由结构

公开路由：

| 路径 | 名称 | 组件 | 说明 |
|---|---|---|---|
| `/` | Home | `Home-Bv-pxTTG.js` | 首页 |
| `/search` | Search | `Home-Bv-pxTTG.js` | 同一个组件处理搜索结果 |
| `/demand` | Demand | `Demand-BspQUzDB.js` | 需求页 |

管理路由（静态发现，未访问）：

| 路径 | 名称 | 说明 |
|---|---|---|
| `/admin` | Admin | 后台外壳 |
| `/admin/dashboard` | Dashboard | 控制台 |
| `/admin/resources` | Resources | 资源管理 |
| `/admin/clouddrive` | CloudDrive | 网盘管理 |
| `/admin/source-center` | SourceCenter | 源库管理 |
| `/admin/api-config` | ApiConfig | 接口配置 |
| `/admin/alipansou-manage` | AlipansouManage | 阿立盘搜管理 |
| `/admin/search-ranking` | SearchRanking | 搜索排名 |
| `/admin/settings` | Settings | 设置 |
| `/admin/demands` | Demands | 需求管理 |

说明：
- 首页和搜索结果复用同一个 `Home` 组件，根据 `route.path` 与 `route.query.ai` 切换视图。
- 搜索路由参数名为 `ai`，不是常见的 `q`/`kw`。
- 资源详情不是独立路由，而是弹窗状态。

## 4. 状态与本地存储

### 4.1 localStorage

| 键 | 作用 | 证据 |
|---|---|---|
| `alipansou_theme_mode` | 亮/暗主题 | 实际读写、`Home.js`/`Demand.js` |
| `alipansou_search_history` | 本机搜索历史 | 首页运行时 |
| `alipansou_search_inline_ad_last_shown` | 搜索结果内广告频率控制 | 首页运行时、`Home.js` |
| `alipansou_backend_url` | 后端地址（管理端/直连模式） | `index-CWn9xag7.js` |
| `alipansou_admin_url` | 管理端地址 | `index-CWn9xag7.js` |
| `admin_token` | 管理端 Bearer Token | `index-CWn9xag7.js` |
| `admin_user` | 管理端用户信息 | `index-CWn9xag7.js` |
| `alipansou_home_notice_seen_<siteName>` | 首页公告已读（sessionStorage） | `Home.js` |

### 4.2 sessionStorage

| 键 | 作用 |
|---|---|
| `Hm_lpvt_9ca8287327a3490853d346ab45af47a1` | 百度统计 |
| `HMACCOUNT` | 百度统计 |
| `alipansou_home_notice_seen_<siteName>` | 公告已读 |

### 4.3 URL 状态

- `/search?ai=<关键词>`：搜索关键词进入 URL，可分享/刷新。
- 筛选、排序、弹窗状态未观察到写入 URL；刷新后可能丢失。
- 需求页支持 `?title=<资源名>` 预填资源名称（脚本中读取 `route.query.title`）。

## 5. 请求层

### 5.1 公开搜索请求

- 使用原生 `fetch`。
- 流式搜索：`POST /api/stream-search`，读取 `response.body.getReader()`，按行解析。
- 回退：`POST /api/search`。
- 请求头：`Content-Type: application/json`、`X-Search-Token`（可选）。
- 请求体：`{ kw, src: "all" }`。
- 取消：`AbortController`。

### 5.2 配置/首页数据

- `GET /api/public/settings`
- `GET /api/public/netdisk-configs`
- `GET /api/enabled-check-services`
- `GET /api/hot-search-words?_=<timestamp>`
- `GET /api/search-history`
- `GET /api/resource-titles`
- `GET /api/notice`
- `GET /api/search-token`

### 5.3 资源与需求

- `POST /api/public/netdisk-click` `{ disk_type }`
- `POST /api/check-link-validity/batch` `{ items: [...] }`
- `POST /api/netdisk/transfer`（转存，条件启用）
- `GET /api/demands`
- `GET /api/demands/wanted`
- `POST /api/demand` `{ title, platforms, play_url, remark, contact, is_paid }`
- `POST /api/demand/{id}/want`

### 5.4 管理端请求

- `index-CWn9xag7.js` 中包含 axios 管理端客户端。
- 管理端 baseURL 来自 `localStorage.alipansou_admin_url`。
- 请求头注入 `Authorization: Bearer <admin_token>`。
- 401 时清除 `admin_token` / `admin_user`。
- 本次未调用任何管理端接口。

## 6. UI 结构与设计模式

### 6.1 首页

- 顶部 `banner`：导航链接 + 主题按钮。
- 主区：Logo、H1、公告、搜索框、三行跑马灯、运营卡片。
- 跑马灯结构（观察到的 class）：
  - `.marquee-row`
  - `.marquee-tag`
  - `.marquee-track`
  - `.marquee-content`（`left-slow` / `right` 方向）
  - `.marquee-word`（彩色搜索词）
- 搜索框：
  - `input[type=search]`
  - `.search` 容器
  - `.clear-btn`
  - `.btn`（放大镜图标，非 `<button>`）
- 广告：
  - `.home-promo-card` / `.home-promo-button`
  - `.search-inline-ad-preload`

### 6.2 搜索结果

- 状态条：`.stat-item`、`.stat-value`、`.stat-label`
- 排序：`.sort-select`（原生 `<select>`）
- 筛选：`.filter-btn`、`.active`
- 结果：
  - `.result-block`
  - `.result-item.resource-group-item`
  - `.item-title`
  - `.disk-icon-img`
  - `.get-btn`
  - `.resource-more-index`
- 弹窗：
  - `.dialog-overlay`
  - `.dialog-box`
  - `.dialog-close`
  - `.dialog-content`
  - `.qrcode-section`
  - `.link-section`
  - `.link-box`
  - `.save-btn`
  - `.copy-share-btn`
- 加载：
  - `.loading-quote`
  - `.spinner-center`
- 空态：
  - `.no-results-image`
  - `.demand-register-btn`

### 6.3 需求页

- Tabs：`.tab-switch`、`.tab-count`
- 表单：`.card`、`.card-body`、`.form-group`、`.platform-tags`、`.paid-toggle`、`.submit-btn`
- 列表：`.demand-list`、`.demand-item`、`.demand-index`、`.demand-info`、`.demand-title`、`.demand-meta`、`.want-count`
- 状态：`.list-loading`、`.empty-state`、`.toast`
- 移动端：`.mobile-menu-btn`、`.navs.mobile-open`

### 6.4 主题

- 根节点：`.home-page.theme-dark` / `.home-page.theme-light`
- 使用 CSS 变量（如 `--theme-header-bg`、`--theme-backdrop-blur`）
- 主题按钮：`.theme-toggle`，aria-label 随状态变化
- 无闪烁处理未观察到（本站已有防闪烁实现，可保留优势）

### 6.5 响应式

| 断点 | 观察到的行为 |
|---|---|
| `max-width: 768px` | 显示移动菜单按钮；隐藏横向导航；调整需求页 padding/标题/卡片圆角 |
| `max-width: 380px` | 隐藏平台徽章；缩小 meta 字号/间距 |

未观察到的：
- 首页/搜索页完整样式表断点（仅确认需求页 CSS）。
- 平板断点。
- 横屏/超宽屏适配。

## 7. SEO 与页面元信息

首页 HTML 包含：

- `<title>`：阿立盘搜-聚合网盘资源搜索助手-夸克/百度/迅雷/阿里/UC/115/123/天翼云盘/移动云盘等网盘资源
- `description`：支持多网盘平台和磁力链接的搜索服务
- `keywords`：网盘搜索、资源搜索、夸克网盘、阿里云盘、百度网盘等
- Open Graph：`og:title`、`og:description`、`og:image`、`og:url`、`og:type`
- Twitter Card：`summary_large_image`
- Canonical：`https://aliso.vip/`
- 内联域名白名单跳转脚本

推论：
- 对方重视搜索引擎收录和社交分享预览。
- 首页/结果页的标题没有按关键词动态变化（至少本次观察未发现）。
- 本站可在自己的 SEO 策略中按页面动态生成 title/description，但不要复制对方文案。

## 8. 第三方依赖与隐私观察

| 依赖/服务 | 用途 | 证据 | 建议 |
|---|---|---|---|
| 百度统计 `hm.baidu.com` | 访问统计 | 首页 HTML | 如需统计，优先自建或选择合规方案 |
| MXA `mxana.tacool.com` | 统计/分析 | 首页 HTML | 不建议照搬 |
| 二维码库 | 生成分享二维码 | `Home.js` 中 `toDataURL` | 本站可选轻量 QR 库 |
| 字体/图标 | 内联 SVG 为主 | DOM | 本站已有图标体系，无需替换 |

## 9. 公开 bundle 中可见的安全/隐私观察

> 以下只做事实记录，不构成漏洞结论，也不提供利用方法。

1. **管理端路由和接口名出现在公开 bundle 中**：包括 `/admin/*` 路由和大量 `/api/*` 管理接口名。它们是否受服务端鉴权保护，未验证。
2. **公开配置字段较多**：`GET /api/public/settings` 返回 `basic/general/search/seo/notice/template/backup/cleanup/rate_limit/log_retention/auto_blacklist` 等配置分组。本次只观察字段结构，未展开敏感值。
3. **网盘配置包含 `share_password` 字段**：`GET /api/public/netdisk-configs` 返回 15 个网盘配置，字段含 `enable_transfer`、`share_password`、`enable_check`、`expired_type` 等。具体值未记录。
4. **搜索令牌机制**：前端先获取 `/api/search-token`，再在搜索请求头中携带 `X-Search-Token`，用于基础反滥用。
5. **无 source map**：未发现可直接还原原始工程结构的 source map；公开的是压缩后的 Vite 产物。
6. **第三方统计脚本**：首页加载百度和 MXA 统计。

## 10. 未验证与限制

- 未获取 source map，无法确认原始组件命名、目录结构和构建配置。
- 未登录管理后台，无法验证后台页面、权限和数据范围。
- 未抓取 POST 响应体，无法确认流式搜索的完整分帧格式和所有字段。
- 未在移动视口逐屏截图，响应式结论主要来自 CSS 断点和既有对标文档。
- 未确认排行榜/分类页是否存在独立路由；公开路由中未发现 `/ranking`、`/category` 等页面，首页“🔥热门”承担了榜单入口的角色。

## 11. 对本站前端改造的启示

1. **技术栈相近**：本站 Vue 3 + Vite，可直接借鉴“页面/组件职责划分”，但不能复制代码或样式。
2. **搜索页状态机值得重构**：把“搜索中 / 已取消 / 完成 / 空结果”做成明确状态，配合计时器和结果计数。
3. **筛选/排序适合前端计算**：本站已有结果聚合，增加 `disk_counts` 和 `sort` 即可，不必重写搜索管线。
4. **弹窗/抽屉作为交付层**：本站 `ResourceCard` 已有操作按钮，可抽出 `ResourceDialog` 组件，增加二维码和变体切换。
5. **主题/响应式已有基础**：保留 `theme.js` 和现有 tabbar，修复图标和横向筛选即可。
6. **不要引入对方的重运营位和第三方统计**：按本站产品目标取舍。
