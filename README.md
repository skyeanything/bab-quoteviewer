# BAB QuoteViewer

A **Python client that connects directly to MT5 Bridge over WS/WSS**. It is an integration example for another App's backend and includes a local browser table for inspecting live prices.

One WebSocket subscribes to all symbols in one authorized MT5 account group. JWTs renew on the same connection. The client needs **Python 3.11 or later** and the packages in `requirements.txt`. It does not use SSH, remote commands, .NET, the MT5 terminal or the Manager SDK.

Read the [market-data integration guide](docs/market-data-integration.md) for JWT claims, subscription frames, acknowledgments, freshness and error handling.

For a colleague handoff in Chinese, use the [step-by-step integration handbook](docs/partner-onboarding.zh-CN.md), including the prerequisites, responsibilities, key registration, token generation, renewal and quote handling.

For JWT setup, see [who signs the token](docs/market-data-integration.md#who-issues-the-jwt), [how to register the public key](docs/market-data-integration.md#register-a-signing-key-once), and [the complete Python signing example](docs/market-data-integration.md#generate-a-jwt-in-python). Bridge verifies JWTs but does not provide a token-issuance endpoint. A registered signing key grants issuer trust; it is not a partner API key restricted to the sample group.

## What the operator must supply

- A reachable WebSocket base URL, such as `wss://quotes.example.com`, or `ws://<host>:<port>` for an operator-provided HTTP listener. `<host>` can be an IP address.
- The configured `sourceId` and exact MT5 account-group name.
- Agreed JWT issuer, audience, key ID and stable caller ID.
- A signing arrangement: the operator must already trust the RSA public key used by this backend, or your backend must obtain tokens from an existing authenticated issuer.

A loopback-only Bridge listener is not an external integration endpoint. Confirm the supplied listener is reachable from the consumer's network before treating this example as an end-to-end deployment acceptance. `ws://` carries JWTs and quotes without transport encryption; `wss://` uses TLS. All values in this repository are placeholders; request real deployment details privately.

## Run

Use a Python executable that is version 3.11 or newer; some operating systems ship an older `python3`.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp config.example.json config.json
```

On Windows, activate the environment with `.venv\Scripts\Activate.ps1` and copy the configuration with `Copy-Item`.

Edit `config.json`:

| Setting | Meaning |
| --- | --- |
| `bridgeUrl` | Direct `ws://` or `wss://` base URL, including the port when needed, without `/v1/...` |
| `sourceId` | Authorized MT5 source identifier |
| `group` | Exact account/pricing group, not an asset category |
| `httpPort` | Local viewer port, default 8877 |
| `auth.issuer`, `auth.audience`, `auth.keyId` | Values registered with Bridge |
| `auth.subject` | Stable backend caller ID; kept unchanged on renewal |
| `auth.privateKeyFile` | Protected RSA private-key path; relative to the configuration file |
| `auth.lifetimeSeconds` | Token lifetime, default 120; must fit the operator's configured maximum |

The included `TokenIssuer` signs an all-group, quote-read grant. Use it only inside an authorized backend. An arbitrary newly generated key will not be accepted by Bridge. Keep private keys out of the browser and repository; `config.json`, `*.local.json`, private-key files and `secrets/` are ignored by Git.

```sh
python start.py
```

Open [the local viewer](http://localhost:8877). To use an external configuration file:

```sh
python start.py --config /path/to/private-config.json
```

Prices remain strings. Normal JWT renewal keeps both the connection and cache. A genuine disconnect retains prices as stale; each row becomes live again only when the new connection receives fresh data. The stream coalesces updates and does not replay all missed ticks.

## Use an existing token service

`QuoteFeed(config, issue_token)` accepts an async callable that returns `Token(value, expires_at)`, where `expires_at` is Unix UTC seconds. Replace `TokenIssuer` with your backend's existing authenticated token acquisition code. Bridge does not define a public token endpoint; do not invent one or execute commands on the Bridge server to obtain credentials.

The browser uses only the local `/api/state` and `/events` interfaces. JWTs and signing keys remain in the Python process. Runtime screens contain the configured endpoint and quote data, so review screenshots before sharing.

## Review and verification

```sh
python -m unittest discover -s tests -p 'test_*.py' -v
node --test tests/ui.test.mjs
```

Node is optional and only runs the existing UI regression test; it is not needed to run QuoteViewer. Python tests use a local WebSocket fixture with real RS256 signatures to check direct header authentication, group subscription, two token expiry boundaries, rejection without extending permission, and stale-cache recovery after a TCP disconnect. These tests do not certify the operator's public ingress or replace testing against a deployed Bridge.

For live acceptance, observe at least two JWT renewals and the earlier tokens' original expiry times. Connection count must remain one, acknowledged expiry must advance, and active symbols must keep receiving updates. Closed markets and symbols without ticks can legitimately be stale or empty.

| File | Purpose |
| --- | --- |
| `quoteviewer.py` | RS256 issuer, direct WebSocket client, cache and local HTTP/SSE adapter |
| `start.py` | Python entry point |
| `config.example.json` | Placeholder-only connection and JWT configuration |
| `wwwroot/index.html` | Existing browser table and session status |
| `tests/test_client.py` | Direct-connection protocol regression tests |
| `docs/market-data-integration.md` | Detailed backend integration contract |

Implementation references: [aiohttp client WebSockets](https://docs.aiohttp.org/en/stable/client_quickstart.html#websockets) and [PyJWT RS256](https://pyjwt.readthedocs.io/en/stable/usage.html#encoding-decoding-tokens-with-rs256-rsa).
