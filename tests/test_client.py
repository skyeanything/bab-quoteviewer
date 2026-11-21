import asyncio
import contextlib
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import socket
import ssl
import tempfile
import time
import unittest

from aiohttp import ClientSession, WSMsgType, encode_basic_auth, web
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
import jwt

from quoteviewer import ClientCredentialsTokenProvider, QuoteFeed, TokenIssuer, create_app, utc_text


class DirectConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="quote-client-test-")
        root = Path(self.temp.name)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        (root / "key.pem").write_bytes(key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        self.public_key = key.public_key()
        self.config = {
            "bridgeUrl": "", "sourceId": "example-source", "group": "demo\\group",
            "auth": {"issuer": "example-issuer", "audience": "example-bridge",
                     "subject": "example-caller", "keyId": "example-key",
                     "privateKeyFile": "key.pem", "lifetimeSeconds": 6},
        }
        self.connections = 0
        self.hold = False
        self.active = None
        self.transport = None
        self.claims = []
        self.token_requests = 0
        self.server_ssl, self.client_ssl = self.tls_contexts(root, key)
        server = web.Application()
        server.router.add_get("/v1/sources/example-source/quotes/stream", self.stream)
        server.router.add_post("/v1/quote-tokens", self.token)
        self.bridge, url = await self.listen(server, self.server_ssl)
        self.config["bridgeUrl"] = url.replace("https:", "wss:")
        self.issuer = TokenIssuer(self.config, root)
        (root / "client-secret.txt").write_text("synthetic-client-secret")
        self.config["auth"] = {"tokenUrl": url + "/v1/quote-tokens", "clientId": "example-client",
                               "clientSecretFile": "client-secret.txt"}
        self.feed = QuoteFeed(self.config, ClientCredentialsTokenProvider(self.config, root, self.client_ssl), self.client_ssl)
        self.viewer, self.viewer_url = await self.listen(create_app(self.feed))
        await self.until(lambda: self.feed.snapshot()["ready"] == 2)

    async def asyncTearDown(self):
        await self.viewer.cleanup()
        await self.bridge.cleanup()
        self.temp.cleanup()

    async def listen(self, app, ssl_context=None):
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        listener = socket.socket()
        listener.bind(("localhost", 0))
        listener.listen()
        port = listener.getsockname()[1]
        await web.SockSite(runner, listener, ssl_context=ssl_context).start()
        return runner, f"{'https' if ssl_context else 'http'}://localhost:{port}"

    def tls_contexts(self, root, key):
        now = datetime.now(timezone.utc)
        ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Fixture CA")])
        ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
              .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
              .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
              .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
              .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, None, None), critical=True)
              .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
              .sign(ca_key, hashes.SHA256()))
        leaf = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
                .issuer_name(ca_name).public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
                .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
                .add_extension(x509.KeyUsage(True, False, True, False, False, False, False, None, None), critical=True)
                .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                .sign(ca_key, hashes.SHA256()))
        (root / "ca.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
        (root / "server.pem").write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
        server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server.load_cert_chain(root / "server.pem", root / "key.pem")
        return server, ssl.create_default_context(cafile=str(root / "ca.pem"))

    async def token(self, request):
        self.assertEqual(request.headers["Authorization"], encode_basic_auth("example-client", "synthetic-client-secret"))
        self.assertEqual(dict(await request.post()), {"grant_type": "client_credentials"})
        self.assertTrue(request.secure)
        self.token_requests += 1
        value = await self.issuer()
        return web.json_response({"access_token": value.value, "token_type": "Bearer", "expires_in": 6,
                                  "expires_at": value.expires_at})

    def validate(self, token):
        self.assertEqual(jwt.get_unverified_header(token), {"alg": "RS256", "typ": "JWT", "kid": "example-key"})
        value = jwt.decode(token, self.public_key, algorithms=["RS256"], issuer="example-issuer", audience="example-bridge")
        self.assertEqual(value["mt5bridge_grant"]["referenceReads"], [
            {"sourceId": "example-source", "group": "demo\\group", "symbol": None}])
        return value

    async def stream(self, request):
        self.assertEqual(request.query["group"], "demo\\group")
        header = request.headers["Authorization"]
        self.assertTrue(header.startswith("Bearer "))
        initial = self.validate(header[7:])
        if initial["sub"] != "example-caller":
            raise web.HTTPForbidden()
        self.claims.append(initial)
        expiry = initial["exp"]
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.active, self.transport = ws, request.transport
        self.connections += 1
        self.assertEqual(await ws.receive_json(), {"all": True})

        async def quotes():
            while not ws.closed:
                if time.time() >= expiry:
                    await ws.close(code=1008, message=b"grant_expired")
                    return
                if not self.hold:
                    for symbol in ["BTCUSD", "EURUSD"]:
                        await ws.send_json({
                            "type": "quote", "sourceId": "example-source", "contextGroup": "demo\\group",
                            "symbol": symbol, "contextVersion": "example-version", "readiness": "ready",
                            "observedAt": utc_text(time.time()), "sourceAgeMilliseconds": 0,
                            "maximumDataAgeMilliseconds": 2000,
                            "data": {"bid": "100.01000", "ask": "100.02000", "last": "0"},
                        })
                await asyncio.sleep(0.05)

        task = asyncio.create_task(quotes())
        try:
            async for message in ws:
                if message.type != WSMsgType.TEXT:
                    continue
                renewal = json.loads(message.data)
                self.assertEqual(set(renewal), {"type", "token"})
                self.assertEqual(renewal["type"], "authorization")
                replacement = self.validate(renewal["token"])
                accepted = replacement["sub"] == initial["sub"]
                if accepted:
                    expiry = replacement["exp"]
                    self.claims.append(replacement)
                await ws.send_json({
                    "schemaVersion": "1", "type": "authorization", "status": "accepted" if accepted else "rejected",
                    "code": None if accepted else "caller_mismatch", "expiresAt": utc_text(expiry),
                })
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, ConnectionResetError):
                await task
        return ws

    async def until(self, condition, invariant=None):
        async with asyncio.timeout(20):
            while not condition():
                if invariant:
                    invariant()
                await asyncio.sleep(0.02)

    async def test_direct_jwt_group_stream_survives_two_expiry_boundaries(self):
        original = self.feed.messages
        await self.until(lambda: self.feed.renewals >= 1)
        first_replacement_expiry = self.feed.expires_at

        def continuous():
            state = self.feed.snapshot()
            self.assertEqual((state["connections"], state["symbols"], state["ready"]), (1, 2, 2))

        await self.until(lambda: self.feed.renewals >= 2 and time.time() > first_replacement_expiry + 0.1, continuous)
        continuous()
        self.assertEqual(self.connections, 1)
        self.assertGreaterEqual(self.token_requests, 3)
        self.assertGreater(self.feed.messages, original)
        self.assertEqual(len({claim["jti"] for claim in self.claims}), len(self.claims))
        async with ClientSession() as session:
            async with session.get(self.viewer_url + "/api/state") as response:
                state = await response.json()
                self.assertEqual(state["rows"][0]["bid"], "100.01000")
                self.assertNotIn("token", state)
            async with session.get(self.viewer_url + "/events") as response:
                event = json.loads((await response.content.readline()).decode()[6:])
                self.assertEqual((event["connections"], event["symbols"]), (1, 2))

    async def test_rejected_renewal_does_not_extend_old_authorization(self):
        expiry = self.feed.expires_at
        self.issuer.auth["subject"] = "another-caller"
        await self.until(lambda: "caller_mismatch" in (self.feed.error or ""))
        self.assertEqual(self.feed.expires_at, expiry)
        self.assertEqual(self.feed.renewals, 0)
        self.assertEqual(self.feed.snapshot()["ready"], 2)
        await self.until(lambda: time.time() > expiry and self.feed.state == "disconnected")
        self.assertEqual(self.feed.snapshot()["ready"], 0)
        self.assertEqual(len(self.feed.rows), 2)

    async def test_connection_reset_retains_stale_cache_until_fresh_data(self):
        self.hold = True
        self.transport.abort()
        await self.until(lambda: self.feed.state == "disconnected")
        self.assertEqual((len(self.feed.rows), self.feed.snapshot()["ready"]), (2, 0))
        await self.until(lambda: self.feed.connections == 2)
        self.assertEqual((len(self.feed.rows), self.feed.snapshot()["ready"]), (2, 0))
        self.hold = False
        await self.until(lambda: self.feed.snapshot()["ready"] == 2)


if __name__ == "__main__":
    unittest.main()
