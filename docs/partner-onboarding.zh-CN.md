# 行情服务接口文档

本文供第三方服务端开发者将行情接入自己的代码。使用 HTTP 获取 JWT，使用一条 WebSocket 订阅授权 group 的全部品种，并在原连接内续期。

本文定义请求、响应和处理规则，不依赖特定编程语言、客户端程序或 SDK。所有 JSON 均使用 UTF-8。

## 1. 服务提供方交付的信息

私有交付资料包含以下三个文件，实际接入值已经填写：

| 文件 | 内容 |
| --- | --- |
| `行情接口文档.md` | 本文及该接入方的实际接口地址、权限范围 |
| `接入参数.json` | 完整 Token 地址、完整 WebSocket 地址、客户端 ID、授权 group、凭证文件名 |
| `client-secret.txt` | 独立客户端密钥，仅交给获授权的接入方 |

`接入参数.json` 仅用于交付参数。接入方可将这些值放入自己的配置或密钥管理系统，不必采用该文件结构。

接入所需参数：

| 参数 | 类型 | 用法 |
| --- | --- | --- |
| `tokenUrl` | 字符串 | 完整 HTTP URL，直接发送 POST |
| `webSocketUrl` | 字符串 | 完整 WS URL，已经包含路径和 URL 编码后的 group，直接连接 |
| `clientId` | 字符串 | HTTP Basic 的用户名，即 `client_id` |
| `clientSecretFile` | 字符串 | 客户端密钥文件名；文件中的密钥即 `client_secret`，读取时去除行尾换行 |
| `group` | 字符串 | 已开通的 MT5 账户组/报价组，供核对权限和返回数据 |

提供方已经把路由要求的数据源标识填入完整 WebSocket URL。接入方无需另外选择或配置 `sourceId`，直接使用给定地址；当前协议返回的行情消息仍包含该字段。

`group` 是 MT5 账户组/报价组，不是「贵金属」「美股」等品种分类。凭证在服务端固定绑定到授权范围；修改 URL 不会扩大权限。

Token 请求使用 HTTP，行情订阅使用 WS，两者使用同一 IP 的 `54013` 端口。网络需要允许接入服务器向给定地址和端口发起出站连接；行情通过已建立的 WebSocket 返回，没有回调地址要求。

公开文档不包含实际地址或密钥；向接入同事交付时，必须同时提供上述私有参数和密钥。

## 2. 调用顺序

1. 使用独立客户端凭证调用 Token 接口，取得 JWT 和有效期。
2. 用 JWT 作为 WebSocket 握手请求头中的 Bearer 凭证，连接完整 WebSocket URL。
3. 连接成功后立即发送 `{"all":true}`。
4. 持续接收 JSON 消息，按 `type` 区分行情、上下文状态和续期确认。
5. 到期前再次调用 Token 接口，并在原 WebSocket 中提交新 JWT。
6. 仅在服务端确认续期成功后更新连接有效期。真正断线时重新鉴权、连接和订阅。

一条 WebSocket 可接收该 group 的全部品种，无需按品种分别建立连接。

## 3. 获取 JWT

### 3.1 请求

使用 `tokenUrl` 发送以下请求：

```http
POST /v1/quote-tokens HTTP/1.1
Authorization: Basic <base64(client_id:client_secret)>
Content-Type: application/x-www-form-urlencoded

grant_type=client_credentials
```

| 项目 | 必填 | 规则 |
| --- | --- | --- |
| HTTP 方法 | 是 | `POST` |
| `Authorization` 请求头 | 是 | `Basic ` 加 Base64 编码结果 |
| `Content-Type` 请求头 | 是 | `application/x-www-form-urlencoded` |
| `grant_type` 表单字段 | 是 | 固定为 `client_credentials`，正文只有这一个字段 |

Basic 编码步骤：连接 `client_id`、一个英文冒号和 `client_secret`，转为 UTF-8 字节，再做 Base64 编码。多数 HTTP 库可直接指定 Basic 用户名和密码，由库生成请求头。不要把客户端密钥作为 Bearer Token 发送。

请求中不传 group、scope、JWT 声明或其他表单字段；额外字段会被拒绝。权限由服务端已配置的凭证映射决定。

### 3.2 成功响应

HTTP 200，响应为 JSON：

```json
{
  "access_token": "<signed-jwt>",
  "token_type": "Bearer",
  "expires_in": 120,
  "expires_at": 1790000120
}
```

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `access_token` | 字符串 | 后续握手和连接内续期使用的完整 JWT |
| `token_type` | 字符串 | 固定为 `Bearer` |
| `expires_in` | 整数，秒 | 本次 JWT 的有效时长；当前配置为 120 秒 |
| `expires_at` | 整数，Unix UTC 秒 | 本次 JWT 的绝对到期时间；不是毫秒 |

以上时间戳是格式示例，以实际响应为准。响应设置 `Cache-Control: no-store` 和 `Pragma: no-cache`。

### 3.3 JWT 由谁生成

JWT 由服务端使用 RS256 签名。接入方直接使用 `access_token`，无需实现签名，也无需获取或填写 `iss`、`aud`、`kid`、`sub`、`jti`、`iat`、`nbf`、`exp`。这些字段及权限声明均由签发接口生成。

不要修改返回的 JWT。客户端密钥用于换取 JWT，JWT 用于访问行情，两者不能互换。

### 3.4 更新 Token

没有 refresh token。到期前，用相同客户端凭证重复调用本节接口，即可取得新的 JWT。更新连接授权的消息格式见第 6 节。

建议提前约 30 秒开始续期。对于 120 秒的 Token，通常约每 90 秒获取一次新 Token。该间隔是客户端实施建议，不是接口 SLA。接入服务器应保持 UTC 时间同步；到期后没有额外有效窗口。

### 3.5 Token 接口错误

| HTTP 状态 | 响应 | 处理方式 |
| --- | --- | --- |
| 400 | `{"error":"invalid_request"}` | 检查 Content-Type，以及正文是否只有正确的 `grant_type` |
| 401 | `{"error":"invalid_client"}` | 核对客户端 ID、密钥和启用状态；不要持续使用错误凭证重试 |
| 429 | `{"code":"capacity_limit_reached","workClass":null}` | 降低请求频率，延迟重试 |
| 404 | 接口不存在 | 核对完整 URL 和所连接的服务版本 |
| 5xx / 超时 | 服务或网络异常 | 在旧授权有效期内延迟重试；到期仍失败则按断线处理 |

401 响应带 `WWW-Authenticate: Basic realm="quote-tokens"`。当前限流为整个 Token 接口合计 60 次/分钟，使用固定时间窗、不排队；不是每个客户端 60 次，也不保证返回 `Retry-After`。

## 4. 建立连接并订阅全部品种

使用交付的完整 `webSocketUrl`。在 WebSocket 握手请求中增加：

```http
Authorization: Bearer <access_token>
```

JWT 不放入 URL 查询参数。选择支持自定义握手请求头的服务端 WebSocket 库，使用交付的完整 WS 地址。

HTTP 101 表示升级为 WebSocket 成功。随后立即发送以下 UTF-8 JSON 文本消息：

```json
{"all":true}
```

此消息表示 URL 中已指定 group 的全部品种。不要再添加 `group`、`type` 或 `symbols` 字段，也不要在此模式的 URL 中添加 `symbol` 查询参数。

首次订阅消息须在服务端规定时间内发送，默认 5 秒；消息大小上限 64 KiB。服务端会读取完整组目录并检查权限及容量，成功后开始发送各品种的上下文和后续报价。该模式没有单独的「订阅成功」JSON 确认帧，应处理首次行情/上下文消息以及连接关闭事件。

默认上下文容量为 4,096，实际可用容量受部署配置和其他订阅影响。超过容量或组目录不完整时会拒绝订阅，不会静默删减品种。

连接期间订阅集合固定。若 MT5 新增品种，需重新建立连接并发送 `{"all":true}` 获取新目录；正常 JWT 续期不改变订阅集合。

应用层发送的消息在首次订阅之后只支持第 6 节的授权续期。不要发送自定义 JSON `ping` 消息；标准 WebSocket 控制帧由 WebSocket 库处理。

## 5. 接收行情

### 5.1 按消息类型分发

| `type` | 含义 | 处理方式 |
| --- | --- | --- |
| `quote` | 当前上下文有可用报价 | 更新对应品种缓存，结合时效字段判断是否实时 |
| `context` | 初始化或陈旧状态，可能附带最后报价 | 更新状态；不能因为 `data` 非空就当作实时 |
| `authorization` | 授权续期结果 | 按第 6 节处理，不按行情格式解析 |

行情与续期确认共用一个 WebSocket，可能交错到达。接收循环必须持续运行，不能在等待续期时停止读取行情。

### 5.2 行情消息示例

以下是格式示例，价格和时间不代表实际市场数据：

```json
{
  "schemaVersion": "1",
  "type": "quote",
  "sourceId": "example-source",
  "symbol": "EURUSD",
  "contextGroup": "demo\\group",
  "priceContext": "group",
  "contextVersion": "example-context-version",
  "readiness": "ready",
  "sequence": 42,
  "observedAt": "2026-01-01T00:00:00+00:00",
  "sourceAgeMilliseconds": 10,
  "maximumDataAgeMilliseconds": 5000,
  "coalescedCount": 0,
  "hasGap": false,
  "isCompleteReplay": false,
  "sourceTimeInterpretation": "mt5-server-trading-time-utc-mapping-unverified",
  "data": {
    "datetime": 1767225600,
    "datetimeMilliseconds": 1767225600000,
    "bid": "1.10001",
    "ask": "1.10003",
    "last": "0",
    "volume": "0",
    "volumeExt": "0",
    "flags": "0",
    "sourceDailyChangePercent": null
  }
}
```

### 5.3 消息字段

| 字段 | JSON 类型 | 含义及处理规则 |
| --- | --- | --- |
| `schemaVersion` | 字符串 | 当前为 `"1"` |
| `type` | 字符串 | `quote` 或 `context` |
| `sourceId` | 字符串 | 服务端返回的数据源标识，作为缓存命名空间的一部分；无需接入方生成 |
| `symbol` | 字符串 | 完整品种名称，保留大小写与后缀 |
| `contextGroup` | 字符串或 null | 此接入方式为授权 group，核对是否与交付参数一致 |
| `priceContext` | 字符串 | 此接入方式为 `group` |
| `contextVersion` | 字符串 | 上下文版本；变化时不能沿用旧版本的序号比较 |
| `readiness` | 字符串 | `initializing`、`ready` 或 `stale` |
| `sequence` | 64 位整数 | 单个上下文内序号，不是跨品种的全局序号 |
| `observedAt` | UTC 时间字符串 | Bridge 观察到该状态或报价的时间 |
| `sourceAgeMilliseconds` | 整数或 null，毫秒 | 观察时已知的源数据年龄；null 表示未知 |
| `maximumDataAgeMilliseconds` | 整数，毫秒 | 实时报价允许的最大年龄，默认 5,000，读取消息实际值 |
| `coalescedCount` | 64 位整数 | 合并掉的中间更新数量 |
| `hasGap` | 布尔值 | 是否存在已知缺口 |
| `isCompleteReplay` | 布尔值 | 当前为 false，不提供完整 tick 回放 |
| `sourceTimeInterpretation` | 字符串 | 说明原始 MT5 时间与 UTC 的映射尚未验证 |
| `data` | 对象或 null | 最近报价；尚无报价时为 null |

### 5.4 `data` 字段

| 字段 | JSON 类型 | 含义 |
| --- | --- | --- |
| `datetime` | 64 位整数 | MT5 服务器交易时间，Unix 秒形式 |
| `datetimeMilliseconds` | 64 位整数 | MT5 服务器交易时间，Unix 毫秒形式 |
| `bid` | 字符串 | Bid 价格 |
| `ask` | 字符串 | Ask 价格 |
| `last` | 字符串 | 最新成交价；为零不代表 bid/ask 无效 |
| `volume` | 字符串 | 标准精度成交量 |
| `volumeExt` | 字符串 | 扩展精度成交量 |
| `flags` | 字符串 | 原始行情标志 |
| `sourceDailyChangePercent` | 字符串或 null | 来源级日涨跌百分比，独立于 group 报价 |

价格、数量和 flags 保留字符串形式。需要计算价格时使用十进制类型；解析时间及序号时保留 64 位整数精度。

MT5 原始时间的 UTC 映射尚未验证，不应直接将 `datetime` 或 `datetimeMilliseconds` 当作可靠 UTC 时间。判断时效使用 `observedAt`、`readiness` 和年龄字段。

### 5.5 缓存和时效

建议按返回的 `(sourceId, contextGroup, symbol)` 保存最新状态，避免不同上下文混用。展示为实时报价至少要求：连接有效、消息来自当前连接、`readiness == "ready"`，且数据年龄在允许范围内。

已知源数据年龄时，可用以下方式估计当前年龄，统一使用毫秒：

```text
当前年龄 = max(0, 接入服务器当前 UTC 时间 - observedAt) + sourceAgeMilliseconds
```

`sourceAgeMilliseconds` 为 null 表示未知，不能仅将它当作零而确认报价实时。系统时钟偏差也会影响估算。休市品种可以长期陈旧，从未收到 tick 的品种可以没有报价。

## 6. 在原 WebSocket 中续期

### 6.1 发送新 Token

旧授权到期前，再次请求第 3 节的 Token 接口。使用同一客户端凭证获取新 JWT，并在当前 WebSocket 发送：

```json
{"type":"authorization","token":"<new-access-token>"}
```

`token` 只放原始 JWT，不带 `Bearer ` 前缀。不要添加其他字段；同一连接同时只保留一个待确认的续期请求。完整续期消息上限为 33 KiB，JWT 上限为 32 KiB。

### 6.2 成功确认

```json
{
  "schemaVersion": "1",
  "type": "authorization",
  "status": "accepted",
  "code": null,
  "expiresAt": "2026-01-01T00:02:00+00:00"
}
```

`expiresAt` 为 UTC 时间字符串，格式与 Token 接口的整数 `expires_at` 不同。时间值以实际响应为准。

只有收到 `accepted` 后，才把连接当前授权的到期时间替换为此值。成功续期保持原连接、原订阅和行情上下文，不需要再次发送 `{"all":true}`，也不应清空缓存。

### 6.3 拒绝确认

```json
{
  "schemaVersion": "1",
  "type": "authorization",
  "status": "rejected",
  "code": "caller_mismatch",
  "expiresAt": "2026-01-01T00:02:00+00:00"
}
```

拒绝不会安装新 Token，也不会延长旧授权；响应中的 `expiresAt` 仍为原到期时间。新 JWT 必须属于同一个客户端身份，并覆盖已有订阅范围。不要混用另一个客户端的 Token。

连续三次续期拒绝会关闭连接；旧授权已到期时也会直接关闭。超时、请求失败或没有收到确认，都不能视为续期成功。

| `code` | 处理方式 |
| --- | --- |
| `grant_invalid` | 检查是否使用签发接口返回的完整 JWT，避免截断或改写 |
| `grant_expired` | 获取新 JWT，检查接入服务器时间和传输延迟 |
| `grant_revoked` | 联系服务提供方核查授权状态 |
| `caller_mismatch` | 保持同一客户端凭证，不混用其他身份 |
| `reference_scope_denied` | 当前 Token 不覆盖既有订阅，核查授权 group |
| `quote_authorization_limit` | 授权并发繁忙，仅在旧授权有效期内延迟重试 |
| `grant_validation_timeout` | 验证超时，仅在旧授权有效期内延迟重试 |
| `grant_validation_unavailable` | 验证服务暂不可用，仅在旧授权有效期内延迟重试 |

## 7. 连接错误和重连

### 7.1 WebSocket 升级前

失败时未建立 WebSocket，读取 HTTP 状态及 JSON `code`。例如：

```json
{"code":"reference_scope_denied"}
```

| HTTP 状态 | 常见 `code` | 处理方式 |
| --- | --- | --- |
| 400 | `invalid_quote_subscription` | 核对完整 URL、group 和 WebSocket 握手请求 |
| 401 | `grant_invalid`、`grant_expired`、`grant_revoked` | 获取有效 JWT 或核查授权状态 |
| 403 | `reference_scope_denied` | 请求不在授权范围，核对交付的完整地址 |
| 429 | `quote_authorization_limit`、`grant_validation_limit` | 延迟重试，避免并发重连 |
| 503 | `grant_validation_timeout`、`grant_validation_unavailable`、`quote_stream_unavailable` | 服务暂不可用，延迟重试 |

### 7.2 WebSocket 建立后

| Close code | Close reason | 含义及处理方式 |
| --- | --- | --- |
| 1008 | `grant_expired` | 授权过期，获取新 JWT 后重连 |
| 1008 | `grant-no-longer-authorized` | 当前授权不再允许连接，核查授权 |
| 1008 | `authorization-renewal-failed` | 连续三次续期拒绝，先处理拒绝原因 |
| 1008 | `client-messages-not-supported` | 消息类型、字段或发送顺序不受支持 |
| 1009 | `authorization-renewal-too-large` | 续期消息超过大小上限 |
| 1008 | `reference_scope_denied` | 订阅不在当前权限范围 |
| 1008 | `quote_subscriber_limit` | 订阅或上下文容量已满 |
| 1011 | `quote_catalog_unavailable` | 组目录为空、不完整或暂不可用 |
| 1008 | `slow-consumer-resubscribe` | 消费跟不上，减少接收循环中的阻塞处理后重连 |
| 1001 | `quote-stream-stopped` | 行情流已停止，延迟重连 |
| 1011 | `grant-refresh-capacity`、`grant-refresh-timeout`、`grant-refresh-failed` | 服务端重验授权失败，延迟重连 |

初始订阅消息超限也可能关闭为 1009。网络故障或超时可能直接断开，没有 Close 帧；同时处理库报告的连接错误。

### 7.3 重连规则

1. 连接断开后，立即将原连接缓存标为陈旧；可保留最后价格用于展示。
2. 延迟重试，并重新获取 JWT。建议采用有上限的退避和随机抖动，避免多实例同时重连。
3. 建立新连接，再发送 `{"all":true}`。
4. 逐品种收到新连接的数据后，重新判断该品种的新鲜度。

本流提供最新值，可能合并中间更新，不保证每个 tick 都被接收。重连后发送当前上下文与后续更新，不回放断线期间的全部 tick；没有断点续传游标。行情缓存及持久化由接入方自己的服务实现。

## 8. 与语言无关的实现流程

以下伪代码说明并发关系，可映射到现有服务的 HTTP/WebSocket 库：

```text
获取 JWT（clientId + clientSecret）
使用 Bearer JWT 建立 WebSocket
发送 {"all": true}
记录当前授权到期时间

接收任务持续处理：
    quote / context → 更新业务缓存与状态
    authorization accepted → 更新当前授权到期时间，安排下一次续期
    authorization rejected → 保留旧到期时间，按错误分类处理
    close / error → 标记缓存陈旧，进入重连流程

续期定时任务：
    在当前授权到期前约 30 秒请求新 JWT
    接收任务继续运行
    在原连接发送 {"type": "authorization", "token": 新 JWT}
    等待接收任务取得确认，不启动第二个并发接收循环
    未确认成功前，不延长当前授权期限
```

对接方应串行安排同一 WebSocket 的发送，并避免在接收回调中同步执行耗时业务。对于限流、临时网络失败，可在当前授权仍有效时重试；对于凭证错误或权限不匹配，应先修正配置或联系服务提供方。

## 9. 凭证和权限更新

独立凭证仅允许读取指定数据源/group 的品种目录、品种元数据和行情，不包含其他组、账户读取、交易、资金或账户修改权限。

客户端密钥当前没有自动到期时间，由服务提供方轮换或停用。轮换后将新密钥更新到接入方自己的配置系统，并确保后续 Token 请求使用新值。已签发 JWT 不会仅因密钥轮换立即失效，仍受到原到期时间限制；当前接口不提供 refresh token 或对接方可调用的即时吊销 API。

服务端凭证配置变更目前需重启 Bridge，接入方应处理正常断线重连。签名私钥由服务提供方保管，不交给接入方。

私有交付资料中的密钥、JWT 和 Authorization 请求头不应进入公开仓库、浏览器前端或共享日志。

## 10. 对接验收

| 检查项 | 通过标准 |
| --- | --- |
| 获取 Token | 用分配的凭证取得 200 响应，能读取 JWT 及到期时间 |
| 订阅 group | 一条 WebSocket 接收授权 group 的品种上下文和报价 |
| 原连接续期 | 至少两次收到 `accepted`，跨过旧 Token 的原到期时间后连接仍有效 |
| 数据处理 | 正确区分 quote、context、authorization，保留价格和整数精度 |
| 断线恢复 | 缓存标为陈旧，重新鉴权和订阅后按品种恢复实时状态 |
| 权限边界 | 错误凭证被拒绝，其他 group 无法访问 |

排障时提供时间、HTTP 状态、错误码或 Close code/reason，以及非敏感的连接与续期计数；不要附带客户端密钥或 JWT。
