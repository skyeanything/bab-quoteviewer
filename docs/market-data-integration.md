# Market-data integration guide

This guide describes the ordinary third-party flow: use an independent client ID and secret to obtain a short-lived JWT, then receive all quotes in the assigned MT5 account group over one WebSocket. Bridge supplies and signs the JWT; the partner never receives a signing key.

All addresses and identities in this public document are examples. The operator's **private handoff package** contains actual configuration, the independent credential and any required server trust certificate. Start with [the Chinese handoff guide](partner-onboarding.zh-CN.md) when forwarding the package to a colleague.

## 1. Components and onboarding

```text
App backend -- client ID + secret over HTTPS --> Bridge token endpoint
App backend <-- scoped, short-lived JWT -------- Bridge token endpoint
MT5 --> Bridge -- one authenticated WSS stream --> App backend cache --> App users
```

The receiving backend owns token acquisition, renewal and the latest-value cache. QuoteViewer implements this in Python and adds a local browser table. Install Python 3.11 or later; no SSH access, .NET SDK, MT5 terminal or Manager SDK is required.

The operator must supply these files through a private channel:

| File/value | Purpose |
| --- | --- |
| `config.json` | Actual `bridgeUrl`, `auth.tokenUrl`, `sourceId`, `group`, `auth.clientId` and credential/certificate paths |
| `secrets/client-secret.txt` | Unique, randomly generated client secret; store only on the receiving backend |
| `certs/bridge-ca.pem` | Public trust certificate for a private-CA deployment; no private key |
| Client code and dependencies | `quoteviewer.py`, launchers, `requirements.txt`, browser files and guides |

`sourceId` identifies the configured MT5 source. `group` is the exact MT5 **account/pricing group**, not an asset category such as metals or equities. Names are case-sensitive and retain backslashes/suffixes. The server binds the client credential to one source and group; editing the local config does not change permission.

The server supplies `iss` (issuer), `aud` (audience), `kid` (signing-key ID), `sub` (stable client identity), timestamps and the grant. A partner does not need to request these values, generate them or implement JWT signing. Treat the returned token as a bearer credential.

## 2. Obtain and refresh a JWT

### Request

```http
POST /v1/quote-tokens HTTP/1.1
Authorization: Basic <base64(client_id:client_secret)>
Content-Type: application/x-www-form-urlencoded

grant_type=client_credentials
```

| Item | Contract |
| --- | --- |
| URL | Full operator-supplied HTTPS token URL; path `/v1/quote-tokens` |
| Authentication | HTTP Basic; UTF-8 `client_id:client_secret`, then Base64 |
| `client_id` | Operator-assigned identifier, at most 64 ASCII letters/digits or `-_.` |
| `client_secret` | Operator-supplied random secret; never a JWT or signing key |
| Body | Exactly one field: `grant_type=client_credentials` |
| `sourceId`, `group`, `scope`, `iss`, `aud`, `sub` | Do not send; additional form fields are rejected |
| TLS | Required; validate the certificate chain and DNS name or IP SAN |

Base64 is not encryption. The Python provider requires HTTPS for credentials and WSS for quotes, follows no token-endpoint redirects, and trusts the supplied CA file while retaining hostname/IP validation. Do not disable certificate verification. IP-and-port access works when the certificate contains that IP in its SAN.

### Success: HTTP 200

```json
{
  "access_token": "<signed-compact-jwt>",
  "token_type": "Bearer",
  "expires_in": 120,
  "expires_at": 1790000120
}
```

The timestamp above is illustrative; use each actual response.

| Field | Type and meaning |
| --- | --- |
| `access_token` | String; fresh RS256 JWT |
| `token_type` | String; `Bearer` |
| `expires_in` | Integer seconds; default/configured deployment lifetime is 120 seconds |
| `expires_at` | Integer Unix UTC seconds; absolute expiration, not milliseconds |

Responses use `Cache-Control: no-store` and `Pragma: no-cache`. Do not log the secret, Authorization header or JWT. Keep host clocks synchronized.

There is **no refresh token**. Before expiry, repeat the same credential-authenticated POST to obtain a new JWT. Each issuance has a new `jti` and timestamps, with the same configured caller and group scope. Reusing a JWT after expiry is rejected; editing its payload invalidates its signature.

### Errors and rate limit

| HTTP status | Body/code | Action |
| --- | --- | --- |
| 400 | `{"error":"https_required"}` | Use the supplied HTTPS endpoint |
| 400 | `{"error":"invalid_request"}` | Check form content type and the single `grant_type` field |
| 401 | `{"error":"invalid_client"}` | Check the supplied client ID/secret or ask the operator to check enabled status; response has a Basic authentication challenge |
| 429 | `{"code":"capacity_limit_reached","workClass":null}` | Back off; do not run a tight retry loop |
| 404 | Endpoint unavailable | Confirm URL and whether issuance is enabled in the deployed version |
| 5xx / network failure | Service failure | Retry with delay while the current token remains valid; preserve stale quotes if it expires |

Default token endpoint capacity is **60 requests per minute across the endpoint**, using fixed windows and no queue. This is configurable, not a per-client allowance. There is no `Retry-After` contract. One receiving backend normally requests a token about every 90 seconds with a 120-second token; do not create a token request per symbol or per App user.

### Permission boundary and credential changes

The issued grant contains only one `referenceReads` entry for the server-configured source/group, with `symbol: null`. This allows that group's catalog, symbol metadata and quotes. It does not authorize another source/group, source-wide catalog access, account reads, trading, funding or account changes. The token endpoint accepts no client-supplied grant.

The operator owns signing keys and the client registry. It stores SHA-256 of the high-entropy secret, compares it in constant time and derives the stable caller as `quote-client:<client_id>`. JWT signing uses the configured issuer/audience and a registered RSA key. Partners do not access these settings.

For a secret rotation, the operator replaces the registered hash and delivers a replacement secret privately. For disablement, the operator disables the client. These configuration changes require a Bridge restart in this deployment; the client reconnects afterward. Existing signed JWTs are not independently revoked merely by changing the secret: they remain cryptographically valid until their original expiry, at most the configured lifetime. Do not promise instantaneous JWT revocation. The Python provider reads the secret at process startup; restart it after replacing the secret file.

The operator renews the server certificate before expiration. A leaf certificate signed by the same CA does not require a new trust file; changing the CA requires a new operator-supplied certificate. Never work around expiration or trust failure by disabling TLS verification.

### Complete Python receiving example

The packaged viewer already executes this flow through `python start.py`. For a backend without the browser, run the following from the package directory. It reuses the same token acquisition, renewal and reconnect code:

```python
import asyncio
import json
from pathlib import Path
import ssl

from quoteviewer import ClientCredentialsTokenProvider, QuoteFeed

class BackendFeed(QuoteFeed):
    def observe(self, value, raw):
        super().observe(value, raw)
        # 在此更新业务缓存；耗时处理应交给有界队列，避免阻塞接收。
        print(json.dumps(value, ensure_ascii=False), flush=True)

async def main():
    config_path = Path("config.json").resolve()
    config = json.loads(config_path.read_text())
    ca_file = config.get("tlsCaFile")
    tls = ssl.create_default_context(
        cafile=str(config_path.parent / ca_file) if ca_file else None
    )
    tokens = ClientCredentialsTokenProvider(config, config_path.parent, tls)
    feed = BackendFeed(config, tokens, tls)
    await feed.run()

asyncio.run(main())
```

Authorization acknowledgments are handled by `QuoteFeed`; `observe` receives only quote/context messages. The example prints market data, never JWTs. Production consumers should choose their own logging volume and cache/distribution behavior. The in-memory cache survives a network reconnect, not a process restart; persistence and tick replay are not provided by this client.

## 3. WebSocket URL and initial subscription

The complete example URL is:

```text
wss://quotes.example.com/v1/sources/example-source/quotes/stream?group=demo%5Cgroup
```

Build it from the actual base URL and **URL-encode** the source path segment and group query value. A JSON group `"demo\\group"` represents one backslash; its URL encoding is `demo%5Cgroup`. Do not URL-encode the group inside the JWT.

An IP-and-port deployment uses the same path and query with the operator's base URL, `wss://<host>:<port>`. No domain name or SSH connection is required. Replace both placeholders with the privately supplied values.

Send this header in the WebSocket upgrade request:

```http
Authorization: Bearer <fresh-signed-jwt>
```

Use a backend WebSocket client that supports request headers. Do not put JWTs in URL query parameters. This third-party flow requires WSS. Load the supplied CA file for a private-CA endpoint; keep certificate and host validation enabled.

After upgrade, immediately send one UTF-8 JSON text message:

```json
{"all":true}
```

The `group` query parameter is required for this mode; omit the `symbol` query parameter. This means **all symbols in that one group**, not all groups. No symbol array is needed.

The initial subscription deadline defaults to 5 seconds (`Capacity:Quotes:MaximumAuthorizationDelay`). The initial message is limited to 64 KiB. Fragmentation does not reset the size or time limit. Only the exact supported subscription shape is accepted; do not add `group`, `type` or a second subscription mode to this message.

Bridge reads the complete MT5 group catalog, validates the full subscription and applies capacity limits before subscribing. It rejects an empty, unavailable or partial catalog and refuses over-capacity groups as a whole. It does not silently truncate the group. The default context limit is 4,096, subject to deployment configuration.

Each connection consumes one subscriber slot, while each symbol also consumes its own context subscription capacity. There is no need to create one WebSocket per symbol.

The symbol set is fixed for the life of the connection. Symbols later added to MT5 require a deliberate reconnect and a new `{"all":true}` subscription. JWT renewal does not reread the catalog or change the subscription. Group membership in the catalog does not guarantee a current tick: some markets are closed or have never produced data.

### Other supported modes

| Mode | URL query | Initial application message |
| --- | --- | --- |
| All group symbols | `group=<encoded-group>`; no `symbol` | `{"all":true}` |
| Explicit symbols | Optional `group`; no `symbol` | `{"symbols":["EURUSD","XAUUSD"]}` |
| Single symbol | `symbol=<encoded-exact-name>` and optional `group` | None; receive quotes directly |

In explicit-symbol mode, the array must be nonempty and every symbol must be authorized. Names retain their MT5 suffixes, are at most 256 characters and cannot contain a NUL character. Exact duplicates are deduplicated. A single unauthorized member rejects the entire set. Do not send a subscription frame after subscribing via the `symbol` URL parameter.

## 4. Incoming quote and context messages

Messages are JSON text. Dispatch by `type` **before** reading quote-specific fields, because authorization acknowledgments do not contain a symbol or price.

Illustrative quote message:

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

The example values are not market data or a statement that MT5 time maps to UTC.

| Field | Consumer behavior |
| --- | --- |
| `schemaVersion` | Validate supported schema version; currently `"1"` |
| `type` | `quote` is ready with a tick; `context` conveys initializing/stale state and may still contain the last tick |
| `sourceId`, `contextGroup`, `symbol` | Use this tuple as the cache key; verify the expected source/group |
| `contextVersion` | Opaque version for that quote context; handle a changed version as a context reset |
| `sequence` | Signed 64-bit per-context sequence, not a global connection sequence; do not compare different symbols |
| `readiness` | `initializing`, `ready` or `stale`; non-null `data` alone does not mean fresh |
| `observedAt` | Bridge observation time in UTC, not the receiving browser's time |
| `sourceAgeMilliseconds` | Source-age anchor at observation, or `null` when unknown; not the age at transmission |
| `maximumDataAgeMilliseconds` | Maximum permitted quote age; default 5,000 milliseconds, read the actual message |
| `coalescedCount` | Intermediate updates coalesced for this delivery |
| `hasGap` | A known gap; do not treat the stream as complete tick history |
| `isCompleteReplay` | Currently always `false` |
| `data` | Tick object or `null`; preserve a distinct no-quote state |

`bid`, `ask`, `last`, `volume`, `volumeExt` and `flags` are strings. Preserve their precision and formatting; use a decimal-capable type for arithmetic. `sourceDailyChangePercent` is optional/nullable source-level daily change and is independent of group pricing. A zero last price does not by itself invalidate bid/ask quotes.

Parse integer sequence and time fields without losing 64-bit precision. MT5 `datetime` and `datetimeMilliseconds` are server trading-time values whose UTC mapping is unverified; use `observedAt` for transport/freshness observations and do not silently reinterpret native timestamps.

For a UI, a fresh row requires an active connection, a row received on that connection, `readiness == "ready"`, and an age below the supplied maximum. QuoteViewer estimates age as:

```text
max(0, localUtcNow - observedAt) + (sourceAgeMilliseconds ?? 0)
```

Use milliseconds throughout. A null source age is unknown, not proof that the source age is zero. The fallback above is the viewer's display estimate. Local clock error and network delay affect the estimate; never replace `observedAt` with local receive time to make delayed data look fresh.

## 5. Renew JWTs on the existing connection

A new JWT must have the same `sub`/caller ID and cover **every active subscribed context**. An all-group stream continues to require group-catalog scope (`symbol: null`); replacing it with a handful of symbol grants is a scope downgrade and is rejected.

Before the active authorization expires:

1. Repeat `POST /v1/quote-tokens` with the same client credential while the receive loop keeps running.
2. Send the following on the existing WebSocket. Send the raw token without the `Bearer ` prefix.
3. Wait for the authorization acknowledgment while continuing to process quotes.
4. Only after `accepted`, replace the active expiry with the server's `expiresAt` and schedule the next renewal.

```json
{"type":"authorization","token":"<fresh-signed-jwt>"}
```

Successful acknowledgment, with an illustrative expiry:

```json
{"schemaVersion":"1","type":"authorization","status":"accepted","code":null,"expiresAt":"2026-01-01T00:02:00+00:00"}
```

There is no resubscription, cache reset or connection replacement on successful renewal. Quotes and acknowledgments share the socket and can arrive interleaved. An accepted acknowledgment proves the replacement credential was installed; merely sending the message does not.

The client message permits only `type` and `token`, without extra or duplicate properties, at a maximum JSON depth of 4. The token must be nonblank, contain no NUL and fit the configured token limit (at most 32 KiB). The complete renewal message must fit 33 KiB. Fragmentation does not reset the size bound. Keep at most one renewal pending per connection.

QuoteViewer starts renewal up to 30 seconds before expiry (one third of the remaining lifetime for shorter credentials). The HTTP token request has a 15-second timeout, the provider call is bounded by 30 seconds and acknowledgment timeout is 10 seconds, both bounded by the active authorization expiry. The active expiry is updated only after a successful server acknowledgment. These are **client policies**, not a Bridge SLA. Size the lead time for the actual token-service and network latency.

### Rejection behavior

```json
{"schemaVersion":"1","type":"authorization","status":"rejected","code":"caller_mismatch","expiresAt":"2026-01-01T00:02:00+00:00"}
```

On rejection, `expiresAt` remains the old authorization's expiry. A failure does not extend permission or install the new token. If the old grant has already expired or reached its revocation-freshness boundary, the server closes the connection instead of granting additional time. Three consecutive failed renewals close with `1008 / authorization-renewal-failed`; a successful renewal resets that counter.

| Rejection code | Check |
| --- | --- |
| `grant_invalid` | Signature, algorithm, key ID, issuer/audience, timestamps and grant shape |
| `grant_expired` | Issue a fresh token; check issuer/Bridge clocks and transit delay |
| `grant_revoked` | Resolve the issuer/operator's revocation decision |
| `caller_mismatch` | Keep `sub` identical to the existing caller |
| `reference_scope_denied` | Cover every active context; retain catalog scope for all-group mode |
| `quote_authorization_limit` | Authorization admission is busy; retry only while the old grant remains valid |
| `grant_validation_timeout` | Credential validation exceeded its budget |
| `grant_validation_unavailable` | Credential validation is unavailable |

Do not retry the same permanent caller/scope error indefinitely. QuoteViewer reports rejection and retries after 5 seconds while the old authorization remains valid; the server's failure limit still applies. An App backend should classify permanent and transient failures according to its token service. Never label an expired/disconnected connection as live.

## 6. Errors, reconnects and delivery guarantees

Before upgrade, failures use HTTP status codes and authorization/error JSON. Missing/invalid/expired credentials can return `401`, scope denial `403`, admission limits `429`, and unavailable validation or quote service `503`. A single-symbol subscriber-capacity failure also uses `503`. Capture the response status and safe error code, without logging the Authorization header.

After upgrade, handle the close code and reason separately from application messages:

| Close code / reason | Meaning and response |
| --- | --- |
| `1008 / grant_expired` | No valid authorization remained; obtain fresh permission and reconnect |
| `1008 / grant-no-longer-authorized` | The active grant no longer authorizes the stream |
| `1008 / authorization-renewal-failed` | Three consecutive renewal failures |
| `1008 / client-messages-not-supported` | Unsupported/malformed client frame; verify mode, schema and message order |
| `1009 / authorization-renewal-too-large` | Renewal message exceeds the size bound |
| `1008 / reference_scope_denied` | Requested subscription lacks group/symbol scope |
| `1008 / quote_subscriber_limit` | Subscription/context capacity is exhausted |
| `1011 / quote_catalog_unavailable` | All-group catalog is empty, partial or unavailable |
| `1008 / slow-consumer-resubscribe` | The consumer fell behind the bounded stream |
| `1001 / quote-stream-stopped` | The stream stopped; restore service/connectivity before resubscribing |
| `1011 / grant-refresh-capacity`, `grant-refresh-timeout`, `grant-refresh-failed` | The server could not revalidate the active credential |

An oversized initial subscription also closes with `1009`; failing to send the first subscription within its deadline can abort the connection. Network loss can end the connection without a close frame. Record socket errors as well as server close reasons.

For a genuine disconnect:

1. Mark the connection and cached prices stale immediately. Preserve the last values for display, with an explicit disconnected/recovering indicator.
2. Retry with a delay and obtain a fresh authorized token. In a fleet of consumers, use bounded backoff and jitter to avoid simultaneous reconnects; QuoteViewer uses a fixed 5-second delay as a local diagnostic tool.
3. Open one new connection and submit the subscription again.
4. Restore live status per symbol only after fresh data arrives on the new connection.

The stream is a **latest-value coalescing stream**. A slow consumer can miss intermediate updates even while connected. The server sends current context state and subsequent updates after reconnect; it does not replay every tick from the outage. `sequence`, `hasGap` and `coalescedCount` do not create a replay cursor or a lossless delivery promise.

## 7. Integration acceptance

Before enabling the App's users, verify the following with the operator-approved group:

1. One connection accepts `{"all":true}` and supplies the expected catalog; record no-data and closed-market symbols separately.
2. The connection survives at least two renewal cycles **and the earlier tokens' original expiry times**, with advancing server-acknowledged expiries and continuing quote delivery.
3. In an isolated test environment, a bad renewal does not extend expiry or change the authorized caller/scope. The server rejects unsupported messages and enforces failure/size bounds.
4. A genuine disconnect leaves cached values visible as stale. A reconnect reauthorizes, resubscribes and restores each symbol only as new data arrives.
5. Search results distinguish an unknown symbol, a known symbol without a fresh quote, and a disconnected feed.

Observe connection count, accepted/rejected renewals, remaining authorization lifetime, quote rate, freshness, close codes and context resets. Never include JWTs, private keys, private endpoints or raw environment configuration in public diagnostics.
