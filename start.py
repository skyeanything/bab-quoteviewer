#!/usr/bin/env python3
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent


def main():
    config_path = ROOT / "config.json"
    if not config_path.exists():
        sys.exit("Copy config.example.json to config.json and set the connection and token provider.")
    config = json.loads(config_path.read_text())
    env = os.environ.copy()
    env.update({
        "QUOTE_VIEWER_UPSTREAM": config["bridgeUrl"].rstrip("/"),
        "QUOTE_VIEWER_SOURCE": config["sourceId"],
        "QUOTE_VIEWER_GROUP": config["group"],
        "QUOTE_VIEWER_REMOTE": urlsplit(config["bridgeUrl"]).netloc,
        "QUOTE_VIEWER_PORT": str(config.get("httpPort", 8877)),
        "QUOTE_VIEWER_TOKEN_COMMAND": json.dumps(config["tokenCommand"]),
    })
    print(f'Open http://localhost:{env["QUOTE_VIEWER_PORT"]}', flush=True)
    try:
        subprocess.run(["dotnet", "run", "--project", str(ROOT / "QuoteViewer.csproj"),
                        "--no-launch-profile"], cwd=ROOT, env=env, check=True)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
