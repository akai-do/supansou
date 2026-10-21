# aliso.vip 公开接口观察

- 观察日期：2026-10-19
- 观察方式：
  - 公开页面浏览时，浏览器自身发起的公开请求；
  - 对公开只读 GET 接口做少量、低频的直接读取；
  - 公开打包脚本中的请求路径/参数静态阅读。
- 合规边界：
  - 未登录、未注册、未提交需求表单；
  - 未调用任何 `/admin/*` 或需要鉴权的接口；
  - 未进行漏洞测试、未绕过访问限制、未批量抓取；
  - 未记录令牌、Cookie、密码或配置中的敏感值。
- 说明：接口和字段仅用于理解功能，不作为本站产品的数据源。复现时应使用自己的后端、数据源和鉴权体系。

## 1. 观察到的公开请求（运行时）

| 方法 | 路径 | 触发页面 | 请求参数/请求体 | 响应结构（字段级） | 证据 | 置信度 |
|---|---|---|---|---|---|---|
| GET | `/api/public/settings` | 首页、结果页、需求页 | 无 | `{code, message, data:{basic, general, search, seo, notice, template, backup, cleanup, rate_limit, log_retention, auto_blacklist}}` | 浏览器请求 + 直接 GET | 高 |
| GET | `/api/public/netdisk-configs` | 首页、结果页、需求页 | 无 | `{code, message, data:[{id, name, display_name, is_enabled, sort_order, icon, color, enable_transfer, share_password, enable_check, filter_level, expired_type, created_at, updated_at}]}` | 浏览器请求 + 直接 GET | 高 |
| GET | `/api/enabled-check-services` | 首页 | 无 | `{code, message, data:[string]}`，本次 14 项 | 浏览器请求 + 直接 GET | 高 |
| GET | `/api/hot-search-words` | 首页、结果页 | `?_=<timestamp>` 防缓存 | `{code, message, data:[{name, words:[string]}]}`，本次 1 组、17 个词 | 浏览器请求 + 直接 GET | 高 |
| GET | `/api/search-history` | 首页 | 无 | `{code, message, data:[string]}`（从前端赋值推断） | 浏览器请求 | 中高 |
| GET | `/api/resource-titles` | 首页 | 无 | `{code, message, data:[string]}`（从前端赋值推断） | 浏览器请求 | 中高 |
| GET | `/api/notice` | 需求页 | 无 | `{code, message, data:{content}}` | 浏览器请求 + 直接 GET | 高 |
| GET | `/api/search-token` | 结果页 | 无 | `{code, message, data:{token}}` | 浏览器请求 + 脚本 | 高 |
| POST | `/api/stream-search` | 结果页 | JSON：`{kw, src:"all"}`；头：`X-Search-Token`（可选） | 流式响应，按行解析；结果为资源条目 | 页面触发的搜索 + 脚本 | 高（结果）/ 中（分帧细节） |
| GET | `/api/demands` | 需求页 | 无（本次未带分页参数） | `{code, message, data:{list:[...], page, page_size, total}}` | 浏览器请求 + 直接 GET | 高 |
| GET | `/api/demands/wanted` | 需求页 | 无 | `{code, message, data:[id]}`（从前端代码推断） | 浏览器请求 | 中高 |

### 1.1 关键字段说明

`/api/public/netdisk-configs` 的单个网盘配置字段：

- `name`：内部名称（如 `quark`、`baidu`）
- `display_name`：展示名称
- `is_enabled`：是否启用
- `sort_order`：排序
- `icon`、`color`：图标/颜色配置
- `enable_transfer`：是否启用转存
- `share_password`：分享口令/提取码配置（具体值不记录）
- `enable_check`：是否启用有效性检测
- `filter_level`、`expired_type`：过滤/过期策略
- `created_at`、`updated_at`：时间

`/api/hot-search-words` 的结构：

```json
{
  "code": 0,
  "message": "ok",
  "data": [
    { "name": "站内热搜", "words": ["凡人修仙传", "现在就出发", "..."] }
  ]
}
```

`/api/public/settings` 的 `data` 分组：

```text
basic, general, search, seo, notice, template,
backup, cleanup, rate_limit, log_retention, auto_blacklist
```

本次只记录字段名，不展开具体配置值。

## 2. 搜索流程相关（脚本确认，部分未实际触发）

| 方法 | 路径 | 请求参数/请求体 | 响应/行为 | 证据 | 置信度 |
|---|---|---|---|---|---|
| GET | `/api/search-token` | 无 | 返回 token；失败时前端继续无令牌请求 | `Home.js` | 高 |
| POST | `/api/stream-search` | `{kw, src:"all"}`；头 `Content-Type: application/json`、`X-Search-Token` | 流式结果，前端按行解析并追加 | `Home.js` + 页面结果 | 高 |
| POST | `/api/search` | `{kw, src:"all"}`；同上 | 流式失败/无结果时回退，返回完整结果集 | `Home.js` | 高 |
| POST | `/api/public/netdisk-click` | `{disk_type}` | 记录网盘点击；超时 5s | `index-CWn9xag7.js` | 高（脚本） |
| POST | `/api/check-link-validity/batch` | `{items:[...]}` | `{code, message, data:{items:[{url, ...}]}}` | `Home.js` | 高（脚本） |
| POST | `/api/check-link-validity` | 由批量检测逻辑调用（具体 body 未展开） | 链接有效性 | 公开 bundle 端点列表 | 中 |

### 2.1 搜索结果条目（从页面模板推断）

结果分组/条目可能包含：

- `title`：资源标题
- `link`：分享链接
- `code`：提取码
- `disk_type`：网盘类型
- `time`：时间
- `duration`：时长（部分资源）
- `is_valid`：有效性
- `items`：同组链接
- `primary`：主链接
- `alternates`：同名其他链接

以上字段来自 `Home.js` 模板和排序逻辑，未抓取完整响应体，字段集合可能不完整。

## 3. 资源弹窗/转存相关（脚本确认，未实际触发）

| 方法 | 路径 | 请求参数/请求体 | 响应/行为 | 证据 | 置信度 |
|---|---|---|---|---|---|
| POST | `/api/public/netdisk-click` | `{disk_type}` | 点击统计 | `index-CWn9xag7.js` | 高 |
| POST | `/api/netdisk/transfer` | `{url, code, platform, expired_type}` | `{code:0, data:{share_url|target_url, share_code|target_password|pwd}}` | `Home.js` | 高（脚本） |
| POST | `/api/netdisk/check-transfer` | 由前端转存判断调用（具体 body 未展开） | 是否可转存 | 公开 bundle 端点列表 | 中 |
| POST | `/api/netdisk/batch-transfer-upload` | 批量转存上传（管理端） | 未调用 | 公开 bundle 端点列表 | 中 |
| POST | `/api/check-link-validity/batch` | `{items:[...]}` | 批量有效性 | `Home.js` | 高 |

说明：
- 转存分支只有在网盘配置 `enable_transfer=true` 时才可能执行。
- 本次未点击“获取资源”，因此没有触发上述写操作/转存请求。
- 本站如果要做类似能力，应使用自己的网盘账号和授权体系，不能调用对方接口。

## 4. 需求页相关

| 方法 | 路径 | 请求参数/请求体 | 响应结构 | 证据 | 置信度 |
|---|---|---|---|---|---|
| GET | `/api/demands` | 无（支持 `page`/`page_size` 推断） | `{code, message, data:{list, page, page_size, total}}` | 直接 GET | 高 |
| GET | `/api/demands/wanted` | 无 | `{code, message, data:[id]}` | `Demand.js` | 中高 |
| POST | `/api/demand` | `{title, platforms, play_url, remark, contact, is_paid}` | `{code, message, data?}`；成功 `code=0` | `Demand.js` | 高（脚本） |
| POST | `/api/demand/{id}/want` | 无 body | `{code, message, data:{want_count, action:"add"|"remove"}}` | `Demand.js` | 高（脚本） |

需求项字段（从页面模板/脚本推断）：

- `id`
- `title`
- `platforms` 或 `platform`
- `play_url`
- `remark`
- `contact`
- `is_paid`
- `status`（已解决/待处理/已驳回）
- `created_at`
- `want_count`
- `userWanted`（前端合并状态）
- `resolve_note`
- `reject_reason`

## 5. 管理端接口（静态发现，未调用）

> 以下路径来自公开 bundle 的字符串，未登录、未调用。列在这里是为了说明对方后台的功能范围，不作为本站实现依据。

### 5.1 认证与概览

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/auth/login` | 管理端登录 |
| GET | `/api/auth/me` | 当前用户 |
| POST | `/api/auth/change-password` | 修改密码 |
| GET | `/api/dashboard/stats` | 控制台统计 |
| GET | `/api/health` | 健康检查 |
| GET | `/api/config` | 配置读取 |
| PUT | `/api/config` | 配置更新 |

### 5.2 资源与网盘

| 方法 | 路径 | 用途 |
|---|---|---|
| GET/POST | `/api/resources` | 资源列表/新增 |
| POST | `/api/resources/batch-delete` | 批量删除 |
| POST | `/api/resources/delete-filtered` | 按筛选删除 |
| POST | `/api/resources/import` | 资源导入 |
| GET | `/api/resource-types` | 资源类型 |
| GET/POST | `/api/netdisk-configs` | 网盘配置 |
| POST/GET/DELETE | `/api/cloud-accounts` | 网盘账号 |
| POST | `/api/cloud-accounts/test-cookie` | Cookie 测试 |
| POST | `/api/cloud-accounts/folders-temp` | 临时目录 |
| GET/POST | `/api/netdisk/check-transfer`、`/api/netdisk/transfer` | 转存检查/转存 |
| POST | `/api/netdisk/batch-transfer-upload` | 批量转存上传 |

### 5.3 源库、接口与搜索质量

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/api-configs` | API 配置 |
| GET/POST | `/api/source-center`、`/api/source-center/toggle` | 源库管理 |
| POST | `/api/search-ranking/preview` | 搜索排名预览 |
| GET | `/api/search-quality/stats` | 搜索质量统计 |
| POST | `/api/check-link-validity` | 链接有效性检测 |
| GET/POST/PUT/DELETE | `/api/document-sources` | 文档源管理 |
| POST | `/api/document-sources/test` | 文档源测试 |
| PUT | `/api/pansou-config` | PanSou 配置 |
| PUT | `/api/system-settings` | 系统设置 |
| POST | `/api/plugin-configs` | 插件配置 |

### 5.4 频道/账号源

| 方法 | 路径 | 用途 |
|---|---|---|
| GET/POST | `/api/tg-channels` | Telegram 频道 |
| POST | `/api/tg-channels/batch-update-sort` | 批量排序 |
| POST | `/api/tg-channels/import` | 导入 |
| GET | `/api/tg-channels/export` | 导出 |
| GET/POST | `/api/qq-channels` | QQ 频道 |
| GET/POST | `/api/weibo-users` | 微博用户 |
| GET/POST | `/api/tieba-forums` | 贴吧 |
| POST | `/api/tieba-forums/batch-update-sort` | 批量排序 |
| POST | `/api/tieba-forums/import` | 导入 |
| GET/POST | `/api/gying-accounts` | 光鸭账号 |

### 5.5 运维与需求管理

| 方法 | 路径 | 用途 |
|---|---|---|
| PUT | `/api/transfer/auto-cleanup-config` | 转存自动清理 |
| PUT | `/api/logs/retention-config` | 日志保留 |
| PUT | `/api/ip-blacklist/auto-config` | IP 黑名单 |
| PUT | `/api/rate-limit/config` | 限流配置 |
| GET | `/api/admin/demands` | 需求列表 |
| POST | `/api/admin/demands/batch-delete` | 批量删除需求 |
| PUT | `/api/admin/notice` | 公告更新 |
| GET | `/api/notice` | 公告读取（公开） |

## 6. 未观察到/未验证

- `/api/stream-search` 的完整响应体、分帧格式、每条记录的完整字段。
- `/api/search` 回退路径的实际响应（未制造失败）。
- 转存接口的真实请求/响应（未点击“获取资源”）。
- 管理端接口的鉴权方式、权限范围、真实响应（未登录、未调用）。
- 搜索历史保存接口的具体方法/路径（前端脚本中有保存逻辑，未展开确认）。
- 需求分页参数（本次未带 `page`/`page_size`，但响应包含这些字段）。
- 链接有效性检测的完整请求/响应字段。

## 7. 对本站接口设计的启示

1. **公开配置接口按分组返回**：`settings` 分为 basic/general/search/seo/notice/template 等，便于后台统一维护；本站可参考分组思路，但字段和实现独立设计。
2. **搜索前发令牌**：在高频搜索场景下可作为轻量反滥用手段；本站已有访问控制/限流，可按需补。
3. **流式搜索接口**：如果要做渐进式搜索，建议定义清晰的 NDJSON/SSE 协议，并在前端支持取消。
4. **网盘配置驱动 UI**：网盘类型、排序、图标、是否转存、是否检测都由配置返回，前端不硬编码；本站已有 `constants.js`，可逐步配置化。
5. **需求接口保持匿名 + 审核**：如要做需求板，匿名提交、站长审核、想要投票、限流防刷是较低成本的组合。
6. **接口观察不等于接口复用**：所有数据源和写操作都应使用本站自己的后端和授权体系。
