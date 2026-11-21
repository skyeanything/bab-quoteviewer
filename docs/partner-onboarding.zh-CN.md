# 第三方行情接入手册

本文面向接收行情的 App 服务端开发者。接入方使用独立的 `client_id` 和 `client_secret` 向 Bridge 获取短期 JWT，再通过一条 WebSocket 接收指定 group 的全部品种。客户端会自动获取 Token、续期和重连。

公开仓库中的地址、凭证标识和 group 均为示例。服务提供方交付的私有包中，`config.json`、凭证文件和证书已经填写，可直接按本文启动。本文不要求接入方生成签名私钥或填写 JWT 声明。

## 1. 收到哪些文件

| 文件 | 内容及用途 |
| --- | --- |
| `config.json` | 实际 HTTPS/WSS 地址、`sourceId`、允许访问的 `group`、客户端 ID，以及凭证和证书路径 |
| `secrets/client-secret.txt` | 本接入方独立的客户端密钥，由服务提供方生成 |
| `certs/bridge-ca.pem` | 验证服务器身份的 CA 公共证书；私有 CA 部署随包提供 |
| `quoteviewer.py`、`start.py` | 自动取 Token、接收行情、原连接续期、断线重连和本机查看页面 |
| `start.sh`、`start.cmd`、`start.ps1` | macOS/Linux 和 Windows 启动脚本 |
| `requirements.txt`、`wwwroot/` | Python 依赖和页面文件 |
| `docs/` | 本手册及完整英文接口规范 |

私有包包含可用凭证，只交给获授权的同事。不要上传公开仓库，也不要把密钥放进网页、移动 App、请求 URL 或共享日志。包内 CA 文件是公共证书，不含签名私钥、TLS 私钥或服务器管理凭证。

服务提供方负责开户、固定权限、签发 JWT、服务器证书和网络入口。接入方负责在自己的服务端保管凭证、运行客户端、消费行情并分发给自己的用户。

## 2. 启动并看到行情

准备 Python **3.11 或更高版本**，将压缩包完整解压。首次启动需要访问 Python 包索引以安装依赖；启动脚本不会安装 Python 本身。不需要 .NET SDK、SSH、MT5 终端或 Manager SDK。

macOS / Linux，在解压目录执行：

```sh
sh start.sh
```

Windows，在解压目录运行：

```bat
start.cmd
```

脚本创建 `.venv`，按 `requirements.txt` 安装依赖并启动客户端。打开 [本机行情页面](http://localhost:8877)，保留 Python 进程运行；按 Ctrl+C 停止。

手动启动方式：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python start.py
```

Windows 将 `.venv/bin/python` 换成 `.venv\Scripts\python.exe`。已安装依赖后，无需每次重新安装，可直接运行 `start.py`。

启动成功后检查：连接状态正常、品种数出现、消息数持续增加。观察至少两次「续期」成功，正常续期期间连接数应保持不变。休市品种可能陈旧，未收到过报价的品种可能没有价格，应同时查看活跃品种。

## 3. 配置从哪里来

以下字段由服务提供方预填。拿到私有交付包后不需要根据文档猜测这些值。

| 配置项 | 类型 | 来源和含义 |
| --- | --- | --- |
| `bridgeUrl` | 字符串 | WSS 基础地址，含端口，不带 `/v1/...` 路径 |
| `sourceId` | 字符串 | 服务端定义的 MT5 数据源 ID，不是 MT5 账号 |
| `group` | 字符串 | 授权的 MT5 账户组/报价组完整名称，不是「贵金属」「美股」等分类 |
| `httpPort` | 整数 | 本机页面端口，默认 8877；被占用时可修改 |
| `tlsCaFile` | 字符串或 `null` | CA 文件路径，相对配置文件；`null` 使用系统信任库 |
| `auth.tokenUrl` | 字符串 | 完整的 HTTPS Token 签发接口地址 |
| `auth.clientId` | 字符串 | 服务提供方分配的独立客户端 ID |
| `auth.clientSecretFile` | 字符串 | 客户端密钥文件路径，相对配置文件 |

JSON 中一个反斜杠写成 `\\`。例如 `"demo\\group"` 的实际组名为 `demo\group`。`sourceId`、group 和品种名称均按实际值精确匹配，保留大小写和品种后缀。

服务端把凭证固定绑定到指定 source/group。修改本机 `group`、拼接另一个 URL 或在请求中增加权限字段，都不能扩大权限。需要另一组行情时，由服务提供方另外配置授权。

## 4. JWT 怎样生成

接入方只提供客户端凭证，Bridge 完成 JWT 构造和签名。

| JWT 字段 | 由谁确定 |
| --- | --- |
| `iss` | Bridge 配置的签发者 |
| `aud` | Bridge 配置的受众 |
| `kid` | Bridge 使用的签名密钥标识 |
| `sub` | Bridge 根据客户端 ID 生成的稳定身份 |
| `jti` | 每次签发生成的新 Token ID |
| `iat`、`nbf`、`exp` | Bridge 根据当前时间和有效期生成 |
| `mt5bridge_grant` | Bridge 从该客户端的服务端授权配置生成 |

因此，接入方**不需要获取或填写 `iss`、`aud`、`kid`、`sub`，也不需要私钥**。不要自行修改返回的 Token；JWT payload 可读但不可篡改，否则签名失效。

### 4.1 请求 Token

方法及路径：`POST /v1/quote-tokens`。使用配置中的完整 `auth.tokenUrl`，必须通过 HTTPS。

```http
Authorization: Basic <Base64(client_id:client_secret)>
Content-Type: application/x-www-form-urlencoded
```

请求正文只有一个字段：

```text
grant_type=client_credentials
```

Basic 值的生成方式：将客户端 ID、英文冒号、客户端密钥按此顺序连接，转换为 UTF-8 字节，再进行 Base64 编码。Base64 不提供加密，因此必须使用 HTTPS。

不要传 `scope`、`sourceId`、`group`、`iss`、`aud`、`sub` 等字段；额外表单字段会被拒绝。

下面是可单独运行的 Token 获取示例，只打印有效期，不打印密钥或 JWT：

```python
import asyncio
import json
from pathlib import Path
import ssl

from quoteviewer import ClientCredentialsTokenProvider

async def main():
    path = Path("config.json").resolve()
    config = json.loads(path.read_text())
    ca = config.get("tlsCaFile")
    tls = ssl.create_default_context(
        cafile=str(path.parent / ca) if ca else None
    )
    provider = ClientCredentialsTokenProvider(config, path.parent, tls)
    token = await provider()
    print({"expires_at": token.expires_at})
    # token.value 是后续 WebSocket 使用的 JWT。

asyncio.run(main())
```

### 4.2 成功响应

HTTP 200，JSON 示例：

```json
{
  "access_token": "<signed-jwt>",
  "token_type": "Bearer",
  "expires_in": 120,
  "expires_at": 1790000120
}
```

| 字段 | 类型与单位 | 用途 |
| --- | --- | --- |
| `access_token` | 字符串 | WebSocket 鉴权和续期所用的 JWT |
| `token_type` | 字符串，固定 `Bearer` | 握手请求头的认证类型 |
| `expires_in` | 整数，秒 | 本次签发的有效时长；当前默认 120 秒 |
| `expires_at` | 整数，Unix UTC 秒 | 绝对到期时间，用于安排续期 |

示例时间戳不是可用的固定值，以每次响应为准。Token 接口响应设置 `Cache-Control: no-store` 和 `Pragma: no-cache`。

没有 `refresh_token`。续期时使用相同客户端凭证再次调用此接口，取得新的 JWT。正常情况下，120 秒 Token 约在签发后 90 秒更新一次，不要按品种或每个 App 用户分别请求 Token。

## 5. 一条连接订阅指定 group 的全部品种

完整路径规则：

```text
wss://quotes.example.com/v1/sources/example-source/quotes/stream?group=demo%5Cgroup
```

使用 `bridgeUrl` 拼接路径。`sourceId` 作为路径段进行 URL 编码，group 作为查询参数编码。实际部署可以使用 IP + 端口，证书必须包含该 IP；无需域名或 SSH 隧道。

在 WebSocket 握手请求中发送：

```http
Authorization: Bearer <access_token>
```

握手成功为 HTTP 101。随后立即发送 JSON 文本帧：

```json
{"all":true}
```

无需提交几百个品种名称，也无需为每个品种建立连接。`all` 表示 URL 中指定 group 的全部品种，不是全部 group。此模式不传 `symbol` 查询参数，也不在订阅消息内重复传 group。

首次订阅消息默认须在 5 秒内发送。服务端读取完整组目录并检查授权及容量；不会通过静默截断目录来完成订阅。默认最大上下文数为 4,096，实际容量以部署配置为准。

连接期间品种集合固定。MT5 后续新增品种时，需主动重连并再次订阅以取得新目录；JWT 续期不会改变订阅集合。

## 6. 自动续期，不清空报价

客户端并行运行接收和续期任务。当前 Token 到期前约 30 秒执行：

1. 用相同 `client_id` 和 `client_secret` 请求新 JWT。
2. 在**原 WebSocket** 中发送以下消息。`token` 是原始 JWT，不带 `Bearer ` 前缀。
3. 接收行情的同时等待服务端续期确认。
4. 只有确认 `accepted`，才用返回的 `expiresAt` 更新连接有效期。

```json
{"type":"authorization","token":"<new-access-token>"}
```

成功确认：

```json
{
  "schemaVersion":"1",
  "type":"authorization",
  "status":"accepted",
  "code":null,
  "expiresAt":"2026-01-01T00:02:00+00:00"
}
```

正常续期不重建连接、不重新订阅、不清空缓存。确认消息和行情消息可能交错到达，应先检查 `type`。

续期拒绝时返回 `status: "rejected"` 和错误码，原有效期不变。连续三次续期拒绝会关闭连接；到期仍未续期成功也会关闭。客户端在有效期内延迟重试；真正断线后标记缓存陈旧，并重新获取 Token、连接和订阅。

HTTP Token 请求超时为 15 秒，续期确认等待为 10 秒，均受当前授权的绝对到期时间约束。不要把发送续期消息视为续期成功，也不要在到期后继续把缓存标记为实时。

## 7. 接收数据和接入业务

`type` 为 `quote` 或 `context` 时处理行情；`authorization` 是续期确认。

| 字段 | 类型/含义 | 处理方式 |
| --- | --- | --- |
| `schemaVersion` | 字符串，当前为 `"1"` | 检查支持的协议版本 |
| `sourceId`、`contextGroup`、`symbol` | 字符串 | 共同构成缓存键 |
| `contextVersion` | 字符串 | 行情上下文标识，变化时按上下文重置处理 |
| `sequence` | 64 位整数 | 单个上下文内序号，不能跨品种比较 |
| `readiness` | `initializing`、`ready`、`stale` | 初始化、可用、陈旧；有价格不等于实时 |
| `observedAt` | UTC 时间字符串 | Bridge 的观察时间 |
| `sourceAgeMilliseconds` | 毫秒或 `null` | 观察时源数据年龄；`null` 表示未知 |
| `maximumDataAgeMilliseconds` | 毫秒 | 可用报价的最大年龄，读取消息实际值 |
| `coalescedCount`、`hasGap` | 整数、布尔值 | 合并更新数量及已知缺口 |
| `isCompleteReplay` | 布尔值，当前为 `false` | 不提供完整逐 tick 回放 |
| `data` | 对象或 `null` | 报价数据，允许尚无报价 |

`data.bid`、`ask`、`last`、`volume`、`volumeExt`、`flags` 均为字符串。计算价格时使用 Decimal 等十进制定点类型；不要转换成浮点数后再展示。`last` 为零不代表 bid/ask 无效。

`data.datetime` 和 `datetimeMilliseconds` 是 MT5 交易服务器时间值，其 UTC 映射尚未验证。不要直接将它们当作可靠 UTC 时间；新鲜度参考 `observedAt` 和年龄字段。

完整报价 JSON、全部续期错误码以及不带浏览器的 Python 接收示例见 [接口规范](market-data-integration.md)。示例通过 `ClientCredentialsTokenProvider` 和 `QuoteFeed` 复用已实现的鉴权、续期与重连逻辑，可在 `observe(value, raw)` 中接入业务缓存。

此流提供最新值并可能合并中间更新，不保证收到每个 tick。重连会重新接收当前上下文及后续变化，不回放断线期间的全部行情。网络断开时 Python 进程保留最后报价并标为陈旧；**退出或重启 Python 进程会清空内存缓存**。

页面的实时状态还要求：连接有效、该行来自当前连接、`readiness == "ready"`，且数据年龄未超过上限。休市或没有 tick 的品种可以保持陈旧或空值，不能据此判断鉴权失败。

## 8. 权限、凭证和证书更新

独立凭证只获得指定 source/group 的品种目录、品种元数据和行情读取权限。不包含账户读取、交易下单、资金操作、账户修改或其他组权限。

客户端密钥与短期 JWT 不同：当前客户端密钥没有自动到期时间，由服务提供方轮换或停用。密钥泄露时联系服务提供方停用或轮换，不要只删除本机 Token。

服务提供方修改服务端凭证配置并重启 Bridge 后，新密钥生效。已经签发的 JWT 不会仅因密钥轮换而立即失效，其签名有效性仍延续到原到期时间，最长为配置的 Token 有效期。交付实现不承诺即时吊销所有已签发 JWT。

接入方替换 `secrets/client-secret.txt` 后重启 Python 客户端；密钥在进程启动时读取。重启会重新连接并重新建立内存缓存。

证书有效期和指纹见私有交付说明。服务器叶证书到期前由服务提供方续签；如 CA 不变，接入方无需更换 CA 文件。如 CA 更换，则使用服务提供方交付的新证书。不得使用 `ssl=False`、关闭主机名校验或忽略证书错误。

## 9. 常见问题与验收

| 现象 | 检查与处理 |
| --- | --- |
| Token 接口 400 `https_required` | 使用配置中的 HTTPS 地址 |
| Token 接口 400 `invalid_request` | 正文只传 `grant_type=client_credentials`，检查表单 Content-Type |
| Token 接口 401 `invalid_client` | 核对客户端 ID、密钥文件及服务端启用状态 |
| Token 接口 429 | 当前默认整个签发接口共 60 次/分钟，延迟重试；不是每个客户端 60 次 |
| TLS 校验失败 | 检查 CA 文件、IP/域名、证书有效期和系统时间 |
| WebSocket 401 | Token 缺失、无效或过期；重新取 Token |
| HTTP 403 / `reference_scope_denied` | URL source/group 与授权不一致；本机改配置不会扩大权限 |
| `grant_expired` | 未在绝对到期前完成续期；检查 Token 请求和确认响应 |
| `caller_mismatch` | 新 Token 身份与原连接不一致；不要混用其他客户端凭证 |
| `quote_catalog_unavailable` | group 目录为空、不完整或暂不可用，由服务提供方核查 |
| `slow-consumer-resubscribe` | 接收消费跟不上；避免在接收循环内执行耗时业务，重连重新订阅 |
| 页面没有匹配的实时品种 | 查看连接状态、缓存、筛选条件及市场是否活跃，区分无品种与报价陈旧 |

交付验收应记录实际结果：独立凭证成功取 Token；错误凭证被拒绝；其他 source/group 被拒绝；一条连接收到完整组目录；两次续期成功且跨过旧 Token 原到期时间后仍持续收到活跃行情；断线缓存处理通过回归验证。

公开仓库的自动测试使用本地 HTTPS/WSS 环境，不等于部署验收。私有包中的交付说明列出该次部署实际执行的检查及结果。
