# Market-data integration guide

This guide describes how another App's **backend** authenticates to MT5 Bridge and receives all quotes in an authorized account group over one WebSocket. It covers the protocol with in-connection JWT renewal. Older Bridge builds that reject authorization messages must be upgraded before using this client.

Every address, identity, group, key ID and price below is an example. Obtain actual deployment values through your private onboarding channel. This repository contains no production endpoint or signing key.

## 1. Components and onboarding

```text
MT5 → Bridge → one backend WebSocket → App backend cache → App users
                             ↑
                short-lived JWTs from a trusted issuer
```

The receiving backend owns the connection, token renewal and latest-value cache. It can distribute its authorized prices to its own clients. QuoteViewer implements the receiving side and adds a local browser table for verification.

Agree on these values with the Bridge operator before connecting:

| Value | Meaning |
| --- | --- |
| WebSocket base URL | The reachable `wss://` endpoint; TLS/ingress is configured by the operator |
| `sourceId` | An opaque identifier for one configured MT5 source; it is not an account login or symbol |
| `group` | The exact MT5 account-group name used for group pricing |
| `iss` and `aud` | The issuer and audience registered in Bridge |
| `kid` and RSA public key | The trusted signing-key identifier and verification key |
| `sub` | A stable identity for the calling backend |
| Lifetime and limits | The deployment's JWT lifetime, clock skew and quote capacity settings |

A group is the account/pricing group, not an asset category such as metals or equities. Source IDs, groups and symbols are matched exactly, including case and symbol suffixes.

Bridge validates credentials; this protocol does **not** define a public login or token-issuance endpoint. A trusted signing backend signs JWTs, or an existing authenticated token service supplies them to the consumer. Never expose a signing key or a general-purpose token issuer to the App browser. Keep signing grants limited to the approved source/group.

## 2. JWT authentication rules

### Signing and verification

Use a signed compact JWT with RSA SHA-256 and PKCS#1 v1.5 padding (`RS256`). Bridge requires an RSA public key of at least 2048 bits. The private key stays with the authorized issuer; Bridge stores only the public verification key.

Header:

```json
{"alg":"RS256","typ":"JWT","kid":"example-key"}
```

| Header field | Required rule |
| --- | --- |
| `alg` | Exactly `RS256`; unsigned tokens and other algorithms are rejected |
| `typ` | Exactly `JWT` |
| `kid` | Nonblank string, at most 256 characters; must match a configured verification key |

Payload construction, with current Unix UTC seconds:

```javascript
const now = Math.floor(Date.now() / 1000);
const payload = {
  iss: "example-backend",
  aud: "example-bridge",
  sub: "example-quote-client",
  jti: crypto.randomUUID(),
  iat: now,
  nbf: now,
  exp: now + 120,
  mt5bridge_grant: {
    version: "1",
    referenceReads: [
      { sourceId: "example-source", group: "demo\\group", symbol: null }
    ]
  }
};
```

This block shows claim construction, not JWT signing. The [runnable C# issuer](../examples/TokenIssuer/Program.cs) signs these claims and returns a fresh token to QuoteViewer.

| Claim | Required rule |
| --- | --- |
| `iss` | Exact configured issuer |
| `aud` | Exactly one audience matching the configured audience; a string or a single-element string array |
| `sub` | Nonblank caller ID, at most 256 characters; becomes Bridge's `CallerId` |
| `jti` | Nonblank token ID, at most 256 characters; issue a new ID for each token |
| `iat`, `nbf`, `exp` | Integer Unix UTC seconds, not milliseconds or strings |
| `mt5bridge_grant` | A JSON object, not a JSON-encoded string |
| `mt5bridge_grant.version` | A nonblank grant-version string; examples use `"1"` |
| `referenceReads` | An array of explicitly authorized source/group/symbol scopes |

Time validation is strict:

- `exp > iat`, `nbf <= exp` and `exp - iat <= MaximumTokenLifetime`.
- `iat` and `nbf` may be ahead of the verifier's clock by at most `ClockSkew`.
- `iat - nbf <= ClockSkew`.
- **`exp <= now` is expired immediately. Clock skew does not grant extra time after expiry.**
- Defaults: maximum lifetime 120 seconds and clock skew 15 seconds. Configuration ceilings are 300 seconds and 30 seconds respectively. A deployment can impose shorter values.

Keep issuer and Bridge clocks synchronized. Do not solve expiry by issuing a day-long token: the configured maximum lifetime is enforced.

JWTs default to a maximum of 32 KiB of UTF-8 bytes; a deployment can set a lower limit. Authorization items across the grant are limited to 1,024. Optional grant arrays can be omitted and then default to empty, but cannot be `null`. Duplicate JSON properties, unsupported grant fields and invalid grant shapes are rejected. No account or trading permission is needed for the quote-only grant above.

The validator can report revoked credentials when its revocation store rejects a `jti`. This integration does not define an online revocation API or promise immediate external revocation propagation. Short-lived tokens and absolute expiry remain authorization boundaries.

### Group and symbol scope

For all symbols in one group, include this single grant item:

```json
{"sourceId":"example-source","group":"demo\\group","symbol":null}
```

`symbol: null` grants that group's catalog and symbols. It avoids enumerating hundreds of symbols in the JWT. It is not a wildcard for other groups or sources. Do not send the string `"null"`, an empty string or `"*"` instead of JSON `null`.

A grant item with an exact symbol can authorize that group/symbol combination, but cannot authorize `{"all":true}`. Source-level symbol access uses `group: null` and the exact symbol; it does not authorize group pricing. Keep the endpoint's source and group identical to the token scope.

For key rotation, the operator must trust the replacement public key before tokens signed with its `kid` are used. A successful renewal still requires the same caller ID and all active scopes.

## 3. WebSocket URL and initial subscription

The complete example URL is:

```text
wss://quotes.example.com/v1/sources/example-source/quotes/stream?group=demo%5Cgroup
```

Build it from the actual base URL and **URL-encode** the source path segment and group query value. A JSON group `"demo\\group"` represents one backslash; its URL encoding is `demo%5Cgroup`. Do not URL-encode the group inside the JWT.

Send this header in the WebSocket upgrade request:

```http
Authorization: Bearer <fresh-signed-jwt>
```

Use a backend WebSocket client that supports request headers. Do not put JWTs in URL query parameters. Use WSS for traffic outside a protected local transport; this client does not provision TLS or expose a Bridge listener.

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

### Minimal .NET connection example

```csharp
using System.Net.WebSockets;
using System.Text;

using var socket = new ClientWebSocket();
socket.Options.SetRequestHeader("Authorization", "Bearer " + jwt);
var url = bridgeBaseUrl.TrimEnd('/')
    + "/v1/sources/" + Uri.EscapeDataString(sourceId)
    + "/quotes/stream?group=" + Uri.EscapeDataString(group);
await socket.ConnectAsync(new Uri(url), cancellationToken);
await socket.SendAsync(Encoding.UTF8.GetBytes("{\"all\":true}"),
    WebSocketMessageType.Text, true, cancellationToken);
// 持续接收并按 type 分发；在旧授权到期前，通过同一连接发送续期消息。
```

This excerpt establishes the subscription only. Use [QuoteFeed.cs](../QuoteFeed.cs) for a complete receive loop, fragmented-message assembly, concurrent token acquisition, renewal acknowledgments and reconnect handling. Keep one receiver and serialize application sends. Do not block quote reception while obtaining a new token.

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

1. Obtain a fresh token while the receive loop keeps running.
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

QuoteViewer starts renewal up to 30 seconds before expiry (one third of the remaining lifetime for shorter credentials). Its token-provider timeout is 30 seconds and acknowledgment timeout is 10 seconds, both bounded by the active authorization expiry. These are **client policies**, not a Bridge SLA. Size the lead time for the actual token-service and network latency.

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
