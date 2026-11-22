# GWL /api/v1 用户 API ↔ MCP 工具 字段级清单（v1）

> 本文件的工具表与参数表由 `python scripts/write_inventory.py` 从代码导出，请勿手改。
> 事实来源：`site-back/routes/api_v1.py`、`site-back/utils/api_v1.py`、`site-back/utils/response.py`、`site-back/services/**`。**代码是唯一权威**；与 `doc/API-V1-用户接口.md` 冲突时以代码为准。

## 1. 基址、鉴权与包络

| 项 | 值 | 证据（site-back 代码位置） |
|---|---|---|
| 线上基址 | https://back.gwl.net.cn/api/v1 | 蓝图 url_prefix=/api/v1（app.py:243）；Nginx 反代到本机 :5000 |
| 本地基址 | http://127.0.0.1:5000/api/v1 | app.py:327 |
| 鉴权 | userid + apikey，推荐请求头 `X-User-Id` / `X-Api-Key` | utils/api_v1.py:193-217：头名归一化（大小写/连字符/下划线都认），另接受 `Authorization: ApiKey <key>` 或 `Bearer <key>`（该形式只取 key，不取 userid） |
| 凭据优先级 | 请求头 → query → JSON body 顶层 → form | utils/api_v1.py:221-246 |
| key 形态 | `gk_` + 43 字符（secrets.token_urlsafe(32)）；入库 sha256$前8位$hex | utils/api_v1.py:48-51,104-111 |
| key 长度闸门 | 16~200 | utils/api_v1.py:55-56,443-444 |
| 业务参数 | GET 走 query；写请求放 JSON body 的 `data` 对象（或 body 顶层摊平） | utils/api_v1.py:249-297 |
| 组类身份 | `X-Org-Class` 或 `?class=` / `?_class=`；不属于则静默忽略，不报错也不提权 | utils/api_v1.py:300-324 |
| 权限口径 | 每次请求实时 resolve_now_permission；`now_permission.context` 恒 null（apikey 路径不启用 dashboard 降级） | utils/api_v1.py:305-324 |
| 响应包络 | `{code, msg, data}`；**code 恒等于 HTTP 状态码**，成功 code=200 | utils/response.py:23-34 |
| 请求体上限 | 16MB（超出 413） | config.py:187 |
| 强制 HTTPS | 应用层不强制（无 ProxyFix / HSTS / 301），TLS 在 Nginx 层 | app.py:176-214 |

## 2. 限流与超时（本服务的重试据此设计）

实现是**进程内 60 秒滑动窗口**（utils/decorators.py:24-53），多 worker 各算一份、重启清零，所以下表是单进程口径。可用 `API_V1_RATE_LIMIT_READ/_WRITE/_IP/_AUTH_FAIL` 覆盖（config.py:58-61）。

| 维度 | 桶 | 默认 | 超限返回 |
|---|---|---|---|
| IP（认证前先扣，防拖库） | apiv1:ip:<ip> | 240/分 | 429 + Retry-After + X-RateLimit-* |
| 用户读 | apiv1:read:<uid> | 120/分 | 429 |
| 用户写（POST/PUT/PATCH/DELETE） | apiv1:write:<uid> | 30/分 | 429 |
| 认证失败 | apiv1:authfail:ip 与 :uid 各一份 | 20/分 | 命中即 429 |

本服务重试策略（`gwl_mcp/api.py`）：

| 情况 | 是否重试 | 理由 |
|---|---|---|
| 429 | 任何方法都重试，按 Retry-After（夹在 1~30 秒） | 限流发生在业务执行之前，重放安全 |
| 500/502/503/504 | 只重试 GET | 非 GET 可能已提交：重放会重复建文档，或把已成功的撤回变成 409 假错误 |
| 连接错误/超时 | 只重试 GET，退避 1/2/4…（上限 8 秒 + 抖动） | 同上 |
| 401/403/404/409/413 | 从不重试 | 必须改输入或等人工处理 |
| 总预算 | 单次工具调用不超过 `GWL_TOTAL_TIMEOUT`（默认 60 秒） | 不把 MCP 客户端挂死 |

## 3. 错误码表（v1 可达）

| code | 触发 | 代表文案（上游原文） |
|---|---|---|
| 200 | 成功 | 成功 / pong / 更新成功 / 无变更 / 已移入回收站 / 恢复成功 / 标签已更新 / 已标记已读 |
| 400 | 参数或业务校验 | 组类标识必须是 3 位字母；title 不能为空；该 slug 已被使用；scope 取值无效…；parent_id 必须为整数；该文档不在回收站中；当前正文与该修订一致，无需恢复；标签最多 8 个、每个不超过 24 字；不支持的对象类型；搜索关键词不能为空；role/message/reason 最多 200 个字符 |
| 401 | 缺凭据 / userid 非法 / key 长度非法 / 该账号未配 key / key 不匹配 | 缺少 userid 或 apikey（…）；用户 ID 或 API Key 无效；该账号尚未配置 API Key，请先在仪表盘设置中轮换生成 |
| 403 | 权限不足，或账户被锁定 | 权限不足；账户已被锁定，请 N 分钟后重试；非管理者；在黑名单；组已停用 |
| 404 | 对象/修订/组类/申请不存在；软删文档对非作者也回 404 | 接口不存在（全局 handler）；组类不存在 |
| 409 | 乐观锁冲突（**唯一带 data 的错误体**）；申请已处理；已在黑名单 | data 内含 current_version / expected_version / editor_username / doc_id |
| 413 | 请求体超过 16MB | werkzeug 默认描述 |
| 429 | 任一限流维度超限 | 请求过于频繁，请 N 秒后重试 |
| 428 | 需要版本号（当前不可达：REQUIRE_VERSION_ON_UPDATE=False） | 缺少文档版本号…（编辑锁已启用） |
| 500 | 未捕获异常（不回显堆栈） | 服务器内部错误；组织操作失败，请稍后重试；数据库操作失败 |

## 4. 端点总表（37 条）与工具覆盖

| # | 方法 | 路径 | 用途 | MCP 工具 | api_v1.py 行 |
|---|---|---|---|---|---|
| 1 | GET | `/ping` | 认证自检 + 本进程限流口径 | `gwl_ping` | 112 |
| 2 | GET | `/me` | 本人资料 | `gwl_get_me` | 127 |
| 3 | GET | `/permissions` | 实时权限快照 | `gwl_get_permissions` | 138 |
| 4 | GET | `/org/classes` | 我的组类平铺清单 | `gwl_list_my_classes` | 159 |
| 5 | GET | `/org/orgs` | 我的组织（含可见组） | `gwl_list_my_orgs` | 170 |
| 6 | GET | `/org/classes/<cid>` | 组类详情（仅成员） | `gwl_get_class` | 181 |
| 7 | GET | `/org/joinable` | 可自助加入的 public 组 | `gwl_list_joinable_classes` | 202 |
| 8 | GET | `/org/classes/<cid>/join-info` | 可见性 + 我的加入状态 | `gwl_get_join_info` | 211 |
| 9 | POST | `/org/classes/<cid>/join` | 加入 public / 申请 private | `gwl_join_class` | 219 |
| 10 | GET | `/org/join-requests/mine` | 我提交的申请 | `gwl_list_my_join_requests` | 230 |
| 11 | DELETE | `/org/join-requests/<int:request_id>` | 撤回我的申请 | `gwl_cancel_join_request` | 240 |
| 12 | GET | `/org/classes/<cid>/join-requests` | 组收到的申请（需 manage） | `gwl_list_class_join_requests` | 248 |
| 13 | POST | `/org/join-requests/<int:request_id>/review` | 批准 / 拒绝申请 | `gwl_review_join_request` | 257 |
| 14 | GET | `/org/classes/<cid>/blacklist` | 黑名单（需 manage） | `gwl_list_blacklist` | 268 |
| 15 | POST | `/org/classes/<cid>/blacklist` | 拉黑用户 | `gwl_add_to_blacklist` | 276 |
| 16 | DELETE | `/org/classes/<cid>/blacklist` | 解除拉黑 | `gwl_remove_from_blacklist` | 286 |
| 17 | GET | `/folders` | 文件夹列表（不含文档） | `gwl_list_folders` | 300 |
| 18 | GET | `/documents` | 文档列表（分页，逐条判权） | `gwl_list_documents` | 313 |
| 19 | POST | `/documents` | 创建文档 | `gwl_create_document` | 333 |
| 20 | GET | `/documents/mine` | 我的未删文档 | `gwl_list_my_documents` | 342 |
| 21 | GET | `/documents/trash` | 我的回收站 | `gwl_list_trash` | 352 |
| 22 | GET | `/documents/<slug>` | 按 slug 读全文（view_count+1） | `gwl_get_document` | 361 |
| 23 | GET | `/documents/id/<int:doc_id>` | 按 id 读全文（不计数） | `gwl_get_document_by_id` | 370 |
| 24 | PUT | `/documents/id/<int:doc_id>` | 更新（乐观锁 version / If-Match） | `gwl_update_document` | 379 |
| 25 | DELETE | `/documents/id/<int:doc_id>` | 软删除进回收站 | `gwl_delete_document` | 389 |
| 26 | GET | `/documents/id/<int:doc_id>/revisions` | 修订历史（不含正文） | `gwl_list_document_revisions` | 398 |
| 27 | GET | `/documents/id/<int:doc_id>/revisions/<int:rev>` | 某版修订全文 | `gwl_get_document_revision` | 407 |
| 28 | POST | `/documents/id/<int:doc_id>/revisions/<int:rev>` | 恢复到某版 | `gwl_restore_document_revision` | 416 |
| 29 | POST | `/documents/id/<int:doc_id>/restore` | 从回收站恢复 | `gwl_restore_document` | 427 |
| 30 | DELETE | `/documents/id/<int:doc_id>/permanent` | 物理删除（不可恢复） | （刻意不封装） | 439 |
| 31 | GET | `/search` | 标题搜索（跨 4 类对象） | `gwl_search` | 452 |
| 32 | GET | `/tags/suggest` | 标签联想 / 热门 | `gwl_suggest_tags` | 469 |
| 33 | GET | `/tags/facets` | 标签聚合 | `gwl_get_tag_facets` | 480 |
| 34 | GET | `/tags/<object_type>/<object_id>` | 某对象的标签 | `gwl_get_object_tags` | 491 |
| 35 | PUT | `/tags/<object_type>/<object_id>` | 全量替换对象标签 | `gwl_set_object_tags` | 503 |
| 36 | GET | `/notifications` | 个人 + 全局通知 + 未读数 | `gwl_list_notifications` | 520 |
| 37 | POST | `/notifications/read` | 标记已读 | `gwl_mark_notifications_read` | 542 |
> **36 个工具 = 37 条路由 − 1**：刻意不封装 `DELETE /documents/id/<int:doc_id>/permanent`（物理删行 + 删盘 + 清 tag_links，不可恢复）。要彻底删除请走站点后台，或自行用 apikey 直接调该路由；**没有任何环境变量能打开这条工具**。

## 5. 工具与参数（由代码导出）

### 5.1 速览

| 工具 | 上游端点 | 只读 | 破坏性 | 幂等 |
|---|---|---|---|---|
| 工具 | 上游端点 | 只读 | 破坏性 | 幂等 |
|---|---|---|---|---|
| gwl_ping | `GET /ping` | ✓ |  | ✓ |
| gwl_get_me | `GET /me` | ✓ |  | ✓ |
| gwl_get_permissions | `GET /permissions` | ✓ |  | ✓ |
| gwl_list_my_classes | `GET /org/classes` | ✓ |  | ✓ |
| gwl_list_my_orgs | `GET /org/orgs` | ✓ |  | ✓ |
| gwl_get_class | `GET /org/classes/{cid}` | ✓ |  | ✓ |
| gwl_list_joinable_classes | `GET /org/joinable` | ✓ |  | ✓ |
| gwl_get_join_info | `GET /org/classes/{cid}/join-info` | ✓ |  | ✓ |
| gwl_join_class | `POST /org/classes/{cid}/join` |  |  |  |
| gwl_list_my_join_requests | `GET /org/join-requests/mine` | ✓ |  | ✓ |
| gwl_cancel_join_request | `DELETE /org/join-requests/{request_id}` |  | ✓ | ✓ |
| gwl_list_class_join_requests | `GET /org/classes/{cid}/join-requests` | ✓ |  | ✓ |
| gwl_review_join_request | `POST /org/join-requests/{request_id}/review` |  |  |  |
| gwl_list_blacklist | `GET /org/classes/{cid}/blacklist` | ✓ |  | ✓ |
| gwl_add_to_blacklist | `POST /org/classes/{cid}/blacklist` |  |  |  |
| gwl_remove_from_blacklist | `DELETE /org/classes/{cid}/blacklist` |  |  | ✓ |
| gwl_list_folders | `GET /folders` | ✓ |  | ✓ |
| gwl_list_documents | `GET /documents` | ✓ |  | ✓ |
| gwl_create_document | `POST /documents` |  |  |  |
| gwl_list_my_documents | `GET /documents/mine` | ✓ |  | ✓ |
| gwl_list_trash | `GET /documents/trash` | ✓ |  | ✓ |
| gwl_get_document | `GET /documents/{slug}` | ✓ |  |  |
| gwl_get_document_by_id | `GET /documents/id/{doc_id}` | ✓ |  | ✓ |
| gwl_update_document | `PUT /documents/id/{doc_id}` |  |  | ✓ |
| gwl_delete_document | `DELETE /documents/id/{doc_id}` |  | ✓ | ✓ |
| gwl_list_document_revisions | `GET /documents/id/{doc_id}/revisions` | ✓ |  | ✓ |
| gwl_get_document_revision | `GET /documents/id/{doc_id}/revisions/{rev}` | ✓ |  | ✓ |
| gwl_restore_document_revision | `POST /documents/id/{doc_id}/revisions/{rev}` |  |  |  |
| gwl_restore_document | `POST /documents/id/{doc_id}/restore` |  |  |  |
| gwl_search | `GET /search` | ✓ |  | ✓ |
| gwl_suggest_tags | `GET /tags/suggest` | ✓ |  | ✓ |
| gwl_get_tag_facets | `GET /tags/facets` | ✓ |  | ✓ |
| gwl_get_object_tags | `GET /tags/{object_type}/{object_id}` | ✓ |  | ✓ |
| gwl_set_object_tags | `PUT /tags/{object_type}/{object_id}` |  |  | ✓ |
| gwl_list_notifications | `GET /notifications` | ✓ |  | ✓ |
| gwl_mark_notifications_read | `POST /notifications/read` |  |  |  |
### 5.2 逐个工具的完整参数表

说明：类型/约束列直接来自官方 SDK 依据函数签名生成的 JSON Schema（`tools/list` 下发的就是它）；未知参数会被拒绝而不是静默忽略；`None` 不会下发给上游；所有工具都不接受 userid/apikey 参数（凭据只来自环境变量）。

#### gwl_ping — 连通性与凭据自检

上游：**`GET /ping`**；readOnlyHint=True，destructiveHint=False，idempotentHint=True。

描述：验证当前 userid + apikey 是否可用，并返回身份信息与本进程生效的限流口径。 凭据（GWL_USER_ID / GWL_API_KEY）来自环境变量，工具参数里不需要也不能传 userid/apikey。 建议每次会话开始时先调用一次：它同时告诉你上游的读/写/IP 配额，便于安排后续调用节奏。返回 data 含 userid、username、real_level（1~5）、server_time（UTC ISO）、rate_limit{read_per_minute,write_per_minute,ip_per_minute,auth_fail_per_minute}。

参数：无。

#### gwl_get_me — 我的资料

上游：**`GET /me`**；readOnlyHint=True，destructiveHint=False，idempotentHint=True。

描述：读取当前 API Key 所属用户的个人资料（与前端「个人中心」同源）。 返回 id、username、nickname、display_name、email、phone、avatar、theme、introduction、gender、location、group_id/group_name、slug、is_public、workspace_layout 等。 出于安全，永远不含 account_apikey、password_hash、pin；要拿权限等级请用 gwl_get_permissions。

参数：无。

#### gwl_get_permissions — 实时权限快照

上游：**`GET /permissions`**；readOnlyHint=True，destructiveHint=False，idempotentHint=True。

描述：返回本次请求实时解析出的权限（服务端每次请求重算，不信任任何客户端声明）。 字段：userid、real_level、level、context（apikey 路径恒为 null）、nodes（5 段权限节点串列表）、class_id 与 class_role（当前组类身份）。 想以某个组类身份操作，用环境变量 GWL_ORG_CLASS（或请求级 X-Org-Class 语义），不属于该组类时会被静默忽略。

参数：无。

#### gwl_list_my_classes — 我的组类清单

上游：**`GET /org/classes`**；readOnlyHint=True，destructiveHint=False，idempotentHint=True。

描述：平铺返回我加入或拥有的所有组类（组织 + 组），是找 cid 的入口。 每项含 id（3 位字母）、name、kind（organization/group）、org_id、owner、role、is_active、is_public、node、can_manage。 另带 status{tables_ready, missing, hint}：组织数据表未就绪时 hint 会说明原因。

参数：无。

#### gwl_list_my_orgs — 我的组织树

上游：**`GET /org/orgs`**；readOnlyHint=True，destructiveHint=False，idempotentHint=True。

描述：返回我加入/拥有的组织，组织为顶层、下辖我可见的组（groups 数组）。 适合先看清层级关系，再决定用哪个 cid 去调用组类详情或黑名单类工具。

参数：无。

#### gwl_get_class — 组类详情

上游：**`GET /org/classes/{cid}`**；readOnlyHint=True，destructiveHint=False，idempotentHint=True。

描述：读取某个组类的详细信息：id、name、kind、org_id、owner、role、can_manage、permissions[]、permission_map。 仅成员、owner、上级组织管理方或超管可见；不是成员 403，不存在 404，cid 不是 3 位字母 400。

| 参数 | 类型 | 必填 | 默认 | 约束与说明 |
|---|---|---|---|---|
| cid | str | 是 | — | 组类标识（路径参数）。；pattern=^[A-Za-z]{3}$ |

#### gwl_list_joinable_classes — 可加入的公开组

上游：**`GET /org/joinable`**；readOnlyHint=True，destructiveHint=False，idempotentHint=True。

描述：列出所有允许自助加入的 public 组（返回裸数组，上限 200 条）。 class_id 可收窄到某个组织下的组；传了不属于任何组织的 id 会 400。

| 参数 | 类型 | 必填 | 默认 | 约束与说明 |
|---|---|---|---|---|
| class_id | str 或 null | 否 | null | 可选：只看某个组织/组类下的可加入组（3 位字母）。省略=全部公开组。；pattern=^[A-Za-z]{3}$ |

#### gwl_get_join_info — 组的加入状态

上游：**`GET /org/classes/{cid}/join-info`**；readOnlyHint=True，destructiveHint=False，idempotentHint=True。

描述：查询某组类的可见性与「我」的状态：is_public、joinable、is_member、blacklisted、request（我提交的待处理申请，没有则 null）。 这条不需要组类身份，任何人都能先探测再决定要不要申请加入。

| 参数 | 类型 | 必填 | 默认 | 约束与说明 |
|---|---|---|---|---|
| cid | str | 是 | — | 目标组类标识（路径参数）。；pattern=^[A-Za-z]{3}$ |

#### gwl_join_class — 加入组类

上游：**`POST /org/classes/{cid}/join`**；readOnlyHint=False，destructiveHint=False，idempotentHint=False。

描述：加入组类：public 组直接入组（data.mode=joined），private 组转为提交申请（data.mode=requested）。 已经是成员不会报错，会回 already_member=true（幂等）。 失败语义：组已停用 / 非公开且不允许申请 / 我在黑名单 403；cid 非法 400。message 最多 200 个字符。

| 参数 | 类型 | 必填 | 默认 | 约束与说明 |
|---|---|---|---|---|
| cid | str | 是 | — | 要加入的组类标识（路径参数）。；pattern=^[A-Za-z]{3}$ |
| role | str | 否 | "member" | 入组角色名：字母开头，可含数字与下划线，1~32 个字符。默认 member。；pattern=^[A-Za-z][A-Za-z0-9_]{0,31}$ |
| message | str | 否 | "" | 申请附言（private 组会给审批人看），最多 200 个字符。；长度≤200 |

#### gwl_list_my_join_requests — 我提交的申请

上游：**`GET /org/join-requests/mine`**；readOnlyHint=True，destructiveHint=False，idempotentHint=True。

描述：列出我提交过的加入申请（裸数组）。默认只返回待处理（pending）的； handled=true 时连已批准/已拒绝/已撤回的一起返回。 每项含 group、requester_username、reviewer_username、status、already_member。

| 参数 | 类型 | 必填 | 默认 | 约束与说明 |
|---|---|---|---|---|
| handled | bool | 否 | false | true=同时返回已处理的申请；默认 false 只看待处理。 |

#### gwl_cancel_join_request — 撤回我的申请

上游：**`DELETE /org/join-requests/{request_id}`**；readOnlyHint=False，destructiveHint=True，idempotentHint=True。

描述：撤回我自己提交的加入申请，状态变为 cancelled。 只能撤自己的（否则 403）；已被批准或拒绝过的再撤回会 409。 先用 gwl_list_my_join_requests 取 request_id。

| 参数 | 类型 | 必填 | 默认 | 约束与说明 |
|---|---|---|---|---|
| request_id | int | 是 | — | 申请 id（路径参数）。；≥1 |

#### gwl_list_class_join_requests — 组收到的申请

上游：**`GET /org/classes/{cid}/join-requests`**；readOnlyHint=True，destructiveHint=False，idempotentHint=True。

描述：列出某组类收到的加入申请（裸数组，需管理权限）。 传组织 id 会汇总其下辖组的申请。status 默认 pending，可取 all / approved / rejected。需要该组类的管理角色（class_role=owner 或权限含 manage），否则 403。

| 参数 | 类型 | 必填 | 默认 | 约束与说明 |
|---|---|---|---|---|
| cid | str | 是 | — | 组类或组织标识（路径参数）。；pattern=^[A-Za-z]{3}$ |
| status | str（枚举：pending/all/approved/rejected） | 否 | "pending" | 筛选申请状态。默认 pending（待处理）；all 返回全部历史。 |

#### gwl_review_join_request — 审批加入申请

上游：**`POST /org/join-requests/{request_id}/review`**；readOnlyHint=False，destructiveHint=False，idempotentHint=False。

描述：批准或拒绝一条加入申请：approve=true 批准入组，false 拒绝。 批准只是建立成员关系，不会因此提权。 已处理过的再审批 409；目标用户在黑名单里 409；申请不存在 404。需要该组类的管理角色（class_role=owner 或权限含 manage），否则 403。

| 参数 | 类型 | 必填 | 默认 | 约束与说明 |
|---|---|---|---|---|
| request_id | int | 是 | — | 申请 id（路径参数）。；≥1 |
| approve | bool | 否 | true | true=批准入组，false=拒绝。默认 true。 |
| note | str | 否 | "" | 备注 / 附言（最多 200 个字符，上游超限返回 400）。；长度≤200 |

#### gwl_list_blacklist — 组类黑名单

上游：**`GET /org/classes/{cid}/blacklist`**；readOnlyHint=True，destructiveHint=False，idempotentHint=True。

描述：列出该组类的黑名单（裸数组：user_id、username、reason、created_by、created_at）。 传组织 id 时汇总下辖组。需要该组类的管理角色（class_role=owner 或权限含 manage），否则 403。

| 参数 | 类型 | 必填 | 默认 | 约束与说明 |
|---|---|---|---|---|
| cid | str | 是 | — | 组类或组织标识（路径参数）。；pattern=^[A-Za-z]{3}$ |

#### gwl_add_to_blacklist — 拉黑用户

上游：**`POST /org/classes/{cid}/blacklist`**；readOnlyHint=False，destructiveHint=False，idempotentHint=False。

描述：把一个用户加入组类黑名单（会同时给对方发一条站内通知）。 拉黑自己 400；已在名单里 409。需要该组类的管理角色（class_role=owner 或权限含 manage），否则 403。

| 参数 | 类型 | 必填 | 默认 | 约束与说明 |
|---|---|---|---|---|
| cid | str | 是 | — | 组类标识（路径参数）。；pattern=^[A-Za-z]{3}$ |
| username | str | 是 | — | 要拉黑的用户登录名。；长度≥1；长度≤64 |
| reason | str | 否 | "" | 备注 / 附言（最多 200 个字符，上游超限返回 400）。；长度≤200 |

#### gwl_remove_from_blacklist — 解除拉黑

上游：**`DELETE /org/classes/{cid}/blacklist`**；readOnlyHint=False，destructiveHint=False，idempotentHint=True。

描述：把一个用户移出组类黑名单（同样会通知对方）。 该用户不在名单里时返回 404；重复调用不幂等。需要该组类的管理角色（class_role=owner 或权限含 manage），否则 403。

| 参数 | 类型 | 必填 | 默认 | 约束与说明 |
|---|---|---|---|---|
| cid | str | 是 | — | 组类标识（路径参数）。；pattern=^[A-Za-z]{3}$ |
| username | str | 是 | — | 要解除拉黑的用户登录名。；长度≥1；长度≤64 |

#### gwl_list_folders — 文件夹列表

上游：**`GET /folders`**；readOnlyHint=True，destructiveHint=False，idempotentHint=True。

描述：只列文件夹（不含文档），返回裸数组：id、name、scope、user_id、group_name、parent_id、sort_order、is_active、created_at、updated_at；group 范围的项还带 class_id/org_id/group_root。 scope 省略时返回我全部可见的文件夹；parent_id=0 只取根级，正整数只取该文件夹的直接子级，省略则返回全部层级。

| 参数 | 类型 | 必填 | 默认 | 约束与说明 |
|---|---|---|---|---|
| scope | str 或 null | 否 | null | 文档范围；省略=我可见的全部范围。；枚举：public/group/private |
| parent_id | int 或 null | 否 | null | 父文件夹 id：0=根级，正整数=该文件夹的直接子级，省略=全部层级。；父文件夹 id。0=只取根级，正整数=只取该文件夹的直接子级，省略=返回全部层级。；≥0 |

#### gwl_list_documents — 文档列表（分页）

上游：**`GET /documents`**；readOnlyHint=True，destructiveHint=False，idempotentHint=True。

描述：按范围列出文档元信息（不含正文），逐条判权后分页。 返回 {items,page,size,total,has_more}；items 是 meta 视图，取正文用 gwl_get_document_by_id（不加浏览数）或 gwl_get_document（会 +1 浏览数）。 folder_id：0=根目录（默认）、-1=该范围下全部文件夹、正整数=指定文件夹。 deleted=1 只看回收站里的（需相应权限）；author_id 只在 scope=public 生效。 tags 最多 8 个，tag_mode=all 表示同时命中，any 表示任一命中。

| 参数 | 类型 | 必填 | 默认 | 约束与说明 |
|---|---|---|---|---|
| scope | str（枚举：public/group/private） | 否 | "public" | 文档范围，默认 public。 |
| folder_id | int | 否 | 0 | 文件夹过滤：0=根目录，-1=全部文件夹，正整数=指定文件夹。；≥-1 |
| deleted | int 或 null | 否 | null | 0=只看未删除（默认），1=只看已软删除。非法值由上游回落。；≥0；≤1 |
| page | int | 否 | 1 | 页码，从 1 开始。非法值由上游回落到 1，不报错。；≥1 |
| size | int | 否 | 20 | 每页条数，1~100；超过 100 上游按 100 处理。；≥1；≤100 |
| tags | list 或 null | 否 | null | 标签过滤（最多 8 个），上游按逗号解析。；标签列表，最多 8 个（上游限制）。；元素≤8 |
| tag_mode | str 或 null | 否 | null | 标签命中方式：all=同时具备，any=任一具备。；枚举：all/any |
| author_id | int 或 null | 否 | null | 只看某作者的文档；仅 scope=public 生效。；用户 id（正整数）。；≥1 |

#### gwl_create_document — 创建文档

上游：**`POST /documents`**；readOnlyHint=False，destructiveHint=False，idempotentHint=False。

描述：创建一篇文档，返回文档详情（含 content，version=1）。 title 必填非空；slug 省略时上游按毫秒时间戳自动生成，已占用则 400。 写入范围（visibility/owning/folder_id/permission_bits）由服务端做写边界校验，越界返回 403；folder_id 不存在返回 400。 本工具不幂等：网络失败时不要盲目重试，先用 gwl_list_my_documents 确认是否已经建过。

| 参数 | 类型 | 必填 | 默认 | 约束与说明 |
|---|---|---|---|---|
| title | str | 是 | — | 标题（1~200 个字符）。；长度≥1；长度≤200 |
| content | str | 否 | "" | 文档正文（Markdown）。 |
| slug | str 或 null | 否 | null | 自定义 slug（全站唯一）。省略则自动生成。；长度≥1；长度≤200 |
| summary | str 或 null | 否 | null | 摘要；超过 500 个字符会被上游截断。；长度≤500 |
| icon | str 或 null | 否 | null | 图标（emoji 或短串）；超过 50 个字符会被上游截断。；长度≤50 |
| folder_id | int 或 null | 否 | null | 归属文件夹 id。省略=根目录（0）。；≥0 |
| permission_bits | str 或 null | 否 | null | 6 位 0/1 权限位串，与站点文档权限节点一致；不确定就先读回原值再改。；pattern=^[01]{6}$ |
| visibility | str 或 null | 否 | null | 文档可见性：public=全站（仍受权限位约束），group=组内，private=仅归属者。；枚举：public/group/private |
| owning | str 或 null | 否 | null | 归属：visibility=public 时可省略；group=3 位组类标识；private=用户 id 字符串。写入受服务端写边界校验，越界返回 403。；长度≤50 |

#### gwl_list_my_documents — 我的文档

上游：**`GET /documents/mine`**；readOnlyHint=True，destructiveHint=False，idempotentHint=True。

描述：列出我自己的未删除文档（分页包络）。 folder_id 默认 0（只看根目录）；要一次看全部请传 -1。

| 参数 | 类型 | 必填 | 默认 | 约束与说明 |
|---|---|---|---|---|
| folder_id | int | 否 | 0 | 0=根目录（默认），-1=全部文件夹，正整数=指定文件夹。；≥-1 |
| page | int | 否 | 1 | 页码，从 1 开始。非法值由上游回落到 1，不报错。；≥1 |
| size | int | 否 | 20 | 每页条数，1~100；超过 100 上游按 100 处理。；≥1；≤100 |

#### gwl_list_trash — 我的回收站

上游：**`GET /documents/trash`**；readOnlyHint=True，destructiveHint=False，idempotentHint=True。

描述：列出被我软删除的文档（分页包络，按删除时间倒序）。 配合 gwl_restore_document 恢复；永久删除不提供工具。

| 参数 | 类型 | 必填 | 默认 | 约束与说明 |
|---|---|---|---|---|
| page | int | 否 | 1 | 页码，从 1 开始。非法值由上游回落到 1，不报错。；≥1 |
| size | int | 否 | 20 | 每页条数，1~100；超过 100 上游按 100 处理。；≥1；≤100 |

#### gwl_get_document — 按 slug 读文档

上游：**`GET /documents/{slug}`**；readOnlyHint=True，destructiveHint=False，idempotentHint=False。

描述：按 slug 读取文档全文（返回字段随可见性做权限位掩码）。 ⚠️ 有副作用：每次命中都会 view_count + 1，因此标记为非幂等。 只是要读内容做编辑时，请改用 gwl_get_document_by_id（不加浏览数）。 不存在 404；无权查看 403。

| 参数 | 类型 | 必填 | 默认 | 约束与说明 |
|---|---|---|---|---|
| slug | str | 是 | — | 文档 slug（路径参数，会自动做 URL 编码）。；长度≥1；长度≤200 |

#### gwl_get_document_by_id — 按 id 读文档

上游：**`GET /documents/id/{doc_id}`**；readOnlyHint=True，destructiveHint=False，idempotentHint=True。

描述：按文档 id 读取全文，不增加浏览数，是编辑前的推荐读法。 返回里的 version 就是乐观锁基线，更新时原样带回 data.version。 软删除的文档只有作者或超管能看到，其他人 404。

| 参数 | 类型 | 必填 | 默认 | 约束与说明 |
|---|---|---|---|---|
| doc_id | int | 是 | — | 文档 id（路径参数）。；≥1 |

#### gwl_update_document — 更新文档（乐观锁）

上游：**`PUT /documents/id/{doc_id}`**；readOnlyHint=False，destructiveHint=False，idempotentHint=True。

描述：更新文档：至少要提供一个可变字段（title/content/summary/icon/folder_id/permission_bits/visibility/owning），否则本工具直接报参数错误而不发请求。 并发安全：强烈建议先 gwl_get_document_by_id 拿到 version 并原样传入；版本不符返回 409，且 data 带 current_version / expected_version / editor_username。 base_content 表示「你准备改动的那份原文」，必须与 content 同时提供，服务端据此自动合并他人改动。 重复提交同样内容返回 msg=无变更（幂等）。revision_summary 是这次修订的说明。

| 参数 | 类型 | 必填 | 默认 | 约束与说明 |
|---|---|---|---|---|
| doc_id | int | 是 | — | 文档 id（路径参数）。；≥1 |
| title | str 或 null | 否 | null | 标题（1~200 个字符）。；长度≥1；长度≤200 |
| content | str 或 null | 否 | null | 文档正文（Markdown）。 |
| summary | str 或 null | 否 | null | 摘要；超过 500 个字符会被上游截断。；长度≤500 |
| icon | str 或 null | 否 | null | 图标（emoji 或短串）；超过 50 个字符会被上游截断。；长度≤50 |
| folder_id | int 或 null | 否 | null | 移动到指定文件夹（0=根目录）。；≥0 |
| permission_bits | str 或 null | 否 | null | 6 位 0/1 权限位串，与站点文档权限节点一致；不确定就先读回原值再改。；pattern=^[01]{6}$ |
| visibility | str 或 null | 否 | null | 文档可见性：public=全站（仍受权限位约束），group=组内，private=仅归属者。；枚举：public/group/private |
| owning | str 或 null | 否 | null | 归属：visibility=public 时可省略；group=3 位组类标识；private=用户 id 字符串。写入受服务端写边界校验，越界返回 403。；长度≤50 |
| revision_summary | str 或 null | 否 | null | 本次修订说明（写进修订历史），最多 200 个字符。；长度≤200 |
| version | int 或 null | 否 | null | 乐观锁基线版本；来自 gwl_get_document_by_id 的 version。省略=不做版本校验。；≥1 |
| base_content | str 或 null | 否 | null | 准备改动的原文基线；必须与 content 同时提供。 |

#### gwl_delete_document — 删除文档（软删除）

上游：**`DELETE /documents/id/{doc_id}`**；readOnlyHint=False，destructiveHint=True，idempotentHint=True。

描述：把文档移入回收站（软删除，可用 gwl_restore_document 恢复）。 仅作者或被授权者，否则 403；已经是回收站状态则 404。 不可恢复的永久删除接口本服务刻意不提供。

| 参数 | 类型 | 必填 | 默认 | 约束与说明 |
|---|---|---|---|---|
| doc_id | int | 是 | — | 文档 id（路径参数）。；≥1 |

#### gwl_list_document_revisions — 修订历史

上游：**`GET /documents/id/{doc_id}/revisions`**；readOnlyHint=True，destructiveHint=False，idempotentHint=True。

描述：列出文档的修订历史（裸数组：revision_num、editor_username、summary、has_content、created_at）。 不含正文；取某一版全文用 gwl_get_document_revision。

| 参数 | 类型 | 必填 | 默认 | 约束与说明 |
|---|---|---|---|---|
| doc_id | int | 是 | — | 文档 id（路径参数）。；≥1 |

#### gwl_get_document_revision — 某版修订全文

上游：**`GET /documents/id/{doc_id}/revisions/{rev}`**；readOnlyHint=True，destructiveHint=False，idempotentHint=True。

描述：读取某一版修订的全文，并带 current_version 与 can_restore。 该修订不存在 404；没有内容快照 404。

| 参数 | 类型 | 必填 | 默认 | 约束与说明 |
|---|---|---|---|---|
| doc_id | int | 是 | — | 文档 id（路径参数）。；≥1 |
| rev | int | 是 | — | 修订号（路径参数）。；≥1 |

#### gwl_restore_document_revision — 恢复到某版

上游：**`POST /documents/id/{doc_id}/revisions/{rev}`**；readOnlyHint=False，destructiveHint=False，idempotentHint=False。

描述：把文档正文恢复到某一版修订（会新写一条修订记录，因此本身可逆）。 需要可编辑权限（403），同样受乐观锁保护：建议带 version。 当前正文已经与该版一致时返回 400（重复调用不幂等）。

| 参数 | 类型 | 必填 | 默认 | 约束与说明 |
|---|---|---|---|---|
| doc_id | int | 是 | — | 文档 id（路径参数）。；≥1 |
| rev | int | 是 | — | 要恢复到的修订号（路径参数）。；≥1 |
| version | int 或 null | 否 | null | 乐观锁基线版本；省略=不做版本校验。；≥1 |

#### gwl_restore_document — 从回收站恢复

上游：**`POST /documents/id/{doc_id}/restore`**；readOnlyHint=False，destructiveHint=False，idempotentHint=False。

描述：把软删除的文档从回收站恢复回来。 文档不在回收站 400；无权恢复 403。恢复后返回文档详情（msg=恢复成功）。

| 参数 | 类型 | 必填 | 默认 | 约束与说明 |
|---|---|---|---|---|
| doc_id | int | 是 | — | 文档 id（路径参数）。；≥1 |

#### gwl_search — 标题搜索

上游：**`GET /search`**；readOnlyHint=True，destructiveHint=False，idempotentHint=True。

描述：按标题跨对象搜索（文档 / 组织 / 组 / 发现条目），逐条判权后分页返回。 data 里除分页包络外还有 q、types[]、counts{document,organization,group,discover}，items 每项含 type、id、title、summary、icon、url、matched 与范围字段。 types 可多值；传 discover_item 会被映射成上游的 discover。 q 必填且不超过 64 个字符，否则 400。命中量超过扫描上限时返回 scan_truncated=true。

| 参数 | 类型 | 必填 | 默认 | 约束与说明 |
|---|---|---|---|---|
| q | str | 是 | — | 标题关键词（1~64 个字符）。；长度≥1；长度≤64 |
| types | list 或 null | 否 | null | 限定搜索对象类型；省略=全部。discover_item 会自动映射为上游的 discover。；元素≤5 |
| scope | str 或 null | 否 | null | 文档范围过滤（仅对文档类结果生效）。；枚举：public/group/private |
| tags | list 或 null | 否 | null | 标签过滤（最多 8 个）。；标签列表，最多 8 个（上游限制）。；元素≤8 |
| tag_mode | str 或 null | 否 | null | 标签命中方式：all=同时具备，any=任一具备。；枚举：all/any |
| page | int | 否 | 1 | 页码，从 1 开始。非法值由上游回落到 1，不报错。；≥1 |
| size | int | 否 | 20 | 每页条数，1~100；超过 100 上游按 100 处理。；≥1；≤100 |

#### gwl_suggest_tags — 标签联想

上游：**`GET /tags/suggest`**；readOnlyHint=True，destructiveHint=False，idempotentHint=True。

描述：标签输入联想；q 省略或为空时返回热门标签。 返回 {q, items[{id,name,count}]}，count 是该标签被使用的次数。

| 参数 | 类型 | 必填 | 默认 | 约束与说明 |
|---|---|---|---|---|
| q | str 或 null | 否 | null | 联想前缀（最多 64 个字符）。省略=取热门标签。；长度≤64 |
| limit | int | 否 | 10 | 返回标签数上限（1~50），默认 10。；≥1；≤50 |

#### gwl_get_tag_facets — 标签聚合

上游：**`GET /tags/facets`**；readOnlyHint=True，destructiveHint=False，idempotentHint=True。

描述：按对象类型聚合标签用量，返回 {object_type, scope, items[{id,name,count}]}。 object_type 必填；scope 只对 document 生效；不支持的类型返回 400。

| 参数 | 类型 | 必填 | 默认 | 约束与说明 |
|---|---|---|---|---|
| object_type | str（枚举：document/organization/group/discover_item） | 是 | — | 要聚合的对象类型。 |
| scope | str 或 null | 否 | null | 文档范围过滤（仅 object_type=document 生效）。；枚举：public/group/private |
| limit | int | 否 | 50 | 返回聚合项上限（1~200），默认 50。；≥1；≤200 |

#### gwl_get_object_tags — 读对象标签

上游：**`GET /tags/{object_type}/{object_id}`**；readOnlyHint=True，destructiveHint=False，idempotentHint=True。

描述：读取某个对象当前的标签（裸数组 [{id,name}]，按名称排序）。 能看到对象就能看到标签；看不到对象 404 / 403。 object_id 用字符串：文档传 doc_id，组织/组传 3 位 cid，发现条目传条目 id。

| 参数 | 类型 | 必填 | 默认 | 约束与说明 |
|---|---|---|---|---|
| object_type | str（枚举：document/organization/group/discover_item） | 是 | — | 对象类型。 |
| object_id | str | 是 | — | 对象 id（字符串形式）。；pattern=^[A-Za-z0-9_-]{1,64}$ |

#### gwl_set_object_tags — 全量替换对象标签

上游：**`PUT /tags/{object_type}/{object_id}`**；readOnlyHint=False，destructiveHint=False，idempotentHint=True。

描述：用给定列表整体替换该对象的标签（不是追加）。 tags=[] 表示清空；超过 8 个或单个超过 24 字返回 400；无权编辑该对象返回 403。 建议先 gwl_get_object_tags 读回现有标签再合并提交，避免误删他人的标签。

| 参数 | 类型 | 必填 | 默认 | 约束与说明 |
|---|---|---|---|---|
| object_type | str（枚举：document/organization/group/discover_item） | 是 | — | 对象类型。 |
| object_id | str | 是 | — | 对象 id（字符串形式）。；pattern=^[A-Za-z0-9_-]{1,64}$ |
| tags | list of str | 是 | — | 新的完整标签列表（空数组=清空，最多 8 个）。；元素≤8 |

#### gwl_list_notifications — 我的通知

上游：**`GET /notifications`**；readOnlyHint=True，destructiveHint=False，idempotentHint=True。

描述：返回我的站内通知 + 我当前可见的全局通知 + 未读计数。 结构：{personal[], global[]（带 is_read）, unread{total,global,personal}}。 这不是分页包络，用 limit 截断（默认 100，最大 200）。

| 参数 | 类型 | 必填 | 默认 | 约束与说明 |
|---|---|---|---|---|
| limit | int | 否 | 100 | 返回条数上限（1~200），默认 100。；≥1；≤200 |

#### gwl_mark_notifications_read — 标记通知已读

上游：**`POST /notifications/read`**；readOnlyHint=False，destructiveHint=False，idempotentHint=False。

描述：把指定通知标记为已读，返回新的未读计数 {unread{total,global,personal}}。 global_ids 与 personal_ids 至少要给一个且不能是空列表，否则本工具直接报错。 只能标记自己的通知；别人的 id 会被上游静默忽略。操作幂等（重复调用结果一致）。

| 参数 | 类型 | 必填 | 默认 | 约束与说明 |
|---|---|---|---|---|
| global_ids | list 或 null | 否 | null | 要标记已读的全局通知 id（最多 500 个，超出上游会截断）。；元素≤500 |
| personal_ids | list 或 null | 否 | null | 要标记已读的个人通知 id（最多 500 个，超出上游会截断）。；元素≤500 |

## 6. 与 v0 的口径差异（有意为之，不是回归）

| 项 | v0 | v1 | 原因 |
|---|---|---|---|
| 协议实现 | 手写 JSON-RPC（零依赖） | 官方 SDK `mcp` 2.x | 会话/SSE/CORS/Host 校验/协议协商交给 SDK |
| 参数校验 | 自研 JSON Schema 子集校验器 | SDK（pydantic）按生成的 schema 校验；本服务再补未知参数、跨字段约束、只读开关 | 消除 v0 顶层 nullable 形同虚设的漏洞 |
| 可选参数类型 | `type: ["integer","null"]` 并集 | `anyOf` | 并集写法在部分客户端/模型上被拒 |
| resources | 无（`resources/list` 回空数组但 `resources/read` 报 -32601，自相矛盾） | 7 个（5 静态 + 2 模板）并声明 capabilities | 客户端可直接浏览端点对照表与概览 |
| 结果结构 | 只有 content[0].text 的 JSON 字符串 | 同时给 structuredContent + outputSchema | 模型不必二次解析文本 |
| MCP logging | 不发 logging/message（`logging/setLevel` 是空壳） | 同样不发：该能力在协议 2026-07-28 已标记废弃（SEP-2577）；日志统一 stderr | 避免 deprecated 警告与两套日志 |
| note/message/reason 上限 | 500 | **200** | 上游 services/org_join_service.py:65 MAX_NOTE=200；v0 的 500 会让模型写出 400 |
| object_id 类型 | `["string","integer"]` 且 pattern 对整数无效 | 一律 str + pattern | union 类型易被客户端拒；上游只按字符串取路径段 |
| 永久删除开关 | 无（硬编码不封装，并有测试锁死） | 同样硬编码不封装（测试继续锁死） | 一致 |

