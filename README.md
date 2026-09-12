# DuPanSou-Archive · 网盘资源聚合搜索引擎

> 个人部署的网盘资源搜索工具，融合 **实时聚合搜索** 与 **历史索引归档**。
> 克隆即用：`python app.py` 打开 `http://localhost:5000`，无需构建前端。

```
        ┌───────────────────────────────┐
        │      DuPanSou-Archive        │
        │   双通道融合网盘搜索引擎       │
        └───────────────────────────────┘
        实时(搜新)  ⚡  +  ⚡ 历史(搜老)
        ┌───────────┐      ┌───────────┐
        │   PanSou    │      │  SQLite   │
        │ 实时聚合搜索 │      │  索引库     │
        └───────────┘      └───────────┘
```

---

## ✨ 项目亮点

| 能力 | 当年度盘搜 | PanSou | **DuPanSou-Archive** |
|------|:---:|:---:|:---:|
| 历史老资源搜索 | ✅ 强 | ❌ 弱 | ✅ **强** |
| 最新资源搜索 | ❌ 弱 | ✅ 强 | ✅ **强** |
| 死链自动标记 | ❌ | ✅ | ✅ |
| 个人索引积累 | ❌ | ❌ | ✅ **独创** |
| 零依赖部署 | ❌ 需MySQL/ES | ⚠️ 需Go | ✅ 纯Python+SQLite |

核心功能：

- **双通道融合搜索** — 本地 SQLite 索引（毫秒级）+ PanSou 实时聚合（100+ 插件），跨源去重、相关度排序
- **CloudSaver 风格界面** — PC 左侧导航 + 移动端底部标签栏，亮/暗双主题（跟随系统、可切换、防闪烁），vue-router 路由（`/resource` `/douban` `/stats`）
- **🩺 智能链接有效性检测** — 四级有效性状态机（有效 / 疑似失效 / 确认失效 / 未检测）：
  搜索结果自动送检，**失效降权、确认失效自动隐藏**（可一键展开查看）；
  连续 2 次失败才判死（防检测源抖动误杀），用户举报直接确认；
  检测结果按状态保鲜（有效 72h / 疑似 30min / 失效 12h 自动复查是否恢复），磁力/ed2k 等无法检测的类型永不误伤
- **🎬 豆瓣榜单 v2** — 11 个分类（热门电影/最新/冷门佳片/电视剧/国产/欧美/韩/日/动画/综艺/纪录片），
  数据来自豆瓣 v2 榜单接口：大海报 + 左上角评分角标（7.1/10）+ 右上角口碑档（神作/推荐/可看/一般）+
  年份/国家/类型/导演/主演简介行 + 评分人数；排序（默认/评分/上映年份）+ 滚动触底自动加载；
  「豆瓣详情」开条目页、「搜资源」一键转搜索
- **🖼 资源封面双通道** — 影视资源匹配豆瓣海报（标题智能清洗 + 多级降级匹配 + SQLite 永久缓存）；
  非影视资源使用 **TG 频道消息自带图**（PanSou 原生 images 字段，走后端代理解决国内直连问题）；
  图片加载四级链：浏览器直连 → 本地代理（子域轮换重试 + 内存缓存）→ weserv → 首字占位块；
  「封面图」开关记忆偏好
- **同资源聚合分组** — 同一资源的多个网盘/发布帖合并为一张资源卡，其余链接一键展开
- **网盘筛选 chips** — 全部/夸克/百度/阿里/迅雷/115/123/天翼/UC/PikPak/磁力，服务端过滤、计数准确
- **粘贴链接直检** — 搜索框粘贴网盘分享链接，直接返回有效性检测结果
- **分页浏览全量结果** — 完整资源池按页返回（10/20/50 每页可选），结果缓存 5 分钟，翻页零成本
- **点击热度置顶（死链防护）** — 结果点击经 `/api/click` 中转记录，同一关键词下被点过的资源加权置顶；点击瞬间后台智能体检 + 结果卡「失效举报」按钮，确认失效立即撤销热度并打失效标记，死链无法靠点击置顶
- **拼写容错** — Chrome 式"您是不是要找"：搜 `Xmimd` 自动纠正为 `xmind` 重搜并提示（词库取自本地索引+搜索历史，频次防回声污染）
- **书源扩展（学霸盘）** — 内置学习资料/书籍向自定义源，教材/课程/电子书查询自动并发补充检索；`ENABLE_XUEBAPAN` 可开关
- **零结果守望队列** — 搜不到的词自动入队，定时收割器每轮用全源重试，"今天搜不到的教材，有了就有"
- **搜索历史** — 本地记录搜索词，点击重搜、单删、清空
- **渐进式索引** — 每次搜索的实时结果自动入库，定时收割器持续扩充，搜得越多索引越强
- **优先级巡检** — 后台每小时按优先级体检索引库：疑似失效 → 新入库未检测 → 失效复查 → 过期保鲜
- **数据统计** — 网盘分布、热词榜、链接健康、索引增长曲线

---

## 🚀 快速开始

### 方式一：Docker Compose 一键全栈（推荐）

```bash
git clone https://github.com/caixiaoq/DuPanSou-Archive.git
cd DuPanSou-Archive
docker compose up -d
# 打开 http://localhost:5000
```

### 方式二：手动部署

**1. 启动 PanSou 搜索引擎（前置依赖）**

> ⚠️ 最小命令只监听 1 个默认频道、不启用插件，搜索结果会很少。
> 正式使用请加 `CHANNELS` / `ENABLED_PLUGINS`，完整推荐列表见 [docker-compose.yml](docker-compose.yml)。

```bash
docker run -d --name pansou -p 8888:8888 \
  -e PORT=8888 \
  -e AUTH_ENABLED=false \
  ghcr.io/fish2018/pansou:latest
```

**2. 启动 DuPanSou-Archive 后端**

```bash
cd backend
pip install -r requirements.txt
python app.py
# 打开 http://localhost:5000
```

> 💡 Windows 用户可直接双击根目录的 **`启动服务.bat`**（在独立最小化窗口中运行，
> 关闭该窗口或双击 `停止服务.bat` 即停止）。注意：直接在某终端里 `python app.py`
> 的话，关闭终端窗口服务就会停止。

仓库已自带构建好的前端（`frontend/dist`），克隆后无需 Node 环境。
若修改了前端代码，重新构建：

```bash
cd frontend
npm install
npm run build   # 产物输出到 frontend/dist，Flask 自动 serve
```

开发模式（热更新，可选）：`npm run dev` 后访问 `http://localhost:3000`。

### 默认只监听本机

出于安全考虑，服务默认绑定 `127.0.0.1` 且关闭调试模式。
局域网/服务器访问请显式开放：

```bash
# Windows CMD
set HOST=0.0.0.0
python app.py

# Linux / macOS
HOST=0.0.0.0 python app.py
```

> 🔐 若 PanSou 开启了认证（`AUTH_ENABLED=true`），设置环境变量
> `PANSOU_USERNAME` / `PANSOU_PASSWORD`，后端会自动登录。

---

## 🧠 核心创新：渐进式索引

**搜得越多，索引越强。**

每次搜索 PanSou 的实时结果会**自动写入本地 SQLite 索引库**；索引库随时间积累，
热门资源的第二次搜索直接命中本地（毫秒级返回）。辅以**定时收割器**（用预设热搜词
定期主动搜索入库）和**链接巡检器**（自动标记失效链接），构成完整闭环。

---

## ⚙️ 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `PORT` | 5000 | Web 服务端口 |
| `HOST` | 127.0.0.1 | 监听地址；局域网/Docker 部署设为 `0.0.0.0` |
| `DEBUG` | 0 | 调试模式（有安全风险，勿对外开启） |
| `PANSOU_BASE_URL` | http://localhost:8888 | PanSou 地址 |
| `PANSOU_USERNAME` / `PANSOU_PASSWORD` | (空) | PanSou 认证（可选） |
| `INDEX_DB_PATH` | backend/data/index.db | 索引数据库路径 |
| `HARVEST_KEYWORDS` | 电影,电视剧,... | 定时收割关键词 |
| `HARVEST_INTERVAL_HOURS` | 6 | 收割周期（小时） |
| `CACHE_TTL_SECONDS` | 300 | 搜索结果缓存时长 |
| `SMART_CHECK_ENABLED` | 1 | 智能有效性检测总开关（关闭后仅剩巡检与举报） |
| `HIDE_DEAD_LINKS` | 1 | 搜索结果自动隐藏"确认失效"的资源卡 |
| `DEAD_CONFIRM_STREAK` | 2 | 连续失败多少次才确认失效（防误杀） |
| `VALIDITY_OK_TTL_HOURS` | 72 | 有效结果保鲜期（小时内不重复送检） |
| `VALIDITY_SUSPECT_TTL_MINUTES` | 30 | 疑似失效复检间隔 |
| `VALIDITY_DEAD_TTL_HOURS` | 12 | 失效复查间隔（探测分享恢复） |
| `CHECKER_INTERVAL_HOURS` | 1 | 后台巡检周期（小时） |
| `CHECKER_CYCLE_LIMIT` | 300 | 每轮巡检最多检测条数 |
| `ENABLE_DOUBAN` | 1 | 豆瓣榜单 + 封面匹配总开关 |
| `ENABLE_POSTERS` | 1 | 搜索结果封面图开关（前端也有开关） |

完整列表见 [docs/01-系统设计文档.md](docs/01-系统设计文档.md)。

---

## 📁 目录结构

```
DuPanSou-Archive/
├── backend/          # Python 后端 (Flask + SQLite)
│   ├── indexer/      # ⭐ 历史索引 + 定时收割（核心创新）
│   ├── checker/      # ⭐ 智能链接有效性检测（核心创新）
│   ├── analyzer/     # ⭐ 搜索统计分析
│   ├── search_engine/ # PanSou 适配层
│   └── api/          # REST API
├── frontend/         # Vue 3 前端（dist 已预构建）
├── docs/             # 系统设计 / 创新点 / 使用教程
├── docker-compose.yml
└── LICENSE
```

---

## ⚠️ 免责声明

- 本项目仅供**学习与技术交流**使用，请勿用于商业用途。
- 本项目**不存储、不上传任何资源文件**，仅对公开渠道的网盘分享链接做搜索与索引。
- 所有网盘链接的版权与内容责任由原分享者承担；如链接侵犯您的权益，请自行删除本地索引
  （删除 `backend/data/index.db` 即可重置）。
- 请遵守所在地区法律法规，合理使用。

---

## 📄 许可证

本项目基于 [MIT License](LICENSE)。

**实时搜索引擎**基于 [fish2018/pansou](https://github.com/fish2018/pansou)（MIT 许可证）作为基础设施，感谢原作。
# supansou
