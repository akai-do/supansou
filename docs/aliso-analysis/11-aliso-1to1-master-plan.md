# aliso.vip 1:1 复刻总计划（人话版）

- 日期：2026-10-19
- 适用对象：准备把本地 `DuPanSou-Archive` 做成 aliso.vip 主站体验的开发者/负责人
- 先说清楚：**1:1 指页面结构、功能、交互流程、信息架构对齐，不是复制对方的代码、CSS、图片、图标、字体和文案。**
- 本文是“总纲”，用来回答“要做什么、先做什么、怎么做、做到什么算完成”。
- 证据细节看 `09-unified-aliso-playbook.md`；逐任务施工看 `10-unified-implementation-plan.md`；文档之间的关系看 `README.md`。

---

## 0. 一页结论

### 0.1 你要做的事

把现在的 DuPanSou：

```text
/resource 搜索页 + /douban + /accel + /guest + /booster + /stats
```

改造成 aliso 主站的结构：

```text
/               首页（大搜索 + 公告 + 热门/历史/本地 + 页脚）
/search?ai=...  搜索结果页（状态条 + 排序 + 网盘计数 + 卡片 + 同名变体 + 弹窗）
/demand         需求板（提交需求 + 历史需求 + 想要投票）
/admin/*        管理后台（登录 + 控制台 + 资源/网盘/源库/接口/排名/设置/需求）
/legal/*        净网/版权/免责声明
```

同时保留你现在的差异化能力：**直链、有效性三态、本地索引/收割、百度加速、豆瓣、统计**。

### 0.2 先不做的东西（按你的要求）

- 广告位：首页 banner、搜索中广告、结果底部广告、广告预览弹窗。
- 分享获益/邀请注册：不做邀请码、返利、注册引导。
- 动画/少儿资源库：不做 `zy.aliso.vip` 这类子站。
- 视频解析子站：建议先不做 `hbh.aliso.vip`；导航里可以先隐藏。
- 微信小程序：先不做；移动端用响应式网页 + 二维码。
- 外部友链：先不做；页脚保留 GitHub、法律页即可。

这些功能在代码里可以留“配置开关/占位”，但默认关闭，不影响主流程。

### 0.3 时间预估

一个人全职、熟悉现有代码的情况下：

| 阶段 | 内容 | 时间 |
|---|---|---|
| 阶段 0 | 准备、数据库备份、路由/开关设计 | 2~3 天 |
| 阶段 1 | 新外壳 + 首页 | 1 周 |
| 阶段 2 | 搜索结果页（状态条/计数/排序/变体/弹窗） | 1.5~2 周 |
| 阶段 3 | 需求板 + 法律页 | 1 周 |
| 阶段 4 | 管理后台 MVP（登录/控制台/资源/需求/设置） | 2 周 |
| 阶段 5 | 管理后台扩展（网盘/源库/接口/排名）+ 流式搜索 | 1.5~2 周 |
| 阶段 6 | 回归、移动端、上线 | 1 周 |

合计约 **7~9 周**。两个人并行（一人前端、一人后端/数据）可以压到 **5~6 周**，但管理后台和联调仍然是瓶颈。

### 0.4 第一个里程碑（2 周内）

做完这 7 件事，用户已经能明显感觉“像 aliso 了”：

1. 首页有 Logo、公告、大搜索框、热门/历史/本地三行。
2. `/search?ai=` 能搜，旧 `/resource?kw=` 不失效。
3. 状态条显示结果数、网盘类型数、耗时、进行中/完成。
4. 网盘 chips 带数量，支持多选。
5. 排序（默认/时间/名称）写进 URL。
6. 同名变体用编号切换。
7. 移动端 tabbar 图标、搜索框同步、筛选横滑修好。

二维码、公告、法律页可以并行做，不阻塞上面的主流程。

---

## 1. 现有文档都是什么（一句话版）

| 文档 | 一句话解释 | 现在还要不要看 |
|---|---|---|
| `../04-aliso公开页面对标与改进规划.md` | 最早的产品/交互人工对标：aliso 首页、搜索、弹窗、需求板、移动端长什么样，以及本站当时怎么改 | 看产品判断和移动端观察；事实以 09 为准 |
| `01-information-architecture.md` | aliso 的站点地图：首页、搜索、需求、后台分别有哪些页面、怎么跳转 | 查页面结构时看 |
| `02-feature-inventory.md` | 把 aliso 的功能拆成 H/S/R/D/G 编号，列了优先级和本站现状 | 查“这个功能叫什么、依赖什么”时看 |
| `03-interaction-flows.md` | 搜索、弹窗、需求板的操作流程和失败降级 | 做交互时看 |
| `04-frontend-profile.md` | aliso 的前端技术画像：Vue3/Vite、chunk、localStorage、请求层、断点 | 想了解对方怎么实现时看 |
| `05-public-api-observations.md` | 公开只读接口的字段、搜索/需求接口、管理端路由的静态发现 | 设计接口时看 |
| `06-gap-analysis.md` | PanSou 上游 / aliso / 本站三方能力矩阵 | 判断“我们缺什么、有什么优势”时看 |
| `07-implementation-plan.md` | 旧版实施计划：阶段、任务、测试、回滚 | 参考测试/回滚思路；具体任务以 10 为准 |
| `08-comparison-matrix.md` | 两份文档逐主题对比、14 条冲突怎么解决、本站现状抽查 | 查“为什么这么定”时看 |
| `09-unified-aliso-playbook.md` | 统一版手册：事实、功能、流程、接口、差距、风险、未验证清单 | **事实依据**；做决策前查 |
| `10-unified-implementation-plan.md` | 详细任务清单：P0/P1/P2，每项含文件、验收、回滚、风险 | **施工细节**；写代码时查 |
| `README.md` | 文档索引和维护规则 | 找文件时看 |

**你只需要记住：**

- 想快速理解全局 → 看这份 `11`。
- 想知道 aliso 到底做了什么、证据是什么 → 看 `09`。
- 想查具体某个任务怎么改 → 看 `10`。
- 想知道两边文档哪里冲突 → 看 `08`。

---

## 2. aliso 主站到底长什么样（人话版）

> 以下行为来自 2026-10-19 的公开页面/公开 bundle/公开只读 GET 观察；完整来源见 09 §3~§6。这里只讲“用户看到什么”。

### 2.1 首页

从上到下是 5 块：

1. **顶部工具条**
   - 视频在线解析（外站）
   - 提交需求
   - 分享获益（邀请注册）
   - 阿立少儿动画资源库（外站）
   - 亮/暗主题切换

2. **主搜索区**
   - Logo + 站点名
   - 公告文字
   - 大搜索框 + 放大镜按钮
   - 输入关键词后跳到 `/search?ai=关键词`

3. **三行快捷发现**
   - 🔥 热门：站内热搜词
   - 📜 历史：搜索历史（具体是本机还是全站，未确认）
   - 📁 本地：本地资源标题
   - 横向滚动，点击直接搜索

4. **运营位**
   - 微信小程序 banner（强调能看到资源目录结构）
   - 搜索页内嵌广告、结果底部广告
   - 这些我们**先不做**

5. **页脚**
   - 友情链接
   - 净网声明、版权说明、免责声明
   - 版权信息

### 2.2 搜索结果页

从上到下是 7 块：

1. **搜索框**：回填关键词，可重新搜索。
2. **状态条**：当前显示多少条、多少种网盘、用时多少秒、是否还在搜索、可取消。
3. **排序下拉**：默认排序 / 按时间 / 按名称。
4. **网盘筛选 chips**：夸克、百度、迅雷、UC、阿里、光鸭、移动、天翼、115、123 等；每个 chip 带数量；单选。
5. **结果卡片**：有效性、标题、网盘类型、日期、获取资源按钮。
6. **同名更多结果**：同一资源的多个链接用 1、2、3…编号切换。
7. **资源获取弹窗**：二维码、百度提取码、资源标题、保存网盘、复制分享。

补充：

- 结果页没有独立详情路由，弹窗不改变 URL。
- 搜索是流式的：边搜边追加，计数持续增长。
- 失败会回退到一次性搜索。
- 空结果会提示“未找到相关资源”，并引导去需求板。

### 2.3 需求板

- 两个 Tab：提交需求 / 历史需求。
- 提交表单：资源名称（必填）、播放平台（必填）、播放地址、备注、联系方式、是否愿意付费。
- 历史需求：按想要数/时间排序，显示排名、标题、状态（已解决/待处理/已驳回）、日期、备注、解决说明/驳回原因、想要数。
- “想要”是公开投票，可切换。

### 2.4 管理后台

aliso 的 `/admin/*` 有 9 个模块：

- dashboard：控制台
- resources：资源管理
- clouddrive：网盘管理
- source-center：源库管理
- api-config：接口配置
- alipansou-manage：盘搜管理
- search-ranking：搜索排名
- settings：设置
- demands：需求管理

本次没有登录、没有访问，所以只能确认“有这些路由/接口名”，不能确认具体交互和权限。我们复刻时按自己的需求做，不照搬后台细节。

### 2.5 移动端

- 768px 以下显示移动菜单按钮，导航收进下拉菜单。
- 380px 以下进一步隐藏平台徽章、缩小 meta 间距。
- 搜索框在页面顶部/中部；结果页滚动后收进粘性顶栏。
- 网盘筛选横向滚动，有“左右滑动”提示。
- 结果卡片单列，操作按钮在卡片内。
- 状态条在窄屏压缩文字，保留数字/图标。

### 2.6 aliso 的强项和弱项

**值得学：**

- 搜索过程可见（结果数/类型数/耗时/取消）。
- 网盘筛选带数量，用户能判断“哪个网盘有货”。
- 同名变体编号切换，减少滚动。
- 首页三行快捷发现，把用户直接送进搜索。
- 交付层集中（二维码/提取码/保存/复制）。

**不要学：**

- 扫码-only 交付：伤害桌面体验和可复制性。
- 一次性渲染全部结果：结果多时性能差。
- 公开他人原始搜索历史：隐私/合规风险。
- 重运营位：广告、邀请注册、小程序会稀释搜索。
- 整体复刻后台：成本高，很多模块你未必用得上。

---

## 3. 你现在有什么（本站现状）

### 3.1 现有页面

| 路由 | 页面 | 作用 |
|---|---|---|
| `/` | 重定向到 `/resource` | 没有真正首页 |
| `/resource?kw=` | 资源搜索 | 搜索 + 热搜 + 本机历史 + 筛选 + 分页 + 有效性 + 封面 |
| `/douban` | 豆瓣榜单 | 11 类榜单、海报 |
| `/accel` | 网盘加速 | 百度登录/解析/下载/任务 |
| `/guest` | 直链解析 | 访客解析 |
| `/booster` | 下载提速 | 本机 Hook 加速 |
| `/stats` | 数据统计 | 索引/分布/热词/巡检 |

### 3.2 现有能力（复刻时要保留）

| 能力 | 现在的实现 | 复刻时怎么用 |
|---|---|---|
| 双通道搜索 | 本地索引 + PanSou + 学霸盘 | 保留；外面套 aliso 的搜索页 |
| 分页/缓存 | `/api/search` 返回分页/缓存标记 | 保留；不改成无限列表 |
| 有效性三态 | 有效/疑似失效/已失效 + 巡检 + 举报 | 保留；卡片徽章继续用 |
| 本地索引/收割 | SQLite + harvester + 零结果守望 | 保留；首页“本地”行和归档页都用它 |
| 百度加速 | BDUSS/扫码/aria2/账号池/Hook | 保留；放到“更多工具”或加速页 |
| 豆瓣 | 11 类榜单 + 海报 | 保留；放到“更多工具” |
| 统计 | `/api/stats` + StatsPage | 保留；后台控制台也可以复用 |
| 搜索历史 | 本机 localStorage + 站长可读全站 | 保留隐私设计；首页“历史”行用本机 |
| 网盘筛选 | 10 类，单选，无数量 | 改造：补类型、计数、多选 |
| 排序 | 无前端入口 | 新增 |
| 同名变体 | 展开行 | 改造：编号 chip |
| 二维码 | 只有百度登录二维码 | 新增资源分享二维码 |
| 公告 | 无 | 新增 |
| 法律页 | 无 | 新增 |
| 需求板 | 无 | 新增 |
| 管理后台 | 只有站长/加速/统计 | 新增 MVP，再扩展 |

### 3.3 主要缺口（按用户感知排序）

1. 没有首页，用户进来直接是搜索页，缺“站点感”。
2. 搜索没有过程感（耗时/进行中/取消）。
3. 网盘筛选没有数量，用户不知道哪个网盘有货。
4. 没有排序。
5. 同名变体是展开行，不够快。
6. 没有资源交付弹窗/二维码。
7. 没有公告、法律页、需求板。
8. 移动端 tabbar 图标空白、搜索框不同步、筛选换行。
9. 没有管理后台，资源/网盘/源库/需求都不能在网页里管。
10. `ACCEL_TOKEN` 硬编码，公开部署有风险。

---

## 4. 复刻边界：什么能一样，什么不能一样

### 4.1 “1:1”具体指什么

| 层面 | 可以一样 | 不能一样 |
|---|---|---|
| 页面结构 | 首页/搜索/需求/后台的区块顺序、层级 | 不能直接复制对方 HTML/CSS |
| 功能 | 搜索、筛选、排序、变体、弹窗、需求板、后台模块 | 不能调用对方接口/数据 |
| 交互 | 状态条、取消、扫码、复制、投票 | 不能复制对方文案/图标/图片 |
| 字段 | 资源标题、网盘、时间、有效性、提取码 | 不能复制对方数据库/私有字段 |
| 视觉 | 布局同构、信息密度接近 | 不能复制配色、Logo、插画、字体文件 |
| 技术 | Vue/Flask 的职责划分 | 不能复制对方源码/bundle |

**建议做法**：用你自己的设计 token（颜色、圆角、间距、阴影）做“同构但不同皮”的页面。用户感觉“结构和 aliso 一样”，但看起来是你自己的产品。

### 4.2 先不做的模块

| 模块 | 处理 |
|---|---|
| 广告位 | 代码里留 `ads_enabled=false`，不建投放/预览/频率控制 |
| 分享获益/邀请注册 | 不做邀请码、返利、注册引导 |
| 动画/少儿资源库 | 不做 `zy` 子站；导航隐藏 |
| 视频解析子站 | 不做 `hbh` 子站；导航隐藏 |
| 微信小程序 | 不做；移动端用响应式网页 + 二维码 |
| 外部友链 | 不做；页脚只留 GitHub + 法律页 |
| 转存/保存网盘 | 先不做；需要账号/合规评估（P2） |
| 全站原始搜索历史 | 不做；只做本机历史 + 聚合热搜 |

### 4.3 合规提醒

- 不调用 aliso 的接口，不把 aliso 作为数据源。
- 不抓取 aliso 的数据。
- 需求板涉及用户联系方式，必须有隐私说明和防刷。
- 法律页文案请自己撰写/审核，本文档不构成法律意见。

---

## 5. 目标系统设计（一页图）

### 5.1 前端页面

```text
/                        首页
/search?ai=关键词        搜索结果（兼容 /resource?kw=）
/demand                  需求板
/legal/clean-network     净网声明
/legal/copyright         版权说明
/legal/disclaimer        免责声明
/admin/login             后台登录
/admin/dashboard         控制台
/admin/resources         资源管理
/admin/clouddrive        网盘管理
/admin/source-center     源库管理
/admin/api-config        接口配置
/admin/pansou-manage     盘搜管理
/admin/search-ranking    搜索排名
/admin/settings          设置
/admin/demands           需求管理
/douban                  豆瓣榜单（保留，放“更多工具”）
/accel                   网盘加速（保留）
/guest                   直链解析（保留）
/booster                 下载提速（保留）
/stats                   数据统计（保留）
```

### 5.2 前端组件树

```text
App.vue
└── AppShell.vue
    ├── SiteHeader.vue          顶部导航 + 主题 + 移动菜单
    ├── SearchDock.vue          全局搜索框
    ├── <router-view>
    │   ├── HomePage.vue        首页
    │   │   ├── HeroSearch.vue
    │   │   ├── NoticeBar.vue
    │   │   ├── QuickDiscovery.vue（热门/历史/本地三行）
    │   │   └── SiteFooter.vue
    │   ├── SearchPage.vue      搜索结果
    │   │   ├── SearchStatusBar.vue
    │   │   ├── SortSelect.vue
    │   │   ├── DiskFilterChips.vue
    │   │   ├── ResultList.vue
    │   │   │   └── ResourceCard.vue
    │   │   ├── ResourceDialog.vue / ResourceDetailDrawer.vue
    │   │   └── EmptyState.vue
    │   ├── DemandPage.vue      需求板
    │   │   ├── DemandForm.vue
    │   │   └── DemandList.vue
    │   ├── LegalPage.vue       法律页
    │   └── AdminLayout.vue     后台
    │       ├── AdminSidebar.vue
    │       ├── DashboardPage.vue
    │       ├── ResourcesPage.vue
    │       ├── NetdiskPage.vue
    │       ├── SourcePage.vue
    │       ├── ApiConfigPage.vue
    │       ├── RankingPage.vue
    │       ├── SettingsPage.vue
    │       └── DemandsPage.vue
    └── SiteFooter.vue
```

### 5.3 后端模块

```text
backend/
├── app.py                     组装 Flask、静态文件
├── access.py                  站长/访客判定（保留）
├── config.py                  现有配置（保留）
├── api/
│   ├── __init__.py            现有搜索/索引/检测/统计（保留，增量改）
│   ├── public.py              [新增] 公开配置/公告/热搜/首页数据
│   ├── auth.py                [新增] 后台登录/当前用户/改密码
│   ├── admin.py               [新增] 后台资源/网盘/源库/接口/排名/设置/需求
│   └── demand.py              [新增] 需求提交/列表/投票
├── site_config.py             [新增] 站点配置读写
├── demand/                    [新增] 需求数据层
├── indexer/                   现有索引（保留，新增表/查询）
├── search_engine/             现有 PanSou 客户端（保留）
├── checker/                   现有巡检（保留）
├── analyzer/                  现有统计（保留）
├── douban/                    现有豆瓣（保留）
└── accel/                     现有加速（保留）
```

### 5.4 页面与接口映射

| 页面 | 主要接口 | 现有/新增 |
|---|---|---|
| 首页 | `GET /api/public/settings`、`/api/hot-search-words`、`/api/search-history`、`/api/resource-titles`、`/api/notice` | 新增公开层，复用现有索引/热词 |
| 搜索页 | `POST /api/search/stream`、`POST /api/search`、`POST /api/check-link-validity/batch` | 流式新增；一次性保留 |
| 资源弹窗 | `POST /api/public/netdisk-click`、`POST /api/netdisk/transfer`（可选） | 新增；转存先不做 |
| 需求板 | `GET/POST /api/demands`、`POST /api/demand`、`POST /api/demand/{id}/want` | 新增 |
| 后台 | `/api/auth/*`、`/api/admin/*` | 新增 |
| 现有工具 | `/api/accel/*`、`/api/douban/hot`、`/api/stats` | 保留 |

---

## 6. 数据库改造

### 6.1 现有表（不要动坏）

| 表 | 用途 |
|---|---|
| `link_index` | 资源链接索引（标题/URL/网盘/来源/存活/有效性/点击热度） |
| `search_history` | 全站搜索词聚合（仅站长可读） |
| `click_stats` | 按关键词的点击统计 |
| `zero_result_watch` | 零结果守望 |
| `poster_cache` | 封面缓存 |
| `accel_*` | 加速任务/账号/配额/口令 |

### 6.2 新增表

```sql
-- 站点配置：公告、首页词条、页脚、开关
site_settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TIMESTAMP
)

-- 网盘配置：驱动前端标签/图标/是否启用/是否检测
netdisk_configs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT UNIQUE,              -- quark/baidu/aliyun/...
  display_name TEXT,
  is_enabled INTEGER DEFAULT 1,
  sort_order INTEGER DEFAULT 100,
  icon TEXT DEFAULT '',
  color TEXT DEFAULT '',
  enable_transfer INTEGER DEFAULT 0,
  enable_check INTEGER DEFAULT 1,
  filter_level TEXT DEFAULT '',
  expired_type TEXT DEFAULT '',
  created_at TIMESTAMP,
  updated_at TIMESTAMP
)

-- 公告：可保留历史版本
notices (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  content TEXT NOT NULL,
  version TEXT NOT NULL,
  enabled INTEGER DEFAULT 1,
  created_at TIMESTAMP
)

-- 需求板
demands (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  platforms TEXT DEFAULT '',     -- JSON 数组
  play_url TEXT DEFAULT '',
  remark TEXT DEFAULT '',
  contact TEXT DEFAULT '',
  is_paid INTEGER DEFAULT 0,
  status TEXT DEFAULT 'pending', -- pending/resolved/rejected
  resolve_note TEXT DEFAULT '',
  reject_reason TEXT DEFAULT '',
  want_count INTEGER DEFAULT 0,
  created_at TIMESTAMP
)

demand_votes (
  demand_id INTEGER NOT NULL,
  voter_key TEXT NOT NULL,       -- 匿名指纹/IP hash，不存明文 IP
  created_at TIMESTAMP,
  PRIMARY KEY (demand_id, voter_key)
)

-- 后台账号（与 ACCEL_TOKEN 分离）
admin_users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  role TEXT DEFAULT 'admin',
  created_at TIMESTAMP,
  last_login_at TIMESTAMP
)

-- 源库/接口/排名（后台扩展）
source_configs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,            -- tg/plugin/weibo/tieba/...
  name TEXT NOT NULL,
  config TEXT DEFAULT '{}',
  enabled INTEGER DEFAULT 1,
  sort_order INTEGER DEFAULT 100
)

api_configs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  base_url TEXT DEFAULT '',
  api_key TEXT DEFAULT '',
  config TEXT DEFAULT '{}',
  enabled INTEGER DEFAULT 1
)

search_ranking (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  keyword TEXT NOT NULL,
  url TEXT NOT NULL,
  weight REAL DEFAULT 0,
  enabled INTEGER DEFAULT 1,
  UNIQUE(keyword, url)
)
```

### 6.3 数据迁移规则

1. **只加表/加列，不删旧表/旧列**；先备份 `backend/data/index.db`。
2. `link_index` 不动结构，只加索引（如需）。
3. `search_history` 保留为“全站聚合”，首页“历史”优先用本机 localStorage。
4. `netdisk_configs` 先按现有 `constants.js` 的 10 类 + `ed2k/others` 种子；光鸭/城通/移动等后端实际返回类型确认后再补。
5. 后台账号首次启动时生成默认管理员（用户名/密码从环境变量读取，首次登录强制修改）。
6. 所有时间字段统一 ISO 8601 字符串（与现有代码一致）。

---

## 7. 后端接口清单

### 7.1 公开接口（不需要登录）

| 方法 | 路径 | 用途 | 现有/新增 |
|---|---|---|---|
| GET | `/api/public/settings` | 站点基础配置（名称、公告、页脚、开关） | 新增 |
| GET | `/api/public/netdisk-configs` | 网盘类型/显示名/图标/是否启用/是否检测 | 新增 |
| GET | `/api/enabled-check-services` | 可用检测服务列表 | 新增（可复用 checker 配置） |
| GET | `/api/hot-search-words` | 首页热门词 | 新增（复用 `/api/hot`） |
| GET | `/api/search-history` | 首页历史（本机/服务端策略待定） | 新增（可先只返回本机） |
| GET | `/api/resource-titles` | 首页“本地”词条 | 新增（复用索引标题） |
| GET | `/api/notice` | 公告内容 | 新增 |
| GET | `/api/search-token` | 搜索令牌（可选） | 新增；已有访问控制，可后置 |
| POST | `/api/search/stream` | 流式搜索（NDJSON） | 新增 |
| POST | `/api/search` | 一次性搜索（回退/分页） | 保留 |
| GET | `/api/demands` | 历史需求 | 新增 |
| POST | `/api/demand` | 提交需求 | 新增 |
| POST | `/api/demand/{id}/want` | 想要投票 | 新增 |
| POST | `/api/public/netdisk-click` | 网盘点击统计 | 新增（可复用 `/api/click`） |
| POST | `/api/check-link-validity/batch` | 批量有效性检测 | 现有 `/api/check/batch` 可做别名 |

### 7.2 后台接口（需要登录）

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/auth/login` | 登录 |
| GET | `/api/auth/me` | 当前用户 |
| POST | `/api/auth/change-password` | 改密码 |
| GET | `/api/dashboard/stats` | 控制台统计（复用 `/api/stats`） |
| GET/POST | `/api/admin/resources` | 资源列表/新增 |
| POST | `/api/admin/resources/import` | 导入资源 |
| POST | `/api/admin/resources/batch-delete` | 批量删除 |
| GET/PUT | `/api/admin/netdisk-configs` | 网盘配置 |
| GET/POST/PATCH/DELETE | `/api/admin/cloud-accounts` | 网盘账号（可复用 accel_accounts） |
| GET/POST | `/api/admin/source-center` | 源库管理 |
| GET/POST | `/api/admin/api-configs` | 接口配置 |
| PUT | `/api/admin/pansou-config` | PanSou 配置 |
| GET/POST | `/api/admin/search-ranking` | 搜索排名 |
| GET/PUT | `/api/admin/settings` | 站点设置 |
| GET/PUT | `/api/admin/demands` | 需求审核/删除 |

### 7.3 流式搜索协议（建议）

```text
POST /api/search/stream
Content-Type: application/json
Body: {"kw":"庆余年","page_size":20,"disk_types":[],"sort":"relevance"}

响应: application/x-ndjson
{"type":"start","elapsed_ms":0}
{"type":"batch","source":"archive","results":[...],"received":12}
{"type":"batch","source":"pansou","results":[...],"received":80}
{"type":"done","total":581,"disk_counts":{"quark":283,...},"elapsed_ms":8123}
{"type":"error","message":"..."}
```

前端用 `fetch` + `ReadableStream` 按行解析；用户点“停止”时 `AbortController.abort()`；失败自动回退 `POST /api/search`。

### 7.4 搜索响应该加什么

在现有 `/api/search` 响应上增量加：

- `elapsed_ms`
- `disk_counts`
- `disk_type_count`
- `sort`
- `streaming`（可选）

不要删除现有字段：`total/raw_count/page/page_size/total_pages/cached/archive_count/realtime_count/hidden_dead/pansou_error` 等。

---

## 8. 前端改造

### 8.1 路由

```js
/                  -> HomePage
/search            -> SearchPage（读 query.ai）
/resource          -> redirect 到 /search?ai=<kw>（兼容旧链接）
/demand            -> DemandPage
/legal/:type       -> LegalPage
/admin/login       -> AdminLogin
/admin             -> AdminLayout（子路由）
/douban /accel /guest /booster /stats -> 保留
```

### 8.2 关键组件说明

| 组件 | 职责 | 关键点 |
|---|---|---|
| `SiteHeader` | 顶部导航、主题、移动菜单 | 导航项：首页/搜索/需求/更多工具；不放开邀请/动画 |
| `SearchDock` | 搜索输入 | 支持 `ai`/`kw`；路由变化同步；粘贴链接检测保留 |
| `NoticeBar` | 公告 | 可关闭；按版本记录已读 |
| `QuickDiscovery` | 热门/历史/本地三行 | 先做 chips/横滑，不做跑马灯动画也可以 |
| `SearchStatusBar` | 结果数/类型数/耗时/状态 | 搜索中/完成/失败/取消 |
| `DiskFilterChips` | 网盘筛选 | 计数、多选、横向滚动 |
| `SortSelect` | 排序 | 默认/时间/名称；写 URL |
| `ResourceCard` | 资源卡 | 直链、复制、有效性、举报、变体编号 |
| `ResourceDialog` | 交付弹窗 | 二维码、提取码、复制、打开；不做扫码-only |
| `EmptyState` | 空结果 | 提示 + 去需求板 |
| `DemandPage` | 需求板 | 表单/历史/投票 |
| `AdminLayout` | 后台外壳 | 侧栏/顶栏/登录态 |
| `LegalPage` | 法律页 | 净网/版权/免责 |

### 8.3 视觉原则

- **同构不同皮**：布局、信息层级、间距节奏参考 aliso，但颜色/圆角/阴影/字体用你自己的。
- 不要直接抄 aliso 的 CSS 变量名、class 名、图片、图标。
- 移动端优先：390px 先验收，再 768/1280。
- 亮/暗主题沿用现有 `theme.js`，不要重写。
- 所有交互都要有 loading/empty/error 三态。

### 8.4 移动端

- 768px 以下：顶部导航收进菜单或保留底部 tabbar（二选一，建议保留现有 tabbar，补图标）。
- 搜索框：滚动后粘性顶部。
- 筛选 chips：横向滚动 + 边缘渐隐。
- 状态条：数字 + 图标，文字可隐藏。
- 资源卡：标题、网盘、有效性、操作；来源/URL 放到详情/抽屉。
- 需求板：单列表单，按钮全宽。

---

## 9. 分阶段实施计划

### 阶段 0：准备（2~3 天）

**目标**：不写业务代码，先把“怎么改、怎么回滚”定下来。

任务：

1. 备份 `backend/data/index.db`。
2. 建分支 `feature/aliso-parity`。
3. 确认 `/resource?kw=` 旧链接兼容策略（重定向/别名）。
4. 确认后台登录方案（用户名/密码 + session 或签名 token）；不要复用 `ACCEL_TOKEN` 当后台密码。
5. 建 feature flag：
   - `ALISO_UI=1`
   - `DEMAND_ENABLED=0/1`
   - `STREAM_SEARCH=0/1`
   - `ADMIN_V2=0/1`
6. 建数据库迁移脚本（只加表/加列）。

**验收**：旧站能正常启动；新分支能构建；数据库有备份；开关能关掉新功能。

### 阶段 1：外壳 + 首页（1 周）

**目标**：打开 `/` 看到 aliso 风格的首页。

任务：

1. `router.js` 增加 `/`、`/search`、`/demand`、`/legal/*`；`/resource` 兼容重定向。
2. 新建 `HomePage.vue`：
   - `SiteHeader`
   - Logo + 站点名 + `NoticeBar`
   - `HeroSearch`（大搜索框）
   - `QuickDiscovery`（热门/历史/本地）
   - `SiteFooter`
3. 新建 `SiteHeader.vue`、`SiteFooter.vue`、`NoticeBar.vue`、`HeroSearch.vue`、`QuickDiscovery.vue`。
4. 后端新增 `GET /api/public/settings`、`/api/notice`、`/api/hot-search-words`、`/api/search-history`、`/api/resource-titles`。
5. 首页搜索跳 `/search?ai=`。
6. 移动端：首页搜索框、三行词条、页脚。

**验收**：

- `/` 有完整首屏，不空白。
- 搜索后进入 `/search?ai=`。
- 热门/历史/本地点击能搜。
- 公告可关闭、刷新不重复。
- 390px 不横向溢出。

### 阶段 2：搜索结果页（1.5~2 周）

**目标**：把现有 `ResourcePage` 改造成 aliso 风格的 `SearchPage`。

任务：

1. `SearchPage.vue` 读取 `query.ai`，兼容 `query.kw`。
2. 顶部搜索框 + 状态条 + 排序 + 网盘 chips。
3. 后端 `/api/search` 增加 `elapsed_ms`、`disk_counts`、`sort`。
4. 网盘多选 + URL `?disk=quark,baidu`。
5. 排序写 `?sort=time`。
6. `ResourceCard` 增加变体编号 chip。
7. 新增 `ResourceDialog`（二维码/提取码/复制/打开）。
8. 空态 + 错误态 + 加载态。
9. 移动端筛选横滑、状态条精简、卡片减负。

**验收**：

- 状态条显示结果数/类型数/耗时。
- 计数与结果一致。
- 多选/排序 URL 可分享、刷新保持。
- 变体切换不刷新。
- 二维码可识别，直链/复制不回归。
- 390px 无横向溢出。

### 阶段 3：需求板 + 法律页（1 周）

**目标**：补齐 aliso 的两个公开页面。

任务：

1. 新建 `demands`、`demand_votes` 表。
2. `POST /api/demand`、`GET /api/demands`、`POST /api/demand/{id}/want`。
3. `DemandPage.vue`：双 Tab、表单、历史列表、状态徽章、想要按钮。
4. 防刷：IP/频率/敏感词/长度限制；站长审核。
5. 法律页：`LegalPage.vue` + `/legal/clean-network`、`/legal/copyright`、`/legal/disclaimer`。
6. 页脚加入口。
7. 去掉“更多工具”选项

**验收**：

- 提交有校验/防刷；历史列表可看；投票可切换。
- 法律页可访问；移动端正常。
- 需求板可一键关闭（`DEMAND_ENABLED=0`）。

### 阶段 4：后台扩展 + 流式搜索（1.5~2 周）

**目标**：补齐 aliso 后台的其余模块，并让搜索“边搜边出”。

任务：

1. 源库管理：`source_configs`；TG 频道/插件/微博/贴吧的启停与排序。
2. 接口配置：`api_configs`；base_url/key/开关。
3. 盘搜管理：PanSou 地址/认证/超时（注意：外部 PanSou 可能需要重启才生效）。
4. 搜索排名：关键词置顶/权重；接入 `_dedup_and_rank`。
5. 流式搜索：`POST /api/search/stream`；前端 `ReadableStream` + 取消 + 回退。
6. 批量检测上屏：保留自动检测，补手动重检/进度（可选）。

**验收**：

- 后台 9 个模块都有入口；不能用的模块明确标“未实现/只读”。
- 修改网盘配置后前端 chips 变化。
- 流式搜索首条结果更早；取消保留已收结果；失败回退。
- 现有 `/api/search` 分页/缓存不回归。

### 阶段5：回归与上线（1 周）

**目标**：把“像 aliso”变成“稳定可用”。

任务：

1. 全量回归：搜索/筛选/排序/变体/二维码/需求/后台/移动端/主题。
2. 性能：`disk_counts`、流式、归档、COUNT。
3. 安全：后台鉴权、需求防刷、二维码、`ACCEL_TOKEN`、反向代理。
4. 部署：`npm run build`、docker compose、静态文件挂载。
5. 回滚演练：关开关、回退路由、恢复数据库备份。

**验收**：

- 验收清单全过。
- 回滚步骤可在 10 分钟内完成。
- 公开部署前 `ACCEL_TOKEN` 已改为环境变量并轮换。

---

## 10. 逐文件改造清单

| 文件 | 动作 | 说明 |
|---|---|---|
| `frontend/src/router.js` | 改 | 增加 `/`、`/search`、`/demand`、`/legal/*`、`/admin/*`；`/resource` 重定向 |
| `frontend/src/App.vue` | 不改/小改 | 仍挂 `AppShell` |
| `frontend/src/components/AppShell.vue` | 改 | 顶部导航/移动菜单/页脚；导航项调整 |
| `frontend/src/components/SearchDock.vue` | 改 | 支持 `ai/kw`；路由同步；保留粘贴链接检测 |
| `frontend/src/pages/ResourcePage.vue` | 改/拆分 | 变成 `SearchPage.vue` 或抽出搜索组件 |
| `frontend/src/components/ResourceCard.vue` | 改 | 变体编号、二维码入口、紧凑卡片 |
| `frontend/src/pages/HomePage.vue` | 新增 | 首页 |
| `frontend/src/pages/DemandPage.vue` | 新增 | 需求板 |
| `frontend/src/pages/LegalPage.vue` | 新增 | 法律页 |
| `frontend/src/pages/admin/*` | 新增 | 后台页面 |
| `frontend/src/components/SiteHeader.vue` | 新增 | 顶部导航 |
| `frontend/src/components/SiteFooter.vue` | 新增 | 页脚 |
| `frontend/src/components/NoticeBar.vue` | 新增 | 公告 |
| `frontend/src/components/SearchStatusBar.vue` | 新增 | 状态条 |
| `frontend/src/components/DiskFilterChips.vue` | 新增 | 网盘筛选 |
| `frontend/src/components/SortSelect.vue` | 新增 | 排序 |
| `frontend/src/components/ResourceDialog.vue` | 新增 | 资源交付弹窗 |
| `frontend/src/components/ResourceQr.vue` | 新增 | 二维码 |
| `frontend/src/constants.js` | 改 | 补光鸭/城通/移动；网盘配置逐步改为接口驱动 |
| `frontend/src/style.css` | 改 | 新布局/移动端/后台样式 |
| `frontend/package.json` | 改 | 加轻量 QR 依赖 |
| `backend/api/__init__.py` | 改 | 搜索加 `elapsed_ms/disk_counts/sort`；保留旧字段 |
| `backend/api/public.py` | 新增 | 公开配置/公告/热搜/首页数据 |
| `backend/api/auth.py` | 新增 | 后台登录 |
| `backend/api/admin.py` | 新增 | 后台管理接口 |
| `backend/api/demand.py` | 新增 | 需求接口 |
| `backend/site_config.py` | 新增 | 站点配置读写 |
| `backend/demand/` | 新增 | 需求数据层 |
| `backend/indexer/__init__.py` | 改 | 新增表/查询；不要破坏旧表 |
| `backend/config.py` | 改 | 新开关/默认管理员/流式配置 |
| `backend/app.py` | 改 | 注册新蓝图；静态文件路由 |
| `docker-compose.yml` | 改 | 环境变量；`ACCEL_TOKEN` 改占位/环境变量 |
| `README.md` | 改 | 启动/部署/后台账号说明 |

---

## 11. 1:1 对照验收清单

### 11.1 首页

- [ ] 顶部工具条：首页/搜索/需求/更多工具/主题。
- [ ] Logo + 站点名 + 公告。
- [ ] 大搜索框，回车/按钮能搜。
- [ ] 热门/历史/本地三行，可点击搜索。
- [ ] 页脚：法律页 + GitHub。
- [ ] 移动端：搜索框、三行横滑、公告换行。
- [ ] 首屏无交互也能稳定显示（不依赖动画）。

### 11.2 搜索结果页

- [ ] `/search?ai=` 可分享/刷新。
- [ ] 状态条：结果数/类型数/耗时/进行中或完成。
- [ ] 排序：默认/时间/名称；URL 可分享。
- [ ] 网盘 chips：带数量；多选；“全部”清除。
- [ ] 结果卡：标题/网盘/日期/有效性/操作。
- [ ] 同名变体：编号切换，不刷新。
- [ ] 资源弹窗/抽屉：二维码/提取码/复制/打开。
- [ ] 空态：提示 + 去需求板。
- [ ] 失败：错误提示；可重试。
- [ ] 移动端：筛选横滑、状态条精简、卡片不溢出。

### 11.3 需求板

- [ ] 提交需求：字段/校验/提交成功提示。
- [ ] 历史需求：列表/排序/状态/想要数。
- [ ] 想要投票：可切换。
- [ ] 防刷：频率/敏感词/长度。
- [ ] 后台审核：通过/驳回/删除。



### 11.5 保留能力

- [ ] 直链打开/复制链接/复制提取码。
- [ ] 有效性三态/巡检/举报。
- [ ] 本地索引/收割/归档。
- [ ] 百度加速/aria2/账号池/Hook。
- [ ] 豆瓣榜单/海报。
- [ ] 统计页。
- [ ] 旧 `/resource?kw=` 链接不失效。

---

## 12. 搜索与数据逻辑（复刻时最容易做错的地方）

### 12.1 搜索仍然走双通道

```text
关键词
  -> 本地索引（历史结果，毫秒级）
  -> PanSou + 学霸盘（实时结果，秒级）
  -> 去重 + 排序 + 同资源聚合
  -> 分页返回
```

不要为了“像 aliso”把本地索引去掉；这是你的优势。

### 12.2 网盘计数

- 计数基于**未筛选的完整结果集**，不是当前页。
- 如果暂时做不到，筛选态就隐藏计数，只显示当前结果数。
- 隐藏失效后计数同步减少。
- 计数类型来自 `netdisk_configs`，不要在前端写死。

### 12.3 排序

- `relevance`：保持现有相关度/存活/点击热度。
- `time`：按时间降序；缺失时间排最后。
- `name`：按标题排序；中文先用 `localeCompare`，不引入大依赖。
- 排序必须在分页之前；默认排序不能改变现有质量。

### 12.4 流式搜索

- 只流“首批/第一页”，分页仍走 `/api/search` 缓存。
- 批次之间按 URL/规范化标题去重。
- 结束帧给最终总数/计数/耗时。
- 用户取消保留已收结果。
- 失败回退一次性搜索。
- 代理（nginx）要关闭缓冲，否则“边搜边出”会变成“最后一次性出”。

### 12.5 后台写资源

- 资源管理直接写 `link_index`；导入要校验 URL/网盘类型/标题长度。
- 删除要软删除或保留审计；不要误删索引。
- 搜索排名通过 `search_ranking` 影响 `_dedup_and_rank`，不要改默认相关度逻辑。

---

## 13. 迁移与兼容

### 13.1 旧链接

- `/resource?kw=xxx` → 301/前端重定向到 `/search?ai=xxx`。
- 旧 `/api/search` 保持兼容；新增字段不删除旧字段。
- 旧 localStorage `dps-search-history` 继续可用；新首页“历史”读同一份数据。

### 13.2 旧功能

- `/douban`、`/accel`、`/guest`、`/booster`、`/stats` 全部保留，放到“更多工具”。
- 豆瓣/加速/统计的接口不动。
- 现有有效性徽章/举报/隐藏失效不动。

### 13.3 开关

建议加环境变量/配置：

```text
ALISO_UI=1            新首页/搜索 UI
STREAM_SEARCH=0       流式搜索（先关，稳定后开）
DEMAND_ENABLED=0      需求板（没运营先关）
ADMIN_V2=0            新后台（先内测）
SITE_NOTICE_ENABLED=1
```

出问题可以逐项关闭，不用回滚整个版本。

### 13.4 数据备份

- 迁移前备份 `index.db`。
- 新表创建失败不影响旧表。
- 后台账号/配置写失败要回滚事务。
- 上线前做一次“恢复备份”演练。

---

## 14. 测试、上线、回滚

### 14.1 测试矩阵

| 类型 | 内容 |
|---|---|
| 搜索 | 关键词/空词/特殊字符/长词/零结果/PanSou离线/网络错误 |
| 筛选 | 单选/多选/全部/无结果/计数一致 |
| 排序 | 三种排序/分页/URL/默认回归 |
| 变体 | 单变体/多变体/失效变体/切换后操作 |
| 二维码 | 生成/识别/关闭/长URL/无网络 |
| 需求 | 提交/校验/投票/审核/防刷 |
| 后台 | 登录/权限/资源/网盘/源库/接口/排名/设置/需求 |
| 移动端 | 390/768/1280；tabbar/筛选/状态条/卡片 |
| 主题 | 亮/暗/刷新保持 |
| 兼容 | 旧 `/resource` 链接、旧 API 字段、旧 localStorage |

### 14.2 上线步骤

1. `cd frontend && npm install && npm run build`。
2. 备份数据库。
3. `docker compose up -d --build`。
4. 检查 `/api/health`、`/`、`/search?ai=测试`、`/admin/login`。
5. 用手机 390px 检查首页/搜索/需求。
6. 观察 24 小时错误日志/搜索延迟。

### 14.3 回滚

| 改动 | 回滚 |
|---|---|
| 新 UI | `ALISO_UI=0`，恢复旧路由 |
| 流式搜索 | `STREAM_SEARCH=0`，走 `/api/search` |
| 需求板 | `DEMAND_ENABLED=0` |
| 新后台 | `ADMIN_V2=0`，保留只读/旧入口 |
| 数据库 | 恢复备份（新表不影响旧表） |
| 前端 | 回退上一个 `dist` |
| 部署 | 回退镜像/提交 |

---

## 15. 风险与合规

| 风险 | 说明 | 处理 |
|---|---|---|
| 复制对方资产 | 1:1 不是复制代码/图片/文案 | 独立实现；只用布局/流程/字段 |
| 调用对方接口 | 数据源不合规 | 只用本站 PanSou/索引 |
| `ACCEL_TOKEN` 硬编码 | 权限泄露 | 环境变量/轮换/清理脚本 |
| 反向代理误判站长 | 所有访客变站长 | `TRUST_PROXY=1` + `ProxyFix` + 验证 |
| 后台弱口令 | 后台被入侵 | 强密码/登录限流/会话过期 |
| 需求板垃圾 | 运营负担 | 匿名 + 审核 + 防刷 |
| 流式被代理缓冲 | 边搜边出失效 | nginx 关闭缓冲/调大超时 |
| 计数性能 | 搜索变慢 | 完整结果集聚合一次 + 缓存 |
| 排序破坏相关性 | 搜索质量下降 | 默认 relevance 不变 |
| 二维码泄露 | 提取码/账号泄露 | 只编码分享链接 |
| FTS5 迁移 | 索引损坏 | 备份 + 双写 + 回滚 |
| dist 未重建 | 页面旧 | 发版前 `npm run build` |

---

## 16. 第一周直接开工清单

### Day 1：外壳和路由

- [ ] 建分支/备份数据库。
- [ ] `router.js` 增加 `/`、`/search`、`/demand`、`/legal/*`；`/resource` 兼容。
- [ ] `AppShell.vue` 改成顶部导航 + 页脚；保留现有侧栏作为“更多工具”（可选）。
- [ ] `SearchDock.vue` 支持 `ai` 参数和路由同步。

### Day 2：首页骨架

- [ ] `HomePage.vue`：Logo/公告/大搜索/三行词条/页脚。
- [ ] `NoticeBar.vue`：可关闭、按版本记录。
- [ ] `QuickDiscovery.vue`：热门/历史/本地。
- [ ] 后端 `/api/public/settings`、`/api/notice`、`/api/hot-search-words`、`/api/search-history`、`/api/resource-titles`。

### Day 3：搜索页状态条

- [ ] `SearchPage.vue` 读取 `ai`。
- [ ] 后端 `/api/search` 加 `elapsed_ms`、`disk_type_count`。
- [ ] `SearchStatusBar.vue`：搜索中/完成/失败。
- [ ] 移动端状态条精简。

### Day 4：网盘计数和多选

- [ ] 后端 `disk_counts`。
- [ ] `DiskFilterChips.vue`：数量、多选、全部。
- [ ] URL `?disk=` 同步。
- [ ] 计数口径验收。

### Day 5：排序和变体

- [ ] 后端 `sort`。
- [ ] `SortSelect.vue` + URL `?sort=`。
- [ ] `ResourceCard.vue` 变体编号 chip。
- [ ] 回归默认排序。

### Day 6：二维码和移动端

- [ ] `ResourceQr.vue` + `ResourceDialog.vue`。
- [ ] tabbar 图标。
- [ ] 筛选横滑。
- [ ] 390px 验收。

### Day 7：联调和演示

- [ ] 首页 → 搜索 → 筛选 → 排序 → 变体 → 二维码全链路。
- [ ] 旧 `/resource` 链接兼容。
- [ ] 记录问题清单，排下一周。

---

## 17. 默认决策（不用再问，按这个做）

| 问题 | 默认决策 | 理由 |
|---|---|---|
| 视觉是否 1:1 复制 | 布局同构，视觉独立 | 避免版权/品牌风险 |
| 是否扫码-only | 否，直链为主，二维码补充 | 桌面体验/可复制性 |
| 是否无限滚动 | 否，保留分页 | 大结果集性能 |
| 搜索参数 | `ai` 为主，兼容 `kw` | 对齐 aliso + 旧链接 |
| 网盘计数 | 基于未筛选完整结果集 | 避免筛选后计数矛盾 |
| 排序 | 服务端排序后再分页 | 分页正确性 |
| 流式搜索 | 先关，稳定后开 | 风险可控 |
| 需求板 | 先做但默认关闭 | 没运营不产生垃圾 |
| 后台 | MVP 先行，再扩展 | 控制成本 |
| 后台登录 | 用户名/密码 + 会话 | 与 `ACCEL_TOKEN` 分离 |
| 广告/邀请/动画库 | 不做 | 按你的要求 |
| 豆瓣/加速/统计 | 保留，放“更多工具” | 本站优势 |
| 法律页 | 新建，自己写文案 | 合规 |

---

## 18. 一句话总结

**先做“首页 + 搜索页 + 需求页 + 法律页”，把 aliso 的壳和搜索体验复刻出来；后台分两批做；广告、邀请、动画库先不碰；直链、有效性、索引、加速、豆瓣、统计全部保留。** 这样既能 1:1 对齐 aliso 的主站体验，又不会把 DuPanSou 原来的优势弄丢。
