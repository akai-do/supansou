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
- **同资源聚合分组** — 同一资源的多个网盘/发布帖合并为一张资源卡，其余链接一键展开
- **分页浏览全量结果** — 完整资源池按页返回（10/20/50 每页可选），结果缓存 5 分钟，翻页零成本
- **搜索历史** — 本地记录搜索词，点击重搜、单删、清空
- **渐进式索引** — 每次搜索的实时结果自动入库，定时收割器持续扩充，搜得越多索引越强
- **链接巡检** — 定时批量检测存活状态，失效链接自动标记
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

完整列表见 [docs/01-系统设计文档.md](docs/01-系统设计文档.md)。

---

## 📁 目录结构

```
DuPanSou-Archive/
├── backend/          # Python 后端 (Flask + SQLite)
│   ├── indexer/      # ⭐ 历史索引 + 定时收割（核心创新）
│   ├── checker/      # ⭐ 链接存活巡检（核心创新）
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
