# 行情服务对接手册

适用对象：接收 MT5 Bridge 行情的 App 服务端开发、签发服务开发和运维同事。

目标：通过一条 WebSocket 接收指定账户组的全部品种，在原连接内更新 JWT，并在真实断线后恢复订阅。

本文公开版使用示例参数，不包含生产地址、私钥或有效 Token。实际参数由服务提供方通过私下渠道交付。示例项目：[bab-quoteviewer](https://github.com/skyeanything/bab-quoteviewer)。完整协议约束见[英文接口文档](market-data-integration.md)。

## 1. 对接前需要拿到什么

先确定 JWT 的签发方式，再开始连接。仅有 WebSocket 地址不能完成鉴权。

| 项目 | 用途 | 谁提供 | 本文示例 |
| --- | --- | --- | --- |
| `bridgeUrl` | WebSocket 基础地址，不含接口路径 | Bridge 运维 | `wss://quotes.example.com` |
| `sourceId` | Bridge 配置的 MT5 数据源标识 | Bridge 运维 | `example-source` |
| `group` | MT5 账户组，用于确定组报价 | 业务方与 Bridge 运维确认 | `demo\group` |
| JWT `iss` | 签发方，必须与 Bridge 配置一致 | Bridge 运维 | `example-backend` |
| JWT `aud` | 接收方，必须与 Bridge 配置一致 | Bridge 运维 | `example-bridge` |
| JWT `kid` | 已登记公钥的标识 | 签发方与运维共同约定 | `example-key` |
| JWT `sub` | 调用服务的稳定身份，续期时保持不变 | 业务方与签发方约定 | `example-quote-client` |
| Token 有效期 | 单枚 JWT 的有效秒数 | Bridge 运维确认上限 | 120 秒 |
| 签名凭证或获取 Token 的方式 | 生成或获取每一枚新 JWT | 见下文两种方式 | 不能留空 |
| 网络可达性 | 对接方能访问服务地址和 TCP 端口 | 双方运维 | 连接前验证 |

`group` 是账户组，不是「贵金属」「外汇」等品种分类。`sourceId` 不是 MT5 登录账号。来源、组名、品种名均按实际值精确匹配，注意大小写和品种后缀。

**JWT 有两种获取方式，必须明确采用哪一种：**

- **受信任后端自行签发。** 对接后端同时承担签发方角色，持有自己的 RSA 私钥；Bridge 运维事先登记匹配公钥。本文第 3～5 节提供完整操作。这也是参考客户端 `TokenIssuer` 的工作方式。
- **普通对接方向受信任签发服务获取。** 私钥由服务提供方保管，对接方只拿短期 JWT。此时还必须交付签发服务地址、调用凭证、请求与响应格式及刷新规则。**当前 Bridge 和本示例没有提供公共 Token 签发接口**，不能假设存在 `/login`、`/token` 或 `/refresh`。

**当前权限边界：** Bridge 的公钥配置只把 `kid` 映射到验签公钥，没有按 `kid` 配置 `sub`、`sourceId`、`group` 或授权类型白名单。登记公钥代表信任该私钥持有者签发授权声明。下文的 Token 只授予行情读取，但私钥本身不会因此变成「只能读取这个组」的凭证。因此，不能把签名私钥作为普通第三方的受限 API Key 直接发放。

## 2. 双方分别做什么

| 顺序 | 执行方 | 动作 | 完成标志 |
| --- | --- | --- | --- |
| 1 | 双方 | 确认上表参数、允许的账户组和 JWT 签发方式 | 对接方知道每个参数的实际值 |
| 2 | 受信任签发方 | 生成 RSA 密钥对，保管私钥，交付公钥及 `kid` | 公钥交给运维；私钥留在签发后端 |
| 3 | Bridge 运维 | 登记公钥，按部署流程使配置生效，放行接入端口 | Bridge 接受该公钥对应的有效 JWT |
| 4 | 对接后端 | 生成或获取短期 JWT，带请求头建立 WebSocket | HTTP 升级成功，状态为 101 |
| 5 | 对接后端 | 在原连接发送 `{"all":true}` | 收到组内各品种的状态或报价 |
| 6 | 对接后端 | 持续接收消息；到期前取得新 JWT 并发送续期消息 | 收到 `status: "accepted"`，到期时间推进 |
| 7 | 对接后端 | 缓存最新状态并分发给自己的用户 | 用户界面能区分实时、过期和无报价 |
| 8 | 双方 | 按第 11 节完成验收 | 续期不中断，真实断线能恢复 |

普通消费者采用 Token 服务时，第 2～3 步由服务提供方的签发系统完成，对接方不需要拿到私钥。

## 3. 一次性生成并登记 RSA 密钥

本节只适用于已获准承担签发角色的后端。密钥生成一次即可；续期不生成新密钥。

在新的私有工作目录中执行以下 OpenSSL 命令。先确认没有需要保留的同名密钥。以下为 macOS/Linux shell 示例；Windows 可使用安装好的 OpenSSL，并由运维配置密钥文件访问权限。

```sh
umask 077
mkdir secrets
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 \
  -out secrets/signing-key.pem
openssl pkey -in secrets/signing-key.pem -pubout \
  -out secrets/signing-public.pem
```

- `signing-key.pem`：私钥，只放在签发后端的受保护目录或密钥管理系统中。
- `signing-public.pem`：公钥，交给 Bridge 运维。
- `kid`：与运维约定的公钥标识，不是密码，也不是公钥内容。

运维在现有 `Authorization:Jwt:PublicKeys` 数组中追加公钥条目，保留已有调用方的公钥。配置字段如下；这里的 PEM 是占位内容，不能直接部署：

```json
{
  "Authorization": {
    "Jwt": {
      "Enabled": true,
      "Issuer": "example-backend",
      "Audience": "example-bridge",
      "MaximumTokenLifetime": "00:02:00",
      "ClockSkew": "00:00:15",
      "PublicKeys": [
        {
          "KeyId": "example-key",
          "PublicKeyPem": "<完整公钥 PEM；换行使用 JSON 转义>"
        }
      ]
    }
  }
}
```

`PublicKeyPem` 接收公钥文件内容，不接收文件路径。JSON 字符串用 `\n` 表示换行。使用环境变量时，新增条目的字段为 `Authorization__Jwt__PublicKeys__N__KeyId` 和 `Authorization__Jwt__PublicKeys__N__PublicKeyPem`，`N` 使用下一个数组索引。

当前公钥环最多支持 16 个唯一 `kid`。修改后按运维流程重启 Bridge，使验签公钥环重新建立；消费者需要处理服务重启带来的断线。只在本机生成密钥、没有登记公钥，鉴权不会成功。

## 4. 准备对接客户端配置

参考项目需要 Python 3.11 或以上版本，不需要 .NET SDK、SSH、MT5 终端或 Manager SDK。

```sh
git clone https://github.com/skyeanything/bab-quoteviewer.git
cd bab-quoteviewer
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp config.example.json config.json
```

Windows PowerShell 使用 `.venv\Scripts\Activate.ps1` 激活环境，使用 `Copy-Item config.example.json config.json` 复制配置。先用 `python --version` 确认解释器版本。

把已登记私钥放在配置指定的位置，填写本机私有 `config.json`：

```json
{
  "bridgeUrl": "wss://quotes.example.com",
  "sourceId": "example-source",
  "group": "demo\\group",
  "httpPort": 8877,
  "auth": {
    "issuer": "example-backend",
    "audience": "example-bridge",
    "keyId": "example-key",
    "subject": "example-quote-client",
    "privateKeyFile": "secrets/signing-key.pem",
    "lifetimeSeconds": 120
  }
}
```

`privateKeyFile` 相对于配置文件所在目录解析，也可使用绝对路径。参考客户端默认读取未加密 PEM；需要密码保护的 PEM 时，应接入自己的密钥管理和签名实现。

`ws://<host>:<port>` 也受支持，`<host>` 可以是 IP 地址。`ws://` 不加密 Token 和行情；`wss://` 使用 TLS。客户端须使用运维实际提供的协议，不能只修改 URL 前缀就获得 TLS。

`config.json`、私钥和有效 Token 不提交到 GitHub，也不交给浏览器或移动前端。

## 5. 如何生成 JWT Token

Bridge 使用 `RS256`：签发方用 RSA 私钥签名，Bridge 用登记公钥验签。它不是使用共享字符串的 `HS256`，也不是把 JSON 做一次 Base64 编码。

JWT Header：

```json
{"alg":"RS256","typ":"JWT","kid":"example-key"}
```

| Payload 字段 | 类型 | 填写规则 |
| --- | --- | --- |
| `iss` | string | 必须等于运维提供的签发方 |
| `aud` | string | 使用运维提供的唯一接收方 |
| `sub` | string | 稳定的调用服务身份；同一连接续期不能改变 |
| `jti` | string | 每次签发生成新的唯一标识 |
| `iat` | integer | 当前 UTC Unix 秒数 |
| `nbf` | integer | 示例使用当前 UTC Unix 秒数 |
| `exp` | integer | 示例使用 `iat + 120`，不得超过部署配置的寿命上限 |
| `mt5bridge_grant` | object | 授权对象，不能先编码成 JSON 字符串 |
| `mt5bridge_grant.version` | string | 示例使用 `"1"` |
| `referenceReads` | array | 仅包含此次允许读取的来源和账户组 |

以下为完整签名代码，可保存为 `generate_jwt.py`，在参考项目目录运行。签发策略必须预先批准配置中的调用方和账户组；不要直接把未经授权的请求参数复制进授权声明。

```python
import json
from pathlib import Path
import time
import uuid

from cryptography.hazmat.primitives import serialization
import jwt

config_path = Path("config.json").resolve()
config = json.loads(config_path.read_text())
auth = config["auth"]
private_key = serialization.load_pem_private_key(
    (config_path.parent / auth["privateKeyFile"]).read_bytes(),
    password=None,
)

def issue_token():
    now = int(time.time())
    expires_at = now + auth["lifetimeSeconds"]
    payload = {
        "iss": auth["issuer"],
        "aud": auth["audience"],
        "sub": auth["subject"],
        "jti": uuid.uuid4().hex,
        "iat": now,
        "nbf": now,
        "exp": expires_at,
        "mt5bridge_grant": {
            "version": "1",
            "referenceReads": [
                {
                    "sourceId": config["sourceId"],
                    "group": config["group"],
                    "symbol": None,
                }
            ],
        },
    }
    value = jwt.encode(
        payload, private_key, algorithm="RS256",
        headers={"typ": "JWT", "kid": auth["keyId"]},
    )
    return value, expires_at

# 调用方使用返回值建立连接或续期，不把 Token 写入共享日志。
token, expires_at = issue_token()
```

`symbol: None` 编码成 JSON `null`，表示该来源、该账户组的目录及品种读取权限，不是所有来源或所有账户组。它无需列出组内几百个品种。不要使用字符串 `"null"`、空字符串或 `"*"` 替代。

时间字段必须使用秒，不能使用毫秒。默认最大寿命为 120 秒，默认时钟偏差容忍为 15 秒；以部署配置为准。**Token 到达 `exp` 后立即失效，不会额外延长 15 秒。**

每次连接和续期都重新调用签名函数，生成新的时间和 `jti`。不要修改旧 Token 的 `exp` 后直接发送，也不要把固定 Token 永久写进配置。

JWT 签名不加密 Payload，持有 Token 的人可以读取其中的字段。不要在 Payload 中放入私钥、密码等秘密。

## 6. 建立连接并订阅整组品种

接口：

```http
GET /v1/sources/{sourceId}/quotes/stream?group=<URL-encoded-group>
Authorization: Bearer <signed-jwt>
```

完整示例地址：

```text
wss://quotes.example.com/v1/sources/example-source/quotes/stream?group=demo%5Cgroup
```

组名在不同位置的表示不同，但代表同一字符串：

| 位置 | 示例 |
| --- | --- |
| 实际组名 | `demo\group` |
| JSON 字符串 | `"demo\\group"` |
| URL 查询参数 | `demo%5Cgroup` |

`sourceId` 也应作为路径段编码。JWT 中的组名保留原字符串，不做 URL 编码。Token 放在请求头中，不放在 URL 中。

HTTP 升级成功后，立即发送一条 JSON 文本消息：

```json
{"all":true}
```

这条消息只包含 `all`。组名已经在 URL 中，不要再次放进消息，也不要加 `type` 或 `symbols`。全部品种模式不能同时设置 URL 的 `symbol` 参数。初始订阅默认须在 5 秒内发送，消息上限为 64 KiB。

`all` 只表示这个账户组的全部品种。Bridge 先读取组目录、检查授权与容量，再建立订阅，不会静默截断超容量的组。一条连接可以覆盖整个组，无需每个品种建立一条连接。

当前品种集合在连接建立时固定；MT5 后续新增品种，需要主动重连并重新订阅才能纳入。JWT 正常续期不会重读目录。

## 7. 如何接收并保存行情

服务器发送 JSON 文本消息。每条消息首先检查 `type`：

| `type` | 含义 | 处理方式 |
| --- | --- | --- |
| `quote` | 有 Tick 的实时上下文 | 更新该品种最新状态；仍检查时效 |
| `context` | 初始化或过期等上下文状态 | 更新状态；`data` 可能为空，也可能保留最后报价 |
| `authorization` | JWT 续期结果 | 交给续期逻辑，不当作行情解析 |

以下是协议结构示例，价格和时间均为演示值：

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

接收端需落实以下处理：

1. 以 `(sourceId, contextGroup, symbol)` 作为缓存键，每个品种保留最新状态。
2. `bid`、`ask`、`last`、`volume`、`volumeExt`、`flags` 保留字符串；需要计算时使用十进制定点类型。`last` 为 `"0"` 不表示 Bid/Ask 无效。
3. `data: null` 表示当前没有报价数据，不能填成价格 0；存在旧 `data` 也不表示报价仍然实时。
4. `readiness` 为 `initializing`、`ready` 或 `stale`。只有连接有效、该行在当前连接收到过新数据且报价未过期时，才显示为实时。
5. `sequence` 是单个报价上下文的序号，不是全连接序号；不要跨品种比较。`contextVersion` 变化时，按该上下文重新建立状态。
6. `observedAt` 是 Bridge 的 UTC 观察时间。MT5 的 `datetime`、`datetimeMilliseconds` 是服务端交易时间值，UTC 映射未经确认，不应自行当作精确 UTC 时间。

参考客户端用以下公式估算当前年龄，统一使用毫秒：

```text
age = max(0, localUtcNow - observedAt) + (sourceAgeMilliseconds ?? 0)
fresh = connected
        AND rowReceivedOnCurrentConnection
        AND readiness == "ready"
        AND age < maximumDataAgeMilliseconds
```

`sourceAgeMilliseconds` 为 null 表示未知；用 0 只是参考界面的展示估算。时钟误差和传输延迟会影响结果。不要用本机接收时间替换 `observedAt`，否则可能把延迟数据显示为实时。

这是最新状态合并流，不保证每一个 Tick 都送达。`coalescedCount`、`hasGap` 用于表达合并或已知缺口，`isCompleteReplay` 当前为 false。交易所休市、品种没有 Tick 等情况下，部分品种可以长期过期或无报价。

## 8. JWT 到期前如何更新

正常续期使用原 WebSocket，不需要断开、清空缓存或重新发送订阅。

以有效期 120 秒为例，参考客户端通常在签发约 90 秒后开始续期，即预留约 30 秒。真正的调度应依据当前已接受的到期时间，预留获取 Token 和等待确认的时间。

1. 保持接收循环运行，同时重新签发或获取一个新 JWT。
2. 新 JWT 的 `sub` 与当前连接保持一致，且继续覆盖全部已订阅品种。整组订阅须保留该组的 `symbol: null` 授权。
3. 在原 WebSocket 上发送下列消息。`token` 使用原始 JWT 字符串，**不加 `Bearer ` 前缀**。

```json
{"type":"authorization","token":"<new-signed-jwt>"}
```

4. 持续接收行情，同时等待服务端续期结果。下面的时间只是格式示例：

```json
{
  "schemaVersion": "1",
  "type": "authorization",
  "status": "accepted",
  "code": null,
  "expiresAt": "2026-01-01T00:03:30+00:00"
}
```

5. 只有收到 `accepted`，才将当前授权到期时间更新为服务端返回的 `expiresAt`，并安排下一次续期。发送成功不等于授权已经更新。

同一连接最多保留一个等待确认的续期请求。行情与续期确认可能交错到达，不要启动两个竞争读取同一个 Socket 的接收循环。

续期被拒绝的示例：

```json
{
  "schemaVersion": "1",
  "type": "authorization",
  "status": "rejected",
  "code": "caller_mismatch",
  "expiresAt": "2026-01-01T00:02:00+00:00"
}
```

被拒绝后，旧授权的到期时间不变；不能把新 Token 的 `exp` 当成已生效。旧授权仍有效时可按错误类型处理；到期后服务器关闭连接。连续 3 次续期失败会以 `1008 / authorization-renewal-failed` 关闭连接。

参考客户端获取 Token 超时为 30 秒，等待续期确认超时为 10 秒，均受当前授权绝对到期时间约束。这是客户端策略，不是服务端 SLA。续期消息只允许 `type`、`token` 两个字段，完整消息最大 33 KiB，Token 最大 32 KiB，部署可设置更低的 Token 限制。

## 9. 最快跑通完整流程

完成公钥登记和私有配置后，在参考项目目录执行：

```sh
python start.py
```

打开 `http://localhost:8877`，查看品种列表、连接次数、消息数、续期次数和当前授权剩余时间。

`start.py` 会使用项目内的 `TokenIssuer` 自动签发 JWT，再由 `QuoteFeed` 处理连接、整组订阅、持续收取、续期及断线重连。第 5 节的 `generate_jwt.py` 用于理解或移植签名逻辑，启动参考客户端时无需另行运行它。

另一 App 服务端可复用 `QuoteFeed(config, issue_token)`，或按协议实现等价客户端。`issue_token` 是异步函数，返回 `Token(value, expires_at)`；时间使用 UTC Unix 秒数。普通消费者接入已有 Token 服务时，只需替换这个获取函数，接收和续期协议不变。

参考项目中的职责对应：

| 文件或函数 | 用途 |
| --- | --- |
| `config.example.json` | 私有配置模板 |
| `quoteviewer.py` / `TokenIssuer` | 加载受信任私钥并生成 JWT |
| `QuoteFeed.run` | 建立连接、初始订阅、真实断线后重试 |
| `QuoteFeed.run_session` | 并行接收行情和执行连接内续期 |
| `QuoteFeed.observe` / `snapshot` | 保存和读取最新报价状态 |
| `start.py` | 启动 Python 接收器和本机查看页面 |

浏览器只连接本机查看服务，私钥和 JWT 留在 Python 后端。正式 App 由自己的后端将允许展示的报价分发给用户。

## 10. 断线恢复和排错

### 真实断线的处理顺序

1. 立即把连接标记为断开，将缓存报价标记为过期，保留最后数值。
2. 延迟重试并获取新的有效 JWT；多实例部署可采用有上限的退避和随机抖动。参考客户端固定等待 5 秒。
3. 建立一条新 WebSocket，再发送一次 `{"all":true}`。
4. 每个品种只有在新连接收到新鲜数据后，才恢复为实时。

服务端重连后发送当前状态及后续更新，不补发断线期间的全部 Tick。搜索界面应区分「不存在该品种」「该品种暂无实时报价」和「连接断开」。

### 常见故障

| 现象或错误 | 优先检查 |
| --- | --- |
| TCP 连接超时 | 基础地址、端口、云安全组、系统防火墙及监听地址 |
| WebSocket 握手 HTTP 401 | 是否带 Bearer 头、Token 是否过期、公钥是否登记、`kid`、签名算法、`iss`、`aud` 和时间字段 |
| HTTP 403 / `reference_scope_denied` | JWT 中来源和账户组是否匹配；整组订阅是否包含 `symbol: null` |
| HTTP 429 / 503 | 接纳容量或服务暂不可用；不要无限快速重连 |
| 续期 `caller_mismatch` | 新 Token 的 `sub` 是否改变 |
| 续期 `grant_invalid` | 签名、公钥、声明格式和有效期上限 |
| 续期 `grant_expired` | Token 已过期；检查时钟、签发时间和传输延迟 |
| 续期 `grant_revoked` | 向签发方确认撤销状态 |
| 续期 `quote_authorization_limit` / `grant_validation_timeout` / `grant_validation_unavailable` | 续期校验接纳或服务故障；重试不能延长旧授权 |
| `1008 / client-messages-not-supported` | 初始消息和续期消息格式，是否重复订阅或发送未支持的消息 |
| `1008 / grant_expired` | 到期前未完成有效续期 |
| `1008 / authorization-renewal-failed` | 连续 3 次续期失败 |
| `1011 / quote_catalog_unavailable` | 整组目录不可用、为空或不完整 |
| `1008 / quote_subscriber_limit` | 订阅或上下文容量不足 |
| `1008 / slow-consumer-resubscribe` | 消费速度落后于有界流；按断线流程恢复 |
| `1009` | 初始订阅或续期消息超过大小限制 |
| 有品种但没有价格 | `data` 是否为空；该品种是否尚无 Tick |
| 有价格但显示过期 | 市场是否休市、`readiness` 和数据年龄是否满足实时条件 |

排错时记录时间、HTTP 状态、关闭码、业务错误码、连接次数及续期次数。不要记录完整 JWT、Authorization 请求头或私钥。更多关闭原因见[完整协议](market-data-integration.md#6-errors-reconnects-and-delivery-guarantees)。

## 11. 上线前验收清单

1. 不带 JWT 不能订阅；有效 JWT 能建立连接并订阅约定组。
2. 一条连接覆盖该组预期品种数；无报价和休市品种单独计数，不要求每个品种持续变化。
3. 至少观察两次续期，并越过旧 Token 的原到期时间：连接次数不增加，确认到期时间向后推进，活跃品种持续更新，缓存不清空。
4. 在隔离测试环境模拟错误续期和真实断线：错误续期不能延长授权，断线后旧价格保留为过期，重连后逐品种恢复实时。
5. 核对时间单位、价格精度、组名编码和日志脱敏；服务端处理行情时不等待前端渲染完成。

对接完成的判断依据是上述实际结果，而不是仅看到 TCP 已连接、仅发送了新 Token，或仅看到页面上保留的旧报价。
