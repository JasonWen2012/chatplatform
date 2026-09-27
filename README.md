# 类微信即时通信服务器

基于 **Python 3.13 + Django 4.2.7** 的即时通信服务：**浏览器直接访问即用**，同时对外提供**独立协议 API**，
任意客户端（含命令行）都能通过 Bearer Token 接入。

第一期采用 HTTP 长轮询实现实时推送，架构上已把实时层抽象为「事件流 + 游标」，
第二期接入 WebSocket 时只需替换传输通道，业务代码无需改动。

---

## 功能范围（第一期）

| 类别 | 已实现 |
|---|---|
| 账号 | 注册、登录（支持用户名或昵称）、登出、改密码、令牌换发与撤销、**头像上传（自动裁剪为 256×256）** |
| 资料与偏好 | 昵称、个性签名、**主题（浅色/深色/跟随系统）、字号、气泡样式、通知开关（存服务端，二期 App 共享）** |
| 联系人 | 用户搜索、好友申请与接受/拒绝、好友列表、删除好友 |
| 会话 | 单聊（幂等复用）、群聊（建群、**界面内改名/加人/踢人**、退群、群主顺位移交） |
| 消息 | 文本、图片、文件、表情；历史回溯与增量同步；**引用回复（展示被引用内容并可定位）**；`client_msg_id` 幂等 |
| 消息体验 | 未读数、单聊已读回执（**实时刷新**）、群聊「N 人已读」、2 分钟内撤回、正在输入 |
| 实时 | 长轮询事件流（`message.new` / `revoked` / **`removed`** / `read.receipt` / `typing` / 好友事件） |
| 附件 | 上传（20MB 上限）、图片缩略图、真实格式校验、按会话成员鉴权下载、**孤儿附件回收命令** |
| 搜索 | **全局搜索**（消息 / 用户 / 会话，消息严格限定本人会话） |
| 举报治理 | 举报消息 → 管理员在管理界面结案或驳回（可同时移除消息） |
| 管理员 | **`/manage/` 六页管理界面**：概览、用户、消息检索、会话浏览、举报处理、操作日志 |
| 交付形态 | 网页版（移动端自适应）+ `/api-docs/` 协议文档 + `/healthz` 健康检查 |

**本期不含**：语音/视频通话、朋友圈、支付、扫码登录、表情贴图包、离线推送（登录后经历史接口补拉）、**请求限流（见「安全取舍」）**。

---

## 管理员体系

服务器管理员 = 账号具备 `is_staff`（被禁用时立即失效）。**管理员照常使用完整聊天界面**，只是额外多一个管理界面。

```powershell
# 授予（两种定位方式都支持）
.venv\Scripts\python.exe manage.py grant_admin --username Jason_Wen
.venv\Scripts\python.exe manage.py grant_admin --nickname WJ_RushB
# 查看 / 撤销
.venv\Scripts\python.exe manage.py grant_admin --list
.venv\Scripts\python.exe manage.py grant_admin --username Jason_Wen --revoke
```

授权后：聊天界面侧栏出现 🛠️ 入口，或直接访问 <http://127.0.0.1:8000/manage/>。

**能力边界（有意设计）**：

| 能做 | 不能做 |
|---|---|
| 查看统计、用户列表、会话与成员 | 在他人会话里**冒名发言**（写操作不旁路，返回 404） |
| 禁用/解禁用户、强制下线全部设备 | 禁用**自己**（会自锁，返回 400 `self_ban`） |
| 检索跨会话消息、移除违规消息 | 通过 `/manage/` 删除用户（高风险操作仅保留在 `/admin/`） |
| 处理举报（结案可同时移除消息） | — |

- **读取他人会话会留痕**：管理员打开非本人会话时写入一条 `conversation.view` 审计记录。
- **所有管理写操作都有审计**：谁、何时、对什么对象做了什么，在「操作日志」页可查，`/admin/` 中为只读。
- 管理员同时具备 `is_superuser`，因此也能使用 Django 原生 `/admin/` 作为兜底（可删任意数据，README 明示该副作用）。

---

## 快速开始

```powershell
# 1. 安装依赖（虚拟环境已包含 Django 与 Pillow）
.venv\Scripts\python.exe -m pip install -r requirements.txt

# 2. 初始化数据库
.venv\Scripts\python.exe manage.py migrate

# 3. 启动服务
.venv\Scripts\python.exe manage.py runserver
```

浏览器打开 <http://127.0.0.1:8000/>，注册两个账号即可互相聊天。
手机与电脑处于同一局域网时，可用 `runserver 0.0.0.0:8000` 让手机访问 `http://<电脑IP>:8000/`。

可选：创建管理后台账号 `manage.py createsuperuser`，然后访问 `/admin/`。

### 用 ngrok 对外访问（重要）

ngrok 是 HTTPS 隧道，而 **Django 4.2 起会校验 POST 请求的 `Origin` 头**。
若域名未列入可信来源，登录与注册会直接报错：

```
禁止访问 (403)
CSRF 验证失败. 请求被中断.
Origin checking failed - https://xxx.ngrok-free.dev does not match any trusted origins.
```

解决办法是把公网域名通过环境变量告知服务，**两者需同时配置**：
`CSRF_TRUSTED_ORIGINS`（放行 Origin 校验）与 `ALLOWED_HOSTS`（放行 Host 校验）。

```powershell
# 推荐：用附带脚本启动，自动注入域名与 HTTPS Cookie 设置
.\scripts\start.ps1 -PublicOrigin https://lapped-entourage-headphone.ngrok-free.dev

# 如果 PowerShell 执行策略禁止运行 .ps1，用等价命令行：
$env:DSH_PUBLIC_ORIGIN="https://lapped-entourage-headphone.ngrok-free.dev"
$env:DSH_PUBLIC_HTTPS="1"
.venv\Scripts\python.exe manage.py runserver
```

然后照常启动 ngrok：

```powershell
ngrok http 8000
```

关于两个环境变量：

| 变量 | 作用 | 何时需要 |
|---|---|---|
| `DSH_PUBLIC_ORIGIN` | 把该来源加入 `CSRF_TRUSTED_ORIGINS` 与 `ALLOWED_HOSTS`（多个用逗号分隔） | **必须**，不设置就会 403 |
| `DSH_PUBLIC_HTTPS` | 让 Session / CSRF Cookie 带上 `Secure` 标记，并启用 `X-Forwarded-Proto` 协议识别 | 经 HTTPS 访问时建议开启 |

注意事项：

- **ngrok 免费版每次重启域名都会变**，换域名后要用新的 `-PublicOrigin` 重启服务，否则又会 403。
- 放行的是**完整来源（含协议）**，写 `https://` 而不是裸域名；反向代理场景需与浏览器地址栏完全一致。
- 该配置只放行你显式指定的域名：陌生来源（例如 `https://evil.example.com`）依然会被 CSRF 拦截。
- 首次访问 ngrok 地址时，ngrok 免费版会显示一个中间提示页，点击确认后即正常。
- 公网暴露时建议同时设置独立密钥：`$env:DJANGO_SECRET_KEY="换成随机长字符串"`。

### 运行测试

```powershell
.venv\Scripts\python.exe manage.py test
```

`manage.py` 在检测到 `test` 子命令时会自动切换到 `backend.config.settings_test`
（长轮询超时归零、密码哈希换 MD5、上传目录隔离到 `_test_media/`），
因此 90+ 个用例可在数秒内跑完。

### 端到端验收脚本

`scripts/` 下附带五个不依赖测试框架的验收集，直接打真实 HTTP 端口，适合部署后自检
（需先启动 `runserver`）：

```powershell
# 39 项：注册→好友→单聊→长轮询→已读→输入中→群聊→撤回→图片上传→越权防护
.venv\Scripts\python.exe scripts\e2e_check.py

# 19 项：静态资源、页面渲染、未登录跳转、登录后聊天页关键节点
.venv\Scripts\python.exe scripts\ui_check.py

# 10 项：ngrok / 反向代理场景（Origin 放行与拒绝边界、HTTPS Cookie、可信域名登录）
.venv\Scripts\python.exe scripts\ngrok_check.py

# 49 项：管理端（越权拦截、读旁路与写不旁路、禁用/解禁、消息移除、举报、审计）
# 需先执行 grant_admin 授权，可用 $env:ADMIN_USERNAME 指定管理员账号
.venv\Scripts\python.exe scripts\admin_check.py

# 35 项：二期接口预留（能力声明、传输层抽象、事件存储工厂、WebSocket 契约、健康检查）
.venv\Scripts\python.exe scripts\ws_contract_check.py
```

五个脚本都是**幂等**的：每次运行使用带时间戳的用户名，无需清库即可重复执行。
`ngrok_check.py` 验证「配置生效且防护未被削弱」：可信域名可正常登录，陌生域名仍被 CSRF 拒绝。

---

## 项目结构

```
manage.py                     命令行入口（test 自动切换测试配置）
requirements.txt
.env.example                  环境变量清单（复制后注入，无需额外依赖）
backend/
  config/                     配置：settings / urls / asgi / wsgi / db_pragmas / settings_test / test_runner
  accounts/                   自定义 User、Token、UserPreference、统一 JSON 与鉴权（api/api.py、api/guards.py）
                              avatar.py 头像裁剪，management/commands/grant_admin.py 授权命令
  contacts/                   FriendRequest、Friendship 及联系人接口
  chat/                       Conversation / Membership / Message / MessageRead / Attachment / MessageReport
                              services.py 领域服务，serializers.py 序列化，api/views.py 接口
                              api/search_views.py 全局搜索
                              management/commands/cleanup_attachments.py 附件回收
  realtime/                   事件流 stream.py（EventStore）、registry.py（存储工厂，二期换 Redis）
  ops/                        管理员体系：api_views.py（/api/v1/ops/*）、web_views.py（/manage/）
                              models.py（AdminActionLog 审计）、pagination.py
                              templates/ops/ 六个管理页面，static/ops/ 管理界面样式与脚本
  apiapp/                     网页视图、总路由、协议文档页、health.py（/healthz）、checks.py（生产配置校验）
  templates/                  base / auth / app / api_docs
  static/                     css/app.css、js/{ui,api,transport,app}.js
  tests_utils.py              测试公共辅助
media/                        用户上传（uploads/ 与 thumbs/）
logs/                         运行日志（轮转，自动创建）
scripts/                      启动与验收工具
  start.ps1                   启动服务（可注入 ngrok 公网来源）
  e2e_check.py                协议端到端验收（39 项）
  ui_check.py                 网页界面冒烟检查（19 项）
  ngrok_check.py              反向代理/CSRF 场景验证（10 项）
  admin_check.py              管理端验收（49 项）
  ws_contract_check.py        二期接口预留验收（35 项）
db.sqlite3                    SQLite 数据库
```

---

## 架构要点

### 1. 一套协议，两种接入

网页端用 Session Cookie，外部客户端用 `Authorization: Bearer <token>`，
**两者复用同一批视图**，所以行为天然一致。未登录时返回 `401` JSON 而非 302 跳转，
保证命令行客户端行为可预期。

统一响应：

```jsonc
{ "ok": true,  "data": { ... } }
{ "ok": false, "error": { "code": "not_friends", "message": "需要先成为好友才能发起会话" } }
```

### 2. 会话内单调序号 `seq`

每条消息在会话内递增，既作稳定排序键，又是长轮询游标与未读/已读的唯一依据。
分配时对 `Conversation` 行加 `select_for_update` 锁，避免并发重号。

### 3. 事件流与游标

```
{ "event_id": 128, "type": "message.new", "conversation_id": 7, "payload": {...}, "ts": "..." }
```

客户端携带 `cursor` 长轮询 `/api/v1/updates/`，服务端无新事件时挂起至多 25 秒后返回空数组。
`event_id` 单调递增，客户端据此保证**不漏不重**。

**第二期升级路径**：新增 Channels consumer 消费同一个 `EventStore`，
把 `backend/realtime/store.py` 的内存实现换成 Redis 即可，`publish()` 调用点与信封格式完全不变。

### 4. 安全与权限

- **权限隔离优先**：消息、附件、会话全部校验 `Membership`，非成员一律返回 **404**（而非 403），避免泄露资源是否存在。
- **撤回**：限发送后 2 分钟内，发送者或群管理员可操作；撤回后正文与附件指针在服务端抹除，重复撤回幂等。
- **上传**：扩展名与 `Content-Type` 只作前置白名单，**真实格式由 Pillow 解码校验**；落盘文件名为随机串；
  下载统一走 `/api/v1/attachments/<id>/download/` 鉴权，不直接暴露 `MEDIA_URL`。
- **密码**：使用 Django 内置校验器（长度、常见口令、纯数字、与用户名相似度）。

### 5. SQLite 并发策略

`backend/config/db_pragmas.py` 通过 `connection_created` 信号为每条连接设置
`journal_mode=WAL`、`synchronous=NORMAL`、`foreign_keys=ON`、`busy_timeout=20000`，
配合 `DATABASES["default"]["OPTIONS"]["timeout"]=20`，显著降低聊天高频写入下的锁冲突。

> 需切到 PostgreSQL 时，只需改 `settings.DATABASES` 并重跑迁移（模型未使用 SQLite 专有特性）。

---

## 主要接口速查

完整文档见 <http://127.0.0.1:8000/api-docs/>。

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/auth/register/` | 注册，返回 token |
| POST | `/api/v1/auth/login/` | 登录（用户名或昵称） |
| GET | `/api/v1/auth/me/` | 当前用户 |
| GET | `/api/v1/users/search/?q=` | 搜索用户 |
| GET/POST | `/api/v1/friends/`、`/api/v1/friend-requests/` | 好友与申请 |
| POST | `/api/v1/friend-requests/<id>/accept\|reject/` | 处理申请 |
| GET/POST | `/api/v1/conversations/` | 会话列表 / 创建（单聊幂等、建群） |
| GET/POST | `/api/v1/conversations/<id>/messages/` | 拉取消息 / 发送消息 |
| POST | `/api/v1/conversations/<id>/read/`、`/typing/` | 标记已读 / 上报输入中 |
| POST | `/api/v1/messages/<id>/revoke/` | 撤回消息 |
| POST/GET | `/api/v1/attachments/`、`/api/v1/attachments/<id>/download/` | 上传 / 下载 |
| POST | `/api/v1/updates/` | 长轮询事件流 |
| GET | `/api/v1/presence/?user_ids=1,2` | 在线状态 |

### 命令行验收示例

```powershell
# 注册两个账号
curl.exe -s -X POST http://127.0.0.1:8000/api/v1/auth/register/ `
  -H "Content-Type: application/json" `
  -d '{\"username\":\"alice\",\"password\":\"Zq7#md-94kx\"}'

# 携带令牌拉取事件流
curl.exe -s -X POST http://127.0.0.1:8000/api/v1/updates/ `
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" `
  -d '{\"cursor\":0,\"timeout\":25}'
```

---

## 测试覆盖

`manage.py test` 共 90+ 用例，重点覆盖：

- **认证**：重名注册、弱口令（过短/常见/纯数字/与用户名相同）、令牌鉴权、登出后失效、令牌换发、改密码吊销旧令牌。
- **联系人**：申请/接受/拒绝、重复申请、自我申请、已是好友、非收件人不可处理、删好友清理待处理申请。
- **消息**：非成员读写返回 404、`seq` 单调递增、`client_msg_id` 幂等、空消息拒绝、分页（`before_seq`/`after_seq`）、跨会话引用拒绝。
- **撤回**：发送者本人可撤回、超 2 分钟拒绝、他人（单聊）拒绝、群管理员可撤回他人消息、重复撤回幂等、正文被抹除。
- **已读与未读**：未读自增与归零、`last_read_seq` 单调不后退、单聊回执落库、群聊已读人数。
- **附件**：缩略图与尺寸识别、伪装扩展名的非图片被拒、超限被拒、非成员下载 404、不可引用他人附件、落盘文件名随机化。
- **实时**：游标不漏不重、超时不回放历史、超时上限、发消息触达对方事件流、撤回事件、输入状态 TTL、在线状态。
- **公网访问**：本地与可信公网来源的页面/静态资源可访问、未登录跳转、可信来源表单提交成功、陌生来源 Origin 仍被拒；
  协议 API 走 Bearer 令牌，不受 Cookie CSRF 限制。

---

## 运维与安全

### 环境变量

复制 `.env.example` 的清单，用环境变量注入（不引入 `python-dotenv`，避免额外依赖）：

| 变量 | 作用 | 何时需要 |
|---|---|---|
| `DJANGO_SECRET_KEY` | 签名密钥 | **生产必填**，缺失时启动即失败（`manage.py check` 报 `chat.E001`） |
| `DJANGO_DEBUG` | `1` 开发 / `0` 生产 | 公网部署务必设为 `0` |
| `DSH_PUBLIC_ORIGIN` | 公网来源（ngrok 等） | 经 HTTPS 隧道访问时必填，否则登录报 CSRF 403 |
| `DSH_PUBLIC_HTTPS` | Cookie 加 Secure | 经 HTTPS 访问时设为 `1` |
| `EVENT_STORE_BACKEND` | `memory` / `redis` | 一期保持 `memory`；二期改 `redis` |

`DJANGO_DEBUG=0` 时项目会自动：关闭 `ALLOWED_HOSTS` 通配符、开启 Cookie `Secure`、
启用代理协议识别，并拒绝缺少密钥的启动。

### 生产部署要点

```powershell
$env:DJANGO_DEBUG = "0"
$env:DJANGO_SECRET_KEY = "从 python -c \"import secrets;print(secrets.token_urlsafe(64))\" 生成"
.venv\Scripts\python.exe manage.py check --deploy     # 应无 CRITICAL
.venv\Scripts\python.exe manage.py migrate
.venv\Scripts\python.exe manage.py collectstatic --noinput
# 用正式服务器承载（runserver 仅用于开发）
.venv\Scripts\python.exe -m waitress --port=8000 backend.config.wsgi:application
```

> **注意**：`DJANGO_DEBUG=0` 后 Django 不再托管静态与媒体文件，**必须先 `collectstatic`
> 并由正式服务器（或前置 Nginx）指向 `staticfiles/`**，否则页面会没有样式。

### 定期维护

```powershell
# 回收孤儿附件与磁盘残留（建议先 dry-run 看清单）
.venv\Scripts\python.exe manage.py cleanup_attachments --dry-run
.venv\Scripts\python.exe manage.py cleanup_attachments --orphan-files
```

会话删除时不会同步删磁盘文件（事务回滚会导致文件先丢），因此依赖上面的命令兜底回收。
默认只处理创建超过 24 小时的附件，避免误删「刚上传还没发送」的文件。

### 健康检查

`GET /healthz` 无需鉴权，只返回 `ok` / `db` / `version`；数据库异常返回 503，便于上游摘除实例。

### 安全取舍（需知情）

- **未做请求限流**（按需求暂缓）：`/auth/login/`、`/auth/register/` 没有暴力破解防护。
  缓解措施是**登录失败审计日志**（`logs/app.log`，记录用户名与来源 IP，优先读 `X-Forwarded-For`），
  可据此事后发现异常。补限流的位置已预留：`backend/accounts/api/throttle.py`。
- 日志**不记录**密码、令牌明文与消息正文；令牌只记前 8 位。
- 管理员可读任意会话正文，这是治理需要，但每次读取都会留下审计记录。

---

## 已知限制与后续计划

1. **单进程状态**：事件缓冲、在线状态、输入状态默认存于进程内存，不跨 worker。
   切换点已就绪（`EVENT_STORE_BACKEND` + `backend/realtime/registry.py`），二期换 Redis 即可。
2. **长轮询占线程**：每个在线客户端占用一个请求线程，小规模使用足够；二期换 WebSocket 解决。
3. **Django 4.2 + Python 3.13**：非官方最优组合（4.2 官方支持到 3.12），本项目实测正常；
   若后续需要，升级 Django 5.1 属低风险改动（未使用两者差异 API）。
4. **全局搜索性能**：当前用 `icontains`，数据量增长后会变慢。接口不变的前提下，
   二期可上 SQLite FTS5 或 PostgreSQL 全文索引。
5. **第二期**：WebSocket 通道（契约已冻结，见 `/api-docs/`）、离线推送、
   语音/视频通话（WebRTC + STUN/TURN）、朋友圈。
