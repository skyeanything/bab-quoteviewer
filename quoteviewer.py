"""Connect directly to Bridge and expose the quote cache to a local browser."""

import argparse
import asyncio
import contextlib
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import time
from urllib.parse import quote, urlencode, urlsplit
import uuid

import aiohttp
from aiohttp import web
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import jwt


ROOT = Path(__file__).resolve().parent


def utc_text(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


def utc_seconds(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


@dataclass(frozen=True)
class Token:
    value: str
    expires_at: float


class TokenIssuer:
    """Sign only the configured source/group grant using an authorized local key."""

    def __init__(self, config, config_directory):
        self.auth = config["auth"]
        self.source = config["sourceId"]
        self.group = config["group"]
        self.lifetime = self.auth.get("lifetimeSeconds", 120)
        if not 1 <= self.lifetime <= 300:
            raise ValueError("lifetimeSeconds must be between 1 and 300")
        key_path = config_directory / self.auth["privateKeyFile"]
        self.key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        if not isinstance(self.key, rsa.RSAPrivateKey) or self.key.key_size < 2048:
            raise ValueError("Use an RSA private key of at least 2048 bits")

    async def __call__(self):
        now = int(time.time())
        payload = {
            "iss": self.auth["issuer"], "aud": self.auth["audience"],
            "sub": self.auth["subject"], "jti": uuid.uuid4().hex,
            "iat": now, "nbf": now, "exp": now + self.lifetime,
            "mt5bridge_grant": {
                "version": "1",
                "referenceReads": [{"sourceId": self.source, "group": self.group, "symbol": None}],
            },
        }
        # 签名在线程中执行，避免阻塞行情接收。
        value = await asyncio.to_thread(
            jwt.encode, payload, self.key, algorithm="RS256",
            headers={"typ": "JWT", "kid": self.auth["keyId"]},
        )
        return Token(value, payload["exp"])


class QuoteFeed:
    def __init__(self, config, issue_token):
        self.config = config
        self.issue_token = issue_token
        self.url = (config["bridgeUrl"].rstrip("/") + "/v1/sources/"
                    + quote(config["sourceId"], safe="") + "/quotes/stream?"
                    + urlencode({"group": config["group"]}))
        self.rows = {}
        self.changed_symbols = set()
        self.state, self.error = "connecting", None
        self.connections = self.messages = self.price_changes = self.renewals = self.version = 0
        self.renewing = False
        self.expires_at = self.last_received = None
        self.pending = None

    def set_state(self, state, error=None):
        self.state, self.error = state, error
        self.version += 1

    def snapshot(self, after=-1):
        now = time.time()
        ready = sum(
            self.state == "connected" and row["connection"] == self.connections
            and row["readiness"] == "ready"
            and max(0, now - utc_seconds(row["observedAt"])) * 1000
            + (row["sourceAge"] or 0) < row["maximumAge"]
            for row in self.rows.values()
        )
        return {
            "sourceId": self.config["sourceId"], "group": self.config["group"],
            "remote": urlsplit(self.config["bridgeUrl"]).netloc,
            "state": self.state, "error": self.error, "connections": self.connections,
            "renewals": self.renewals, "renewing": self.renewing,
            "messages": self.messages, "priceChanges": self.price_changes,
            "changedSymbols": len(self.changed_symbols), "symbols": len(self.rows),
            "withData": sum(row["bid"] is not None for row in self.rows.values()),
            "ready": ready, "version": self.version, "lastReceivedAt": self.last_received,
            "tokenExpiresAt": utc_text(self.expires_at) if self.expires_at else None,
            "rows": [row for row in self.rows.values() if row["version"] > after],
        }

    def observe(self, value, raw):
        if (value["sourceId"] != self.config["sourceId"]
                or value["contextGroup"] != self.config["group"]):
            raise ValueError("Unexpected quote source or group")
        symbol, data = value["symbol"], value["data"] or {}
        previous = self.rows.get(symbol)
        bid, ask, last = (data.get(name) for name in ("bid", "ask", "last"))
        direction = 0
        if bid is not None and previous and previous["bid"] is not None:
            if (bid, ask, last) != (previous["bid"], previous["ask"], previous["last"]):
                self.price_changes += 1
                self.changed_symbols.add(symbol)
            direction = (Decimal(bid) > Decimal(previous["bid"])) - (Decimal(bid) < Decimal(previous["bid"]))
        self.messages += 1
        self.version += 1
        self.last_received = utc_text(time.time())
        self.rows[symbol] = {
            "symbol": symbol, "bid": bid, "ask": ask, "last": last,
            "readiness": value["readiness"], "receivedAt": self.last_received,
            "observedAt": value["observedAt"], "maximumAge": value["maximumDataAgeMilliseconds"],
            "sourceAge": value["sourceAgeMilliseconds"],
            "updates": previous["updates"] + 1 if previous else 1,
            "direction": direction, "raw": raw, "version": self.version,
            "connection": self.connections,
        }

    async def run(self):
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, sock_connect=15)) as session:
                while True:
                    try:
                        self.set_state("authorizing")
                        token = await asyncio.wait_for(self.issue_token(), 30)
                        self.expires_at = token.expires_at
                        self.set_state("connecting")
                        async with asyncio.timeout(15):
                            socket = await session.ws_connect(
                                self.url, headers={"Authorization": "Bearer " + token.value},
                            )
                        async with socket:
                            await socket.send_json({"all": True})
                            self.connections += 1
                            self.set_state("connected")
                            await self.run_session(socket)
                    except (aiohttp.ClientError, OSError, TimeoutError, ValueError, KeyError, RuntimeError) as error:
                        # 不把可能包含 URL、请求头或令牌的异常详情写入页面。
                        detail = (f"WebSocket 握手失败：HTTP {error.status}" if isinstance(error, aiohttp.WSServerHandshakeError)
                                  else str(error) if isinstance(error, FeedError)
                                  else f"行情连接失败（{type(error).__name__}），5 秒后重试。")
                        self.set_state("disconnected", detail)
                        await asyncio.sleep(5)
        finally:
            self.renewing = False
            self.set_state("stopped")

    async def run_session(self, socket):
        loop = asyncio.get_running_loop()
        async with asyncio.timeout(max(0, self.expires_at - time.time())) as deadline:
            async def receive():
                async for message in socket:
                    if message.type == aiohttp.WSMsgType.TEXT:
                        value = json.loads(message.data)
                        if value["type"] == "authorization":
                            if self.pending is not None and not self.pending.done():
                                self.pending.set_result(value)
                        else:
                            self.observe(value, message.data)
                    elif message.type == aiohttp.WSMsgType.ERROR:
                        raise FeedError("行情连接中断，正在重新连接。")
                raise FeedError(f"服务器关闭连接：{socket.close_code}")

            async def renew():
                while True:
                    remaining = max(0, self.expires_at - time.time())
                    await asyncio.sleep(remaining - min(30, remaining / 3))
                    accepted = False
                    while not accepted:
                        self.renewing = True
                        self.version += 1
                        try:
                            token = await asyncio.wait_for(self.issue_token(), 30)
                        except (OSError, TimeoutError, ValueError, RuntimeError):
                            self.error = "暂时无法获取新授权，将在当前授权到期前重试。"
                            await asyncio.sleep(5)
                            continue
                        self.pending = loop.create_future()
                        try:
                            await socket.send_json({"type": "authorization", "token": token.value})
                            ack = await asyncio.wait_for(self.pending, 10)
                        finally:
                            self.pending = None
                        if ack["status"] == "accepted":
                            self.expires_at = utc_seconds(ack["expiresAt"])
                            deadline.reschedule(loop.time() + max(0, self.expires_at - time.time()))
                            self.renewals += 1
                            self.renewing, self.error = False, None
                            self.version += 1
                            accepted = True
                        else:
                            self.error = "授权续期被拒绝：" + ack["code"]
                            self.version += 1
                            await asyncio.sleep(5)

            tasks = [asyncio.create_task(receive()), asyncio.create_task(renew())]
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    await task
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                self.pending = None
                self.renewing = False


class FeedError(RuntimeError):
    pass


def create_app(feed):
    app = web.Application()
    stopping = asyncio.Event()

    async def lifecycle(app):
        task = asyncio.create_task(feed.run())
        yield
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def shutdown(app):
        stopping.set()

    async def index(request):
        return web.FileResponse(ROOT / "wwwroot/index.html")

    async def state(request):
        return web.json_response(feed.snapshot(), headers={"Cache-Control": "no-store"})

    async def events(request):
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-store"})
        await response.prepare(request)
        version = -1
        try:
            while not stopping.is_set():
                snapshot = feed.snapshot(version)
                version = snapshot["version"]
                await response.write(("data: " + json.dumps(snapshot, ensure_ascii=False) + "\n\n").encode())
                try:
                    await asyncio.wait_for(stopping.wait(), 0.25)
                except TimeoutError:
                    pass
        except ConnectionResetError:
            pass
        return response

    app.cleanup_ctx.append(lifecycle)
    app.on_shutdown.append(shutdown)
    app.add_routes([web.get("/", index), web.get("/api/state", state), web.get("/events", events)])
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    args = parser.parse_args()
    if not args.config.exists():
        parser.error("Copy config.example.json to config.json and configure your authorized endpoint and signing key.")
    config = json.loads(args.config.read_text())
    if urlsplit(config["bridgeUrl"]).scheme not in ("ws", "wss"):
        parser.error("bridgeUrl must use ws:// or wss://")
    try:
        issuer = TokenIssuer(config, args.config.resolve().parent)
    except (OSError, ValueError, KeyError) as error:
        parser.error(f"Check auth configuration and the authorized RSA private key ({type(error).__name__}).")
    feed = QuoteFeed(config, issuer)
    web.run_app(create_app(feed), host="localhost", port=config.get("httpPort", 8877), access_log=None)


if __name__ == "__main__":
    main()
