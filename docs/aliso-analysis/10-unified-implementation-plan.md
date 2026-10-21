# 10 · 统一实施计划

- 制定日期：2026-10-19
- 依据：`09-unified-aliso-playbook.md`（现行单一事实来源）、`08-comparison-matrix.md`（冲突与现状核对）
- 原则：
  1. 只借鉴功能与交互模式，不复用 aliso 的代码、样式、文案、图片、接口；
  2. 优先增量改造现有 Flask + SQLite + Vue 3 结构，不重写；
  3. 先补搜索体验，再做运营型功能；
  4. 每项改动都要有可验证的验收标准和回滚方式；
  5. 任务编号与 09 的 `U-P0-* / U-P1-* / U-P2-*` 对应。

---

## 0. 使用方式与前置检查

### 0.1 开发前必须确认

| 检查 | 命令/位置 | 通过标准 |
|---|---|---|
| 当前分支与工作区 | `git status` | 明确哪些改动是本次任务；不要覆盖未提交的 BoosterPage/Dockerfile 等改动 |
| 前端依赖 | `cd frontend && npm install` | 能执行 `npm run build` |
| 后端依赖 | `cd backend && pip install -r requirements.txt` | 能启动 `python app.py` |
| PanSou 可用 | `docker compose ps` 或访问 `PANSOU_BASE_URL` | `/api/health` 不为 offline；否则搜索降级为历史索引 |
| 索引库 | `backend/data/index.db` | 有数据；备份后再做索引相关改动 |
| 构建产物 | `frontend/dist/index.html` | 发版前重新 `npm run build`；不要假设 dist 与当前源码同步 |
| 站长令牌 | 环境变量 `ACCEL_TOKEN` | 不要使用仓库中硬编码值；公开部署前轮换（见 §8） |

### 0.2 任务总览

| 批次 | 任务 | 目标 | 预估 |
|---|---|---|---|
| P0 | U-P0-1 | 搜索状态条 + 耗时 | 0.5~1 天 |
| P0 | U-P0-2 | 网盘计数 | 1~1.5 天 |
| P0 | U-P0-3 | 网盘多选 + URL 状态 | 1 天 |
| P0 | U-P0-4 | 排序 | 1 天 |
| P0 | U-P0-5 | 同名变体编号 | 1 天 |
| P0 | U-P0-6 | 移动端修复 | 1~1.5 天 |
| P0 | U-P0-7 | 二维码补充交付 | 0.5~1 天 |
| P0 | U-P0-8 | 公告条 + 法律页 | 1~2 天（含文案审核） |
| P1 | U-P1-1 | 渐进式搜索 | 3~5 天 |
| P1 | U-P1-2 | 本地归档页 + 接口参数 | 2~3 天 |
| P1 | U-P1-3 | 资源详情抽屉 | 2~3 天 |
| P1 | U-P1-4 | 统计页修复 | 0.5 天 |
| P1 | U-P1-5 | 站点配置 | 2~3 天 |
| P2 | U-P2-1~9 | 运营型/性能/评估项 | 按需 |

---

## 1. P0 任务详情

### U-P0-1 搜索状态条 + 耗时

**目标**：搜索完成后显示耗时、结果数、网盘类型数、实时/历史/检测/缓存；搜索中显示“搜索中…/已用时”。

**涉及文件**

- `backend/api/__init__.py`（`/api/search`）
- `frontend/src/pages/ResourcePage.vue`
- `frontend/src/style.css`（状态条样式，如需）

**后端改动**

1. 在 `search()` 入口记录 `t0 = time.perf_counter()`。
2. 在 `jsonify({...})` 中增加：
   - `elapsed_ms`: `int((time.perf_counter() - t0) * 1000)`
   - `disk_type_count`: 当前结果集中不同 `disk_type` 的数量（可从 `cards` 计算；与 U-P0-2 共用）
3. 保持所有旧字段不变，避免破坏前端。

**前端改动**

1. `data()` 增加 `searchStartedAt: 0`、`elapsedMs: 0`、`_elapsedTimer: null`。
2. `fetchSearch()` 开始时启动计时器（每 250ms 更新），`finally` 中停止。
3. 优先使用后端 `data.elapsed_ms`；没有时用本地计时。
4. 状态条增加：
   - 搜索中：`搜索中… 已用时 Xs`
   - 完成：`耗时 Xs · 共 N 个资源 · M 类网盘`
   - 失败：显示错误，不显示耗时。
5. `beforeUnmount` 清理计时器。

**验收标准**

- 搜索完成后 1 秒内显示耗时、结果数、类型数。
- 搜索失败不显示“耗时 0s”之类的误导信息。
- 刷新页面后不残留上一次计时。
- 窄屏（390px）状态条不横向溢出；可精简为图标 + 数字。
- 现有实时/历史/检测/缓存信息不丢失。

**回滚方式**

- 后端字段向后兼容，前端可忽略；删除 `elapsed_ms/disk_type_count` 与状态条新增部分即可。
- 或加前端 feature flag `showSearchMeta`，默认开，出问题关。

**风险**

- 计时器未清理导致内存泄漏/重复计时 → 在 `beforeUnmount` 清理。
- 缓存命中时 `elapsed_ms` 很小，用户可能误解 → 标注“缓存”或显示“服务端耗时”。

---

### U-P0-2 网盘计数

**目标**：每个网盘 chip 显示当前结果数量；计数不随筛选选择而错误变化。

**涉及文件**

- `backend/api/__init__.py`（`_do_search`、`_dedup_and_rank`、`_group_resources`、`/api/search` 响应）
- `frontend/src/pages/ResourcePage.vue`
- `frontend/src/constants.js`
- `frontend/src/style.css`

**后端改动**

1. 在融合、去重、分组后、分页切片前，基于**完整结果池**计算：
   ```python
   disk_counts = {}
   for card in cards:
       dt = card.get("disk_type") or "others"
       disk_counts[dt] = disk_counts.get(dt, 0) + 1
   ```
2. 当前 `/api/search` 把 `disk_types` 透传给 `index_db.search()` 和 PanSou `cloud_types`，导致计数只反映已筛选集合。P0 建议：
   - **方案 A（推荐，改动小）**：保留源端过滤，但计数只在“全部”状态显示；选中筛选后隐藏计数。
   - **方案 B（体验最好，改动中等）**：缓存完整结果池（不含 `disk_types`），请求时在融合层过滤并计算计数。缓存键去掉 `disk_types`，但会增加单次召回量与内存。
3. 先实施方案 A，验收后再评估 B；无论哪种，响应都返回 `disk_counts`。
4. 计数应排除已隐藏的失效资源卡（与主列表一致），并在文档中写明口径。

**前端改动**

1. chip 文案：`{{ getDiskLabel(t) }} ({{ disk_counts[t] || 0 }})`。
2. “全部” chip 显示总数。
3. 数量为 0 的类型：默认隐藏或置灰（二选一，保持一致）。
4. 移动端 chip 横向滚动（见 U-P0-6）。

**验收标准**

- 各类型计数之和 = 当前结果总数（在“全部”状态下）。
- 选中某类型后，结果数与计数口径一致（不会出现“计数 283、列表 0”）。
- 隐藏失效后计数同步减少。
- 空结果时 chip 不显示或显示 0，不报错。
- 大结果集（500+ 卡）计数耗时不超过 50ms（本地基准）。

**回滚方式**

- 前端忽略 `disk_counts`，恢复纯文字 chip。
- 后端保留字段无害；删除计算逻辑即可。

**风险**

- 计数口径与分页冲突：必须基于完整结果集，不是当前页。
- 源端过滤导致计数不完整：先按方案 A 明确展示规则，后续再改方案 B。
- 大结果集聚合成本：一次 O(n)，随缓存复用，通常可接受。

---

### U-P0-3 网盘多选 + URL 状态

**目标**：支持多选网盘类型、“全部”清除、URL 可分享/刷新恢复。

**涉及文件**

- `frontend/src/pages/ResourcePage.vue`
- `frontend/src/style.css`
- `backend/api/__init__.py`（确认 `disk_types` 数组已支持）

**后端改动**

1. 确认 `/api/search` 的 `disk_types` 支持数组（当前已是）。
2. 缓存键使用 `",".join(sorted(disk_types))`，确保多选组合不会串缓存（当前已按 sorted 拼接）。
3. 若采用 U-P0-2 方案 B，过滤移到融合层；否则保持现状。

**前端改动**

1. `diskType: ''` 改为 `selectedDiskTypes: []`。
2. `searchParams()`：
   ```js
   if (this.selectedDiskTypes.length) p.disk_types = [...this.selectedDiskTypes]
   ```
3. `toggleDisk(t)`：存在则移除，不存在则加入；空数组表示“全部”。
4. URL：
   - 写入 `?disk=quark,baidu`；不选时删除该参数。
   - `$route` watch 中解析 `disk` 并同步 `selectedDiskTypes`。
   - 避免 watch 与 setDisk 互相触发：用 `syncingFromRoute` 标志或比较值。
5. 搜索/翻页/改每页条数时保留 `disk` 参数。
6. 移动端 `.chips-row.filters` 横向滚动、`flex-wrap: nowrap`、边缘渐隐。

**验收标准**

- 单选/多选/全部切换正确；结果与 URL 同步。
- 刷新、前进/后退后筛选状态恢复。
- 多选组合的缓存不串（A+B 与 A+C 结果不同）。
- 390px 下 chips 可横滑，不撑破页面。
- 空结果时仍可切换筛选。

**回滚方式**

- 保留 `diskType` 单值兼容层，出问题切回单选 UI。
- URL 参数可忽略，不影响后端。

**风险**

- 路由 watch 循环：用值比较/标志位。
- 多选 + 分页：切换筛选要重置到第 1 页。
- 后端 `cloud_types` 对多选的支持需实测（PanSou 参数语义）。

---

### U-P0-4 排序

**目标**：默认/时间/名称三种排序，服务端排序后再分页，URL 可分享。

**涉及文件**

- `backend/api/__init__.py`
- `frontend/src/pages/ResourcePage.vue`
- `frontend/src/style.css`

**后端改动**

1. `/api/search` 读取 `sort`：
   - `relevance`（默认）：保持现有 `_dedup_and_rank` 顺序。
   - `time`：按 `datetime`/`last_seen` 降序；缺失时间的排最后。
   - `name`：按 `title` 本地化比较（`locale.strxfrm` 或简单 `casefold`；中文可用 `pypinyin` 可选，不强制引入）。
2. 排序必须在分页切片之前。
3. 缓存键**不必**包含 `sort`：缓存完整 `cards`（已按 relevance 排序），每次请求再排序 + 分页；排序 1000 张卡成本可接受。
4. 与有效性降权的关系：`relevance` 保留降权；`time/name` 只改变展示顺序，不改变有效性标记/隐藏逻辑。

**前端改动**

1. 状态条旁增加 `<select v-model="sort">`：默认排序/按时间/按名称。
2. `searchParams()` 增加 `sort`。
3. URL `?sort=time`；默认不写或写 `relevance`。
4. 切换排序重置到第 1 页，保留 `kw/disk`。
5. `$route` watch 同步 `sort`。

**验收标准**

- 三种排序结果符合预期；时间排序稳定（同时间用标题/URL 兜底）。
- 默认排序与改动前逐页对比一致（抽样 3 页）。
- URL 可分享，刷新/前进后退保持。
- 切换排序不重复请求两次（避免 watch + change 双触发）。
- 失效资源仍隐藏/降权，不因排序回到顶部。

**回滚方式**

- 默认 `relevance`，隐藏下拉即可；后端保留参数无副作用。
- 出问题可强制忽略 `sort`。

**风险**

- 中文名称排序不理想：先按 Unicode/`localeCompare`，不引入大依赖。
- 时间字段格式不统一：解析失败排最后，不抛异常。
- 缓存 + 排序一致性：确保缓存的是未分页完整列表。

---

### U-P0-5 同名变体编号 chip

**目标**：把“同资源其他 N 个链接”展开行升级为编号 chip/折叠面板，点击切换当前变体。

**涉及文件**

- `frontend/src/components/ResourceCard.vue`
- `frontend/src/style.css`
- `frontend/src/constants.js`（如需复用标签/徽章函数）

**前端改动**

1. 增加 `activeIndex: 0`（0 = 主链接，1..N = 变体）。
2. `computed current()`：返回当前选中的链接对象（主链接或变体）。
3. 卡片主操作（打开/复制链接/复制提取码/举报）改用 `current`。
4. 变体区：
   - 编号 chip：`1 2 3 … N`（主链接为 1，变体依次）。
   - 当前 chip 高亮；每个 chip 的 `title` 显示网盘/有效性。
   - 点击切换，不刷新页面。
   - 变体多时用横向滚动/折叠，避免卡片过高。
5. 每个变体仍显示网盘 tag、有效性徽章、提取码（如有）。
6. 失效变体明确标识；当前变体失效时保留提示，不自动跳转。

**验收标准**

- 切换变体无需刷新；主操作跟随当前变体。
- 当前变体高亮；网盘/有效性/提取码正确。
- 失效变体有明确标识。
- 390px 下编号 chip 不横向溢出。
- 键盘可达（Tab/Enter），有 aria-label。

**回滚方式**

- 保留原展开行模板，出问题切回 `expanded` 模式。
- 新逻辑集中在 `ResourceCard.vue`，可单独回退。

**风险**

- 主链接与变体字段不一致：以 `current` 为准，缺失字段用主链接兜底。
- 卡片高度/移动端布局：先做折叠，默认只显示编号行。
- 举报事件：`$emit('report', current)`，确保父级处理的是当前变体。

---

### U-P0-6 移动端修复

**目标**：修复已确认的移动端缺陷：tabbar 图标空白、SearchDock 不同步、筛选换行、状态条拥挤、卡片信息过多。

**涉及文件**

- `frontend/src/components/AppShell.vue`
- `frontend/src/components/SearchDock.vue`
- `frontend/src/pages/ResourcePage.vue`
- `frontend/src/style.css`

**前端改动**

1. `AppShell.vue` 的 `menus` 增加 `icon` 字段（内联 SVG 或统一 emoji；不引入大依赖）：
   ```js
   { path: '/resource', label: '资源搜索', icon: '…' }
   ```
   确保 `tabbar` 的 `{{ m.icon }}` 有内容。
2. `SearchDock.vue` 增加：
   ```js
   watch: { initial(v) { if (v !== this.kw) this.kw = v } }
   ```
   只在 props 变化时同步，不覆盖用户正在输入的内容。
3. `ResourcePage.vue`：
   - 筛选 chips 移动端横向滚动（与 U-P0-3 共用）。
   - 状态条窄屏精简：保留结果数/类型数/耗时，隐藏次要标签或换行。
   - 资源卡移动端隐藏 `.res-url`/`.res-src` 等次要信息，保留标题、网盘、有效性、操作。
4. `style.css` 增加 `@media (max-width: 768px)` 规则；不改变桌面布局。

**验收标准**

- 390px 宽度下无横向溢出。
- tabbar 6 个图标可见、可点击、与文字对齐。
- 从结果页点击热搜/历史后，SearchDock 输入框同步新关键词。
- 筛选可横向滚动，不遮挡内容。
- 状态条不换行错位；卡片操作按钮可点。
- 768px/1280px 桌面布局不回归。

**回滚方式**

- CSS 改动可整体回退；`menus` 增加 `icon` 是增量字段，不影响桌面。
- `SearchDock` watch 可移除。

**风险**

- emoji 在不同系统渲染不一致 → 优先内联 SVG。
- watch 覆盖用户输入 → 只在 `initial` 真正变化时同步。
- 移动端隐藏信息过多 → 保留“来源”在详情/抽屉中可查。

---

### U-P0-7 二维码补充交付

**目标**：在卡片/抽屉增加“手机打开”二维码，保留直链和复制主路径；二维码只编码分享链接。

**涉及文件**

- `frontend/package.json`（新增轻量 QR 依赖）
- 新增 `frontend/src/components/ResourceQr.vue`
- `frontend/src/components/ResourceCard.vue`
- `frontend/src/style.css`

**前端改动**

1. 选择轻量、纯前端、无外部服务的 QR 库（如 `qrcode` 或 `qrcode-generator`）；构建时打包，不在运行时请求第三方。
2. `ResourceQr.vue`：
   - props：`url`、`title`、`password`（仅用于 UI 展示，不编码进二维码）。
   - 生成 256×256 左右的二维码（容错级别 M）。
   - 显示“使用手机相机或网盘 App 扫码”；URL 过长时提示复制。
   - 提供“复制链接/复制提取码/关闭”。
3. `ResourceCard.vue`：
   - 增加“二维码”按钮；点击打开小弹窗/底部抽屉。
   - 不改变现有“打开/复制链接/复制提取码/举报”行为。
4. 二维码内容只包含 `url`；不要把 `password`、Cookie、token、账号拼进二维码。

**验收标准**

- 手机相机/网盘 App 可识别二维码并打开分享链接。
- 直链打开、复制链接、复制提取码不回归。
- 二维码不含提取码/Cookie/账号/令牌。
- 无网络时仍能生成二维码（库在本地）。
- 移动端弹窗不遮挡关闭按钮；ESC/遮罩可关闭。

**回滚方式**

- 隐藏“二维码”按钮；删除组件/依赖。
- 不影响主搜索与直链。

**风险**

- 依赖体积：选轻量库；构建后检查 bundle 大小。
- 长 URL 二维码密度高：提高尺寸/降低容错或提示复制。
- 安全：二维码只编码分享链接；提取码单独展示。

---

### U-P0-8 公告条 + 法律页

**目标**：增加可配置公告条和页脚法律入口；法律页当前不存在，需要新建。

**涉及文件**

- 新增 `backend/site_config.py` 或 `backend/data/site_config.json`
- `backend/api/__init__.py`（`GET /api/site-config`）
- `frontend/src/components/AppShell.vue`
- 新增 `frontend/src/pages/LegalPage.vue`
- `frontend/src/router.js`
- `frontend/src/style.css`

**后端改动**

1. 新增轻量站点配置（P0 可先用 JSON/环境变量，P1 再做写入后台）：
   ```json
   {
     "notice": {"enabled": true, "text": "本站公告…", "version": "2026-10-19"},
     "footer": {"legal": true}
   }
   ```
2. `GET /api/site-config` 只返回公开安全字段；不返回 token/账号/管理配置。
3. 读取失败时返回默认值，不影响搜索。

**前端改动**

1. `AppShell.vue` 启动时 `GET /api/site-config`；有公告则显示公告条。
2. 关闭公告：`localStorage.dps-notice-seen-<version>`；版本变化后重新提示。
3. 页脚增加“净网声明 / 版权说明 / 免责声明”入口。
4. 新增 `LegalPage.vue` + 路由：
   - `/legal/clean-network`
   - `/legal/copyright`
   - `/legal/disclaimer`
5. 文案由本站撰写，需审核；不复用 aliso 文案。

**验收标准**

- 公告可配置、可关闭、刷新不重复打扰；版本更新后会重新提示。
- 页脚法律入口可访问；移动端公告换行正常，不横向裁切。
- 关闭公告不影响搜索/筛选/排序。
- 法律页有返回入口；404 不出现。
- `GET /api/site-config` 不含敏感字段。

**回滚方式**

- 公告可一键 `enabled=false`；前端隐藏公告条。
- 法律路由可下线，页脚入口可隐藏。
- 配置读取失败时默认不显示公告。

**风险**

- 法律文案准确性 → 必须审核；本文档不构成法律意见。
- 公告版本管理 → 用 `version` 而不是时间戳，避免每次刷新都提示。
- 公开配置泄露 → 白名单字段，写入必须站长鉴权（P1）。

---

## 2. P1 任务详情

### U-P1-1 渐进式搜索

**目标**：边搜边出，显示已用时/已收到/持续搜索中，支持取消；失败回退一次性搜索。

**涉及文件**

- `backend/api/__init__.py`（新增 `POST /api/search/stream`）
- `frontend/src/pages/ResourcePage.vue`
- 可选：新增 `frontend/src/composables/useStreamSearch.js`

**后端改动**

1. 新增 `POST /api/search/stream`，返回 `application/x-ndjson`：
   ```text
   {"type":"start","elapsed_ms":0}
   {"type":"batch","source":"archive","results":[...],"received":N}
   {"type":"batch","source":"pansou","results":[...],"received":N}
   {"type":"done","total":N,"disk_counts":{...},"elapsed_ms":...}
   {"type":"error","message":"..."}
   ```
2. 并行/分阶段调用本地索引、PanSou、学霸盘；先到先推送。
3. 客户端断开时停止未完成的上游请求（`GeneratorExit`/`request.environ` 检查）。
4. 保留 `POST /api/search` 作为回退；流式只负责第一页/首批，后续分页仍用 `/api/search` 的缓存结果池。
5. 去重：流式批次之间按 URL/规范化标题去重；最终 `done` 给出汇总计数。

**前端改动**

1. `fetch` + `response.body.getReader()` + `TextDecoder` 按行解析。
2. 状态：`searching / streaming / done / cancelled / error`；计时器与已收到计数。
3. `AbortController`：点击“停止”后保留已收结果，显示“搜索已取消”。
4. 流式失败/无结果自动回退 `POST /api/search`。
5. 分页按钮在流式完成后仍可用（走 `/api/search`）。
6. 兼容移动端：状态条精简。

**验收标准**

- 首条结果出现时间明显早于完整结果（用同一关键词对比）。
- 取消后不再追加结果；已收结果保留。
- 流式失败自动回退，用户能看到完整结果或明确错误。
- 分页/筛选/排序在流式完成后仍正确。
- 不破坏现有 `/api/search` 缓存与分页。

**回滚方式**

- 环境变量/前端开关 `STREAM_SEARCH=0`，直接走 `/api/search`。
- 保留旧接口与旧 UI 路径。

**风险**

- PanSou 上游是请求/响应式，不一定支持流式；流式层必须由本站后端做多源并行/分块。
- 流式 + 分页语义复杂：只流首批，分页走缓存。
- 批次去重/排序与最终结果不一致：以 `done` 的汇总为准，前端做一次校正。
- 连接中断/代理缓冲：确保 nginx 关闭缓冲或使用合适头。

---

### U-P1-2 本地归档页 + 接口参数

**目标**：把已有 `/api/archive` 和本地索引变成可见功能；支持关键词、网盘、仅存活、时间范围、分页。

**涉及文件**

- `backend/api/__init__.py`（`/api/archive`）
- `backend/indexer/__init__.py`（如增加时间范围查询）
- 新增 `frontend/src/pages/ArchivePage.vue`
- `frontend/src/router.js`
- `frontend/src/components/AppShell.vue`
- `frontend/src/pages/ResourcePage.vue`（“只搜本地”开关）

**后端改动**

1. `/api/archive` 增加参数：
   - `page`（默认 1）
   - `page_size`（默认 20/50，设上限）
   - `since_days`（可选，按 `last_seen`/`first_seen` 过滤）
   - `sort`（可选：recent/alive）
2. 返回 `total/page/page_size/total_pages`。
3. SQL 增加 `COUNT(*)` 与 `LIMIT/OFFSET`；注意动态 WHERE 的参数顺序。
4. 复用现有索引，不新建表。
5. `only_alive` 默认保持 `true`，与现有行为兼容。

**前端改动**

1. 新增 `ArchivePage.vue`：
   - 筛选：关键词、网盘类型、仅存活、时间范围（7/30/90 天/全部）。
   - 分页；空态/加载态/错误态。
   - 复用 `ResourceCard`。
2. 路由 `/archive`；侧栏/移动菜单加入口。
3. 搜索页增加“只搜本地”开关，`searchParams()` 传 `only_archive: true`。
4. 未搜索态可增加“本地归档”入口（可选）。

**验收标准**

- 可按关键词/网盘/存活/时间筛选；分页正确。
- 与主搜索共享资源卡；打开/复制/举报行为一致。
- 空态/加载态/错误态完整。
- 390px 无横向溢出。
- 主搜索“只搜本地”只返回历史索引结果，不调 PanSou。

**回滚方式**

- 独立路由可下线；侧栏入口可隐藏。
- 后端新增参数向后兼容；默认行为不变。

**风险**

- `COUNT(*)` 在大表上变慢 → 加索引/缓存；先做基准。
- 时间字段格式不统一 → 用 `last_seen` 为主，解析失败忽略。
- 归档结果与主搜索结果重复 → 复用同一资源卡与去重逻辑。

---

### U-P1-3 资源详情抽屉

**目标**：统一展示资源详情与全部变体，减少卡片信息堆叠；保留卡片快捷操作。

**涉及文件**

- 新增 `frontend/src/components/ResourceDetailDrawer.vue`
- `frontend/src/components/ResourceCard.vue`
- `frontend/src/pages/ResourcePage.vue`
- `frontend/src/style.css`

**前端改动**

1. 抽屉内容：
   - 标题、网盘、来源、时间、有效性；
   - 全部变体（编号/列表）；
   - 直链、提取码、复制、打开、二维码；
   - 失效举报；
   - 有效性历史（如果后端提供；当前没有，先不做）。
2. 打开方式：卡片标题/“详情”按钮；桌面右侧抽屉，移动端底部全屏抽屉。
3. 抽屉打开/关闭不影响搜索状态；ESC/遮罩关闭；焦点返回触发元素。
4. 复用 `ResourceQr` 与变体数据；避免重复请求。

**验收标准**

- 抽屉打开/关闭不刷新搜索、不丢失筛选/排序。
- 变体切换不刷新；当前变体高亮。
- 移动端全屏/底部抽屉适配；关闭按钮可点。
- 键盘可达；aria-label 完整。
- 直链/复制/二维码/举报行为与卡片一致。

**回滚方式**

- 独立组件，隐藏“详情”入口即可回退。
- 卡片原有操作保留，不依赖抽屉。

**风险**

- 与卡片功能重复 → 抽公共方法/组件，避免两套逻辑。
- 移动端抽屉高度/滚动 → 先做底部全屏，减少手势复杂度。
- 焦点管理 → 打开记录触发元素，关闭后 focus 回去。

---

### U-P1-4 统计页修复

**目标**：去掉硬编码“运行中”，使用真实巡检器状态；用已有 `growth` 数据画索引增长。

**涉及文件**

- `backend/api/__init__.py`（`/api/stats`）
- `frontend/src/pages/StatsPage.vue`
- `frontend/src/style.css`

**后端改动**

1. `/api/stats` 的 `checker` 增加 `running: checker.is_running()`（当前 `get_stats()` 不含 running）。
2. `harvester` 已有 `running`，保持不变。
3. `growth` 已有 `[{date, new_links}]`，保持不变。

**前端改动**

1. 巡检器状态使用 `stats.checker.running`，不再硬编码。
2. 新增“索引增长”区块：用 `stats.analyzer.growth` 画简单柱状/折线（SVG/CSS，不引入图表库）。
3. 空数据时显示“暂无增长数据”。
4. 保持现有索引/分布/来源/热词区块。

**验收标准**

- 巡检器启动/停止时页面显示真实状态。
- growth 数据按日期升序展示；空数据不报错。
- 移动端图表不横向溢出。
- 不改变 `/api/stats` 现有字段。

**回滚方式**

- 前端可单独回退图表区块；后端新增 `running` 字段向后兼容。

**风险**

- `growth` 数据点过多 → 只显示最近 14 天（API 已按 14 天）。
- SVG 手写图表可访问性 → 提供文本摘要/标题。

---

### U-P1-5 站点配置

**目标**：公告、首页词条、页脚法律入口、网盘开关可配置；公开读、站长写。

**涉及文件**

- 新增/扩展 `backend/site_config.py`
- `backend/api/__init__.py`
- `frontend/src/pages/AccelPage.vue` 或新增 `frontend/src/pages/SiteConfigPage.vue`
- `frontend/src/router.js`
- `frontend/src/components/AppShell.vue`

**后端改动**

1. 配置文件：`backend/data/site_config.json`（默认值 + 原子写入）。
2. `GET /api/site-config`：公开安全字段（公告、页脚、首页词条、网盘显示开关）。
3. `PUT /api/site-config`：`is_station(request)` 鉴权；字段白名单校验；写入前备份。
4. 不把 `ACCEL_TOKEN`、账号、Cookie、管理配置放入公开响应。

**前端改动**

1. 站长模式（现有 AccelPage 或新页面）增加“站点配置”区块：
   - 公告开关/文本/版本；
   - 首页热门词条（可选，覆盖 `/api/hot`）；
   - 页脚法律入口开关；
   - 网盘筛选显示开关（可选）。
2. 保存后提示；失败显示服务端错误。
3. 公告条读取同一配置（与 U-P0-8 对接）。

**验收标准**

- 修改公告/词条后首页/搜索页生效。
- 未授权用户不能写入（403）。
- 配置变更可回滚（保留上一版备份）。
- 公开接口不含敏感字段。
- 配置错误不影响搜索默认行为。

**回滚方式**

- 删除配置文件回默认值；前端隐藏配置入口。
- 写入接口可禁用，保留只读。

**风险**

- 公开配置面扩大 → 白名单字段 + 鉴权 + 审计日志。
- 并发写入 → 原子写/锁；保留备份。
- 配置缓存 → 读取短 TTL，保存后失效。

---

## 3. P2 任务详情

### U-P2-1 需求板

**目标**：匿名提交 + 站长审核 + 想要投票 + 基础防刷。

**涉及文件**：新增 `backend/demands.py` 或 `backend/api/demands.py`；`frontend/src/pages/DemandPage.vue`；`frontend/src/router.js`；`backend/indexer/__init__.py`（新表）。

**后端改动**：`demands`/`demand_votes` 表；`POST /api/demand`、`GET /api/demands`、`POST /api/demand/{id}/want`；站长审核/删除；IP/频率限制、敏感词、内容长度限制。

**前端改动**：`/demand` 页面；表单校验；历史列表；状态徽章；想要按钮；移动端适配。

**验收标准**：提交有校验/防刷；审核状态正确；投票幂等/可取消；垃圾需求可删除；未登录可提交/查看。

**回滚方式**：独立路由/表，可停用；不影响搜索。

**风险**：垃圾内容/运营负担；无审核能力不要上线。

---

### U-P2-2 加速面板补全

**目标**：把已有但未上屏的加速端点做成可操作面板。

**涉及文件**：`frontend/src/pages/AccelPage.vue`；`backend/accel/routes.py`（确认端点）。

**后端改动**：无或仅补字段；现有 `/speedup`、`/speedup/diagnose`、`/accounts`、`/tasks/{id}/priority` 已存在。

**前端改动**：增加调速/诊断/账号池/任务优先级区块；保留现有状态/登录/解析/下载。

**验收标准**：调速生效；诊断有结果；账号增删改查正常；任务优先级可切换；错误有提示。

**回滚方式**：隐藏新区块；现有 AccelPage 功能不受影响。

**风险**：账号/Cookie 敏感；不要在前端日志/URL 暴露；与访客配额隔离。

---

### U-P2-3 FTS5 索引

**目标**：数据量大时替代/补充 SQLite `LIKE`，保证搜索延迟与相关性。

**涉及文件**：`backend/indexer/__init__.py`；迁移脚本；`backend/config.py`。

**后端改动**：新建 FTS5 虚拟表/触发器或双写；查询走 FTS5，保留 LIKE 回退；基准测试。

**前端改动**：无。

**验收标准**：同等关键词结果相关性不下降；P95 查询延迟改善；迁移可回滚；旧库可继续用。

**回滚方式**：开关切回 LIKE；保留原表。

**风险**：FTS5 分词对中文不友好（需 trigram/自定义）；迁移失败；索引膨胀。

---

### U-P2-4 跨源标题聚合

**目标**：按规范化标题合并跨源重复卡片，显示“N 个来源/变体”。

**涉及文件**：`backend/api/__init__.py`（`_group_resources`/规范化）；`frontend/src/components/ResourceCard.vue`。

**后端改动**：标题规范化（去广告词/标点/空格/季集格式）；聚合阈值；保留变体列表。

**前端改动**：卡片显示来源数/变体数；变体切换。

**验收标准**：重复卡片减少但相关性不下降；不把不同资源误合并；变体可追溯。

**回滚方式**：关闭聚合开关，恢复原分组。

**风险**：误合并；标题规范化规则需要样本验证。

---

### U-P2-5 转存/保存网盘（评估）

**目标**：评估是否需要类似 aliso 的转存能力；默认不开启。

**涉及文件**：`backend/accel/`（现有百度能力）；新增转存适配层（如做）。

**后端改动**：账号授权、转存接口、失败重试、配额；与加速账号隔离。

**前端改动**：如做，弹窗/抽屉增加“保存到我的网盘”；默认隐藏。

**验收标准**：授权/失败/配额提示清晰；不泄露 Cookie/令牌；可一键关闭。

**回滚方式**：功能开关默认关闭；删除路由/入口。

**风险**：账号合规、平台风控、法律风险；没有明确授权体系不做。

---

### U-P2-6 多站点/工具导航

**目标**：存在视频解析/少儿资源等衍生项目时，提供“更多工具”入口。

**涉及文件**：`frontend/src/components/AppShell.vue`；`frontend/src/constants.js`。

**后端改动**：无。

**前端改动**：侧栏/移动菜单增加“更多工具”；配置化链接。

**验收标准**：入口可见但不抢搜索主流程；移动端不遮挡 tabbar。

**回滚方式**：隐藏入口。

**风险**：稀释搜索体验；没有衍生项目不做。

---

### U-P2-7 微信小程序/移动承接

**目标**：有明确移动产品规划时再做；否则二维码 + 响应式网页足够。

**涉及文件**：独立项目；主站仅增加入口/二维码。

**后端改动**：按小程序需求另建 API。

**前端改动**：入口/banner。

**验收标准**：入口不误导；无小程序时不要放空二维码。

**回滚方式**：移除入口。

**风险**：运营投入大；与主站数据/权限边界。

---

### U-P2-8 全站历史/热搜

**目标**：只展示聚合热搜，不展示他人原始搜索词。

**涉及文件**：`backend/api/__init__.py`（`/api/hot`）；`frontend/src/pages/ResourcePage.vue`。

**后端改动**：聚合/降噪/去重；不返回原始用户搜索记录。

**前端改动**：热搜 chips/跑马灯；不展示“某用户搜过什么”。

**验收标准**：热搜不包含隐私/敏感词；无原始历史泄露。

**回滚方式**：关闭热搜展示。

**风险**：隐私/合规；热词被刷。

---

### U-P2-9 手动批量重检

**目标**：现有搜索后自动检测已覆盖首屏 30 条；只补“重检本页/进度/失败重试”。

**涉及文件**：`frontend/src/pages/ResourcePage.vue`；`backend/api/__init__.py`（`/api/check/batch`）。

**后端改动**：无或增加 `force` 参数（已有 `force`）。

**前端改动**：增加“重新检测本页”按钮；显示检测中/完成/失败；更新徽章。

**验收标准**：重检不阻塞页面；结果与后端一致；失败可重试。

**回滚方式**：隐藏按钮；自动检测保留。

**风险**：重复检测浪费资源；加频率限制/只对当前页。

---

## 4. 第一个迭代（可直接开工的 5~7 项）

建议第一个迭代做 **U-P0-1 ~ U-P0-7**，U-P0-8 与法务文案并行、不阻塞搜索上线：

1. **U-P0-1 搜索状态条 + 耗时**（前后端各一处，先做）
2. **U-P0-2 网盘计数**（先方案 A，明确计数口径）
3. **U-P0-3 网盘多选 + URL 状态**
4. **U-P0-4 排序**（服务端排序 + URL）
5. **U-P0-5 同名变体编号**
6. **U-P0-6 移动端修复**
7. **U-P0-7 二维码补充交付**

并行项：

8. **U-P0-8 公告条 + 法律页**（文案审核可与 1~7 并行；法律页上线不依赖搜索改造）

**迭代顺序建议**

```text
Day 1：U-P0-1 + U-P0-6（互不依赖，先修可见缺陷）
Day 2-3：U-P0-2 + U-P0-3（计数/多选共用后端改动）
Day 3-4：U-P0-4（排序）
Day 4-5：U-P0-5 + U-P0-7（纯前端）
Day 5-6：联调、回归、移动端验收
```

**为什么不是先做渐进式搜索**

- 它需要后端多源并行/分块、前端流式解析、取消/回退、分页语义，成本和风险都高；
- P0 的状态条/计数/排序/变体已经能显著改善“过程感”和“决策效率”；
- 先稳定一次性搜索，再引入流式，回滚更容易。

---

## 5. 测试矩阵

### 5.1 功能回归

- 搜索：关键词、空关键词、特殊字符、长关键词、零结果、网络错误、PanSou offline。
- 筛选：单选/多选/全部/无结果/计数一致性。
- 排序：三种排序 + 分页 + URL + 默认排序回归。
- 变体：单变体/多变体/失效变体/切换后操作。
- 二维码：生成/识别/关闭/长 URL/无网络。
- 移动端：390px、768px、1280px；tabbar、筛选横滑、状态条、卡片。
- 主题：亮/暗切换 + 刷新保持。
- 公告：显示/关闭/刷新不重复/版本更新。
- 法律页：路由/返回/移动端。
- 归档（P1）：关键词/网盘/存活/时间/分页/空态。
- 需求板（P2）：提交/审核/投票/防刷。

### 5.2 性能

- 搜索首条结果时间、完整结果时间；
- `disk_counts` 聚合耗时；
- 大结果集（500+）渲染性能；
- 移动端滚动/筛选流畅度；
- `/api/archive` COUNT/OFFSET 在 10 万/100 万行下的延迟。

### 5.3 可访问性

- 键盘可达：搜索框、筛选、排序、变体、弹窗/抽屉关闭。
- aria-label：主题切换、二维码、关闭按钮、变体 chip。
- 对比度：亮/暗主题。
- 焦点管理：弹窗/抽屉打开/关闭后焦点返回触发元素。

### 5.4 安全/合规

- 不复制对方代码/样式/文案/图片；
- 不调用对方接口；
- 二维码不含敏感信息；
- 需求板有防刷/审核；
- 公开配置不含 token/账号；
- 公开部署前移除硬编码 `ACCEL_TOKEN` 并轮换；
- 反向代理下验证 `is_station` 不会把所有访客当站长。

---

## 6. 回滚总表

| 改动 | 回滚方式 | 回滚粒度 |
|---|---|---|
| `/api/search` 新增字段 | 字段向后兼容；前端忽略/删除 UI | 单字段 |
| 网盘计数 | 隐藏 chip 数字 | 前端 |
| 多选筛选 | 切回单值 `diskType` | 前端 |
| 排序 | 默认 `relevance`，隐藏下拉 | 前端/参数 |
| 变体编号 | 恢复原展开行 | 组件 |
| 移动端 CSS | 回退 `style.css` 对应 media query | CSS |
| 二维码 | 隐藏入口/删依赖 | 组件 |
| 公告/法律页 | 关闭公告/下线路由 | 配置/路由 |
| 流式搜索 | `STREAM_SEARCH=0` 走 `/api/search` | 开关 |
| 归档页 | 下线路由/隐藏入口 | 路由 |
| 详情抽屉 | 隐藏入口，保留卡片操作 | 组件 |
| 统计页图表 | 回退区块 | 前端 |
| 站点配置 | 删配置文件回默认；禁用写入 | 配置/接口 |
| 需求板 | 停用路由/表 | 独立模块 |
| FTS5 | 切回 LIKE | 开关 |
| 转存 | 默认关闭/删入口 | 开关 |

---

## 7. 风险清单（执行视角）

| 风险 | 触发点 | 影响 | 缓解 |
|---|---|---|---|
| 搜索缓存键遗漏新参数 | U-P0-3/U-P0-4 | 结果串缓存 | 缓存键包含 `kw/only_archive/disk_types/src`；排序在缓存后做 |
| 计数口径不一致 | U-P0-2 | 用户不信任 | 明确“全部状态计数”；隐藏失效同步；验收抽样 |
| 路由 watch 循环 | U-P0-3/U-P0-4 | 重复请求/卡死 | 值比较 + 标志位；只在一个入口写 URL |
| 移动端布局回归 | U-P0-6 | 体验下降 | 390/768/1280 三档截图回归 |
| QR 依赖过大 | U-P0-7 | 首屏变慢 | 轻量库 + 懒加载组件 |
| 法律文案不准确 | U-P0-8 | 合规风险 | 审核；不复制对方文案 |
| 流式上游不兼容 | U-P1-1 | 延期 | 后端多源并行；保留回退 |
| 流式分页语义 | U-P1-1 | 结果重复/丢失 | 只流首批；分页走缓存；done 汇总校正 |
| 归档 COUNT 慢 | U-P1-2 | 页面卡顿 | 索引/缓存/限制时间范围 |
| 配置公开面泄露 | U-P1-5 | 安全 | 白名单字段 + 站长鉴权 + 审计 |
| `ACCEL_TOKEN` 硬编码 | 部署 | 权限泄露 | 环境变量/轮换/清理脚本（见 §8） |
| 反向代理误判站长 | 部署 | 配额/权限失效 | `TRUST_PROXY=1` + `ProxyFix` + 只信任内网直连方 |
| dist 未重建 | 发版 | 页面旧 | 每次 `npm run build`；检查 hash |
| 索引数据损坏 | U-P2-3 | 搜索不可用 | 备份 + 双写 + 回滚 LIKE |

---

## 8. 公开部署前必须处理

1. **`ACCEL_TOKEN` 硬编码**
   - 位置：`docker-compose.yml:66`、`启动本机加速器.bat:10`、`scripts/run_accel_hidden.vbs:8`。
   - 处理：改为环境变量/本地未跟踪配置；轮换令牌；清理 shell 历史/日志中的值；文档不复述具体值。
2. **反向代理站长判定**
   - 位置：`backend/access.py:55-63`、`backend/app.py:154-167`。
   - 处理：确保 `TRUST_PROXY=1` 且 `ProxyFix` 生效；只信任内网/回环直连方的 `X-Forwarded-For`；部署后从公网验证 `/api/history` 返回 403（访客）而不是 200。
3. **法律页**
   - 当前不存在；P0 新建净网/版权/免责声明；文案审核。
4. **前端构建**
   - `docker-compose.yml:77` 挂载 `frontend/dist`；发版前 `cd frontend && npm run build`，确认 `index.html` 引用新 hash。
5. **二维码**
   - 只编码分享链接；不编码提取码/Cookie/账号/令牌。
6. **公开配置**
   - 如新增 `/api/site-config` 写入，必须站长鉴权；公开读只返回安全字段。

---

## 9. 交付顺序与依赖

```text
P0-1 状态条/耗时 ──┐
P0-6 移动端修复  ──┤（可并行）
P0-2 网盘计数 ─────┼──> P0-3 多选/URL ──> P0-4 排序
P0-5 变体编号 ─────┤
P0-7 二维码 ───────┘
P0-8 公告/法律页 ──（并行，文案审核）

P1-1 渐进式搜索 ──（依赖 P0-1 状态机）
P1-2 归档页 ──────（依赖后端参数）
P1-3 详情抽屉 ────（依赖 P0-5/P0-7）
P1-4 统计页修复 ──（独立）
P1-5 站点配置 ────（依赖 P0-8）

P2-1 需求板
P2-2 加速面板
P2-3 FTS5
P2-4 聚合优化
P2-5 转存评估
P2-6 多站点
P2-7 小程序
P2-8 全站历史/热搜
P2-9 手动重检
```

**依赖说明**

- U-P0-3 依赖 U-P0-2 的计数口径确定；否则多选后计数会自相矛盾。
- U-P0-4 依赖 U-P0-3 的 URL 状态约定（`kw/disk/sort`）。
- U-P1-1 依赖 U-P0-1 的状态与计时基础。
- U-P1-3 依赖 U-P0-5/U-P0-7 的变体与二维码组件。
- U-P1-5 依赖 U-P0-8 的配置读取。

---

## 10. Definition of Done

每个任务完成必须满足：

- [ ] 代码已实现，且只在任务涉及的文件内改动（或明确说明额外改动）。
- [ ] 旧行为回归通过；默认路径不受影响。
- [ ] 验收标准逐条验证，有截图/日志/命令输出。
- [ ] 移动端 390px、桌面 1280px 至少各验一次。
- [ ] 无新增敏感信息（token/Cookie/账号）进入前端或日志。
- [ ] 回滚方式已验证或明确可操作。
- [ ] 更新 09/10 的相关状态（如实现与计划不一致）。
- [ ] 发版前重新构建 `frontend/dist`。
