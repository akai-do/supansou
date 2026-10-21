# 13 · aliso 1:1 改造：启动、开关与回滚

> 本文件只记录本次改造的运行方式和回滚步骤，不修改 `04` 与 `01~07` 原文。

## 1. 分支与备份

- 工作分支：`feature/aliso-parity`
- 数据库备份：`backend/data/backups/index-<时间戳>.db`
  - 使用 SQLite 在线 backup API，兼容 WAL/SHM，不直接复制主库文件。
- 前端构建产物备份：`backend/data/backups/frontend-dist-<时间戳>.zip`
  - `backend/data/` 已被 `.gitignore` 忽略，备份不会进入版本库。

## 2. Feature flags

代码内默认值（`backend/config.py`），可用环境变量逐项覆盖：

| 变量 | 默认 | 作用 |
|---|---|---|
| `ALISO_UI` | `1` | 新首页/搜索外壳；设为 `0` 时 `/` 回退到 `/resource`，`/search` 回退为旧搜索页 |
| `STREAM_SEARCH` | `0` | 流式搜索（阶段 5 才实现） |
| `DEMAND_ENABLED` | `0` | 需求板（阶段 3 才实现） |
| `ADMIN_V2` | `0` | 新管理后台（阶段 4~5 才实现） |
| `SITE_NOTICE_ENABLED` | `1` | 首页公告总开关 |

**阶段 3/4 新增环境变量**

| 变量 | 默认 | 作用 |
|---|---|---|
| `ADMIN_USERNAME` | `admin` | 默认管理员用户名（仅在没有任何管理员时创建） |
| `ADMIN_PASSWORD` | 空 | 默认管理员初始密码；为空则不创建账号，登录页提示后台未初始化 |
| `ADMIN_SECRET_KEY` | 空 | 后台会话签名密钥；为空时进程内随机生成，重启后需重新登录 |
| `ADMIN_SESSION_HOURS` | `12` | 后台登录有效期 |
| `ADMIN_COOKIE_SECURE` | `0` | HTTPS 部署时设为 `1` |
| `DEMAND_SUBMIT_LIMIT` | `3` | 每个 IP 在窗口内最多提交需求数 |
| `DEMAND_SUBMIT_WINDOW_MINUTES` | `10` | 提交限流窗口（分钟） |
| `DEMAND_VOTE_LIMIT` | `30` | 每个 IP 在窗口内最多投票次数 |
| `DEMAND_VOTE_WINDOW_MINUTES` | `1` | 投票限流窗口（分钟） |

首次启用后台：

```bash
cd backend
ADMIN_V2=1 ADMIN_PASSWORD='请换成一个强密码' python app.py
```

启动后打开 `/admin/login`，用 `admin` + 上面的密码登录；首次登录会被要求修改密码。
密码只保存哈希，不会打印到日志；后台账号与 `ACCEL_TOKEN` 完全分离。

启动示例（本机）：

```bash
cd backend
ALISO_UI=1 STREAM_SEARCH=0 DEMAND_ENABLED=0 ADMIN_V2=0 python app.py
```

Docker 部署时把同名变量加到 `docker-compose.yml` 的 `backend.environment` 即可；本次没有修改该文件，避免覆盖已有未提交改动。

## 3. 数据库迁移

只新增表，不修改旧表：

- `site_settings(key, value, updated_at)`
- `notices(id, content, version, enabled, created_at)`
- `demands(...)`：需求内容、状态、想要数、软删除标记
- `demand_votes(demand_id, voter_key, created_at)`：匿名投票指纹
- `admin_users(...)`：后台账号（密码哈希、首次改密标记）
- `admin_audit_log(...)`：后台操作审计

入口：`backend/site_config.py::ensure_schema()`、`backend/demand_store.py::ensure_schema()`、
`backend/admin_store.py::ensure_schema()`，均可重复调用。
旧表 `link_index`、`search_history`、`click_stats`、`zero_result_watch`、`poster_cache`、`accel_*` 均不删除、不改字段、不改索引。

## 4. 新公开接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/public/settings` | 站点名/公告/页脚/功能开关（仅安全字段） |
| GET | `/api/notice` | 当前公告 |
| GET | `/api/hot-search-words` | 首页热门词 |
| GET | `/api/search-history` | 首页历史兼容接口；服务端不公开原始搜索记录，前端仍读本机 localStorage |
| GET | `/api/resource-titles` | 首页“本地”行词条 |
| GET | `/api/demands` | 历史需求（`enabled=false` 表示总开关关闭） |
| POST | `/api/demand` | 提交需求（校验/敏感词/限流） |
| POST | `/api/demand/{id}/want` | 想要投票（可切换） |
| POST | `/api/auth/login` | 后台登录 |
| GET | `/api/auth/me` | 当前后台用户 |
| POST | `/api/auth/logout` | 后台登出 |
| POST | `/api/auth/change-password` | 修改后台密码 |
| GET | `/api/admin/resources` | 资源列表（分页/筛选） |
| POST | `/api/admin/resources` | 新增/更新资源 |
| POST | `/api/admin/resources/import` | JSON 批量导入（最多 500 条） |
| POST | `/api/admin/resources/batch-delete` | 批量删除（最多 200 条） |
| GET | `/api/admin/demands` | 后台需求列表（含联系方式） |
| PUT | `/api/admin/demands/{id}` | 审核/解决/驳回 |
| DELETE | `/api/admin/demands/{id}` | 软删除需求 |
| GET/PUT | `/api/admin/settings` | 站点名称/公告/页脚/需求板二级开关 |
| GET | `/api/admin/audit` | 最近后台操作 |

## 5. 回滚

| 改动 | 回滚方式 |
|---|---|
| 新首页/外壳 | `ALISO_UI=0`，或前端回退到上一版 `frontend/dist` |
| 新搜索页 | 路由回退到旧 `ResourcePage`；`/resource?kw=` 始终可用 |
| 公告 | `SITE_NOTICE_ENABLED=0` |
| 需求板 | `DEMAND_ENABLED=0`（总闸），或后台“站点设置”里关闭二级开关 |
| 管理后台 | `ADMIN_V2=0`；后台账号/审计表可保留 |
| 新表 | 新表不影响旧表；可直接保留，不必删表 |
| 数据库 | 恢复 `backend/data/backups/index-<时间戳>.db` |
| 前端构建 | 解压 `backend/data/backups/frontend-dist-<时间戳>.zip` 回 `frontend/dist` |
| 代码 | `git switch main`（未提交的本次改动仍保留在工作区） |

## 6. 发版前检查

1. `cd frontend && npm install && npm run build`
2. `cd backend && python -m compileall .`
3. 验证 `/api/health`、`/api/search`、`/api/hot`、`/api/stats`
4. 验证旧 `/resource?kw=` 与 `/douban`、`/accel`、`/guest`、`/booster`、`/stats`
5. 1280px 与 390px 检查 `/` 和 `/search?ai=`
