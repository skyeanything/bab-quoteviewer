# BAB QuoteViewer

A repository containing the market-data API reference and an optional Python diagnostic viewer.

To integrate into an existing backend, use the [Chinese API reference](docs/partner-onboarding.zh-CN.md) or [English API reference](docs/market-data-integration.md). They define the HTTP/WebSocket protocol without depending on this viewer. The operator supplies the API document, assigned connection parameters, client secret and CA certificate privately.

The remaining instructions are for running the optional diagnostic viewer.

## Diagnostic viewer configuration

The diagnostic viewer reads these private files:

| File | Purpose |
| --- | --- |
| `config.json` | Actual HTTPS/WSS endpoint, source, permitted group and client ID |
| `secrets/client-secret.txt` | Independent client secret, kept on the receiving backend |
| `certs/bridge-ca.pem` | Server trust certificate when the endpoint uses a private CA |

The credential is bound to one source/group on the server. Changing `config.json` does not grant access to another group. The partner does not generate signing keys or choose `iss`, `aud`, `kid` or `sub`; Bridge fills and signs those fields. No account, trading or funding permission is issued.

This public repository contains placeholders only. A public clone by itself cannot connect to a private deployment. Do not commit the private package, credentials or actual server certificate.

## Run

Install **Python 3.11 or later**, extract the private package, then run from its directory:

macOS / Linux:

```sh
sh start.sh
```

Windows:

```bat
start.cmd
```

The launcher creates `.venv`, installs the pinned dependencies and runs the client. First use requires access to the Python package index. It does not install Python itself. No SSH, .NET SDK, MT5 terminal or Manager SDK is needed.

Open [the local viewer](http://localhost:8877). Leave the Python process running. Stop it with Ctrl+C. The page displays connection count, renewal count, latest quotes and freshness; JWTs and client secrets remain in the Python process.

Manual setup:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python start.py
```

On Windows, use `.venv\Scripts\python.exe`. An external configuration can be supplied with `python start.py --config /path/to/config.json`.

## Configuration

| Setting | Meaning |
| --- | --- |
| `bridgeUrl` | Direct `wss://` base URL, including the supplied port; no API path |
| `sourceId` | Exact source assigned by the operator |
| `group` | Exact MT5 account/pricing group, not an asset category |
| `httpPort` | Local viewer port; default 8877 |
| `tlsCaFile` | Trust certificate path, relative to config; `null` uses system trust |
| `auth.tokenUrl` | Full HTTPS URL of `POST /v1/quote-tokens` |
| `auth.clientId` | Independent identifier assigned by the operator |
| `auth.clientSecretFile` | Secret file path, relative to config |

The example config uses a placeholder domain. An IP and port work with a matching IP certificate in the operator's private package. Certificate and host verification remain enabled.

## Client behavior and reuse

`ClientCredentialsTokenProvider` calls the token API with HTTP Basic authentication and `grant_type=client_credentials`. `QuoteFeed` authenticates the WebSocket with the resulting bearer token and sends `{"all":true}`. Before expiry, it requests another token and sends an authorization frame on the same socket. Only an accepted acknowledgment updates the active expiry.

Normal renewal retains the connection and cache. A genuine disconnect keeps the latest prices marked stale and retries after five seconds. Freshness returns per symbol only after new data arrives. The cache is in memory; stopping the Python process clears it. The stream coalesces updates and does not replay every missed tick.

For another backend, reuse `ClientCredentialsTokenProvider` and `QuoteFeed`; the integration guide includes a complete receiving example without the browser. The legacy `TokenIssuer` remains for existing operator-controlled configurations and test fixtures. It is not used by the partner package and requires an operator-held signing key.

## Verification

```sh
python -m unittest discover -s tests -p 'test_*.py' -v
node --test tests/ui.test.mjs
```

Node is only needed for the existing UI test. Python tests use verified local HTTPS/WSS, real RSA signatures, credential-based token issuance, two expiry boundaries, rejected renewal and cache recovery. Live deployment acceptance must additionally check the actual network endpoint, fixed group scope and ongoing quotes across token expiry.
