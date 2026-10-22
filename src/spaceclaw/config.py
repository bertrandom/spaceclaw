"""Runtime configuration, read from config/secrets.json and SPACECLAW_* env vars.

Env vars take precedence over the JSON file, so production can run off either.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    flickr_api_key: str
    flickr_api_secret: str
    public_url: str = "https://spaceclaw.net"
    telnet_host: str = "0.0.0.0"
    telnet_port: int = 23
    web_host: str = "127.0.0.1"
    web_port: int = 8080
    db_path: str = "spaceclaw.db"
    dev: bool = False
    # Dev-only: entering this passphrase logs in as admin_nsid.
    backdoor_passphrase: str | None = None
    admin_nsid: str | None = None

    @property
    def callback_url(self) -> str:
        return f"{self.public_url}/callback"

    def login_url(self, session_id: str) -> str:
        return f"{self.public_url}/login/{session_id}/"


def load_config(secrets_path: str | os.PathLike[str] | None = None) -> Config:
    path = Path(secrets_path or os.environ.get("SPACECLAW_SECRETS", "config/secrets.json"))
    file_values: dict[str, str] = {}
    if path.exists():
        file_values = json.loads(path.read_text())

    def get(name: str, default: str | None = None) -> str | None:
        return os.environ.get(f"SPACECLAW_{name.upper()}", file_values.get(name, default))

    dev = get("dev", "") not in ("", "0", "false") or "SPACE_CLAW_DEV" in os.environ

    api_key = get("flickr_api_key")
    api_secret = get("flickr_api_secret")
    if not api_key or not api_secret:
        raise SystemExit(
            f"Missing Flickr API credentials: set them in {path} or via "
            "SPACECLAW_FLICKR_API_KEY / SPACECLAW_FLICKR_API_SECRET."
        )

    return Config(
        flickr_api_key=api_key,
        flickr_api_secret=api_secret,
        public_url=get("public_url", "http://localhost:8080" if dev else "https://spaceclaw.net").rstrip("/"),
        telnet_host=get("telnet_host", "0.0.0.0"),
        telnet_port=int(get("telnet_port", "2323" if dev else "23")),
        web_host=get("web_host", "127.0.0.1"),
        web_port=int(get("web_port", "8080")),
        db_path=get("db_path", "spaceclaw.db"),
        dev=dev,
        backdoor_passphrase=get("backdoor_passphrase") if dev else None,
        admin_nsid=get("admin_nsid", "61091860@N00") if dev else None,
    )
