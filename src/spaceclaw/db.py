"""SQLite storage.

`users` holds each Flickr user's profile, OAuth tokens and login passphrase
(a unique column). `oauth_requests` holds in-flight web logins, keyed by the
OAuth request token, until Flickr redirects back to /callback.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    nsid               TEXT PRIMARY KEY,
    username           TEXT NOT NULL,
    realname           TEXT,
    path_alias         TEXT,
    oauth_token        TEXT NOT NULL,
    oauth_token_secret TEXT NOT NULL,
    passphrase         TEXT UNIQUE,
    profile            TEXT NOT NULL DEFAULT '{}',
    created_at         INTEGER NOT NULL DEFAULT (CAST(strftime('%s', 'now') AS INTEGER)),
    updated_at         INTEGER NOT NULL DEFAULT (CAST(strftime('%s', 'now') AS INTEGER))
);

CREATE TABLE IF NOT EXISTS oauth_requests (
    token             TEXT PRIMARY KEY,
    token_secret      TEXT NOT NULL,
    telnet_session_id TEXT NOT NULL,
    created_at        INTEGER NOT NULL DEFAULT (CAST(strftime('%s', 'now') AS INTEGER))
);
"""

# Pending OAuth handshakes older than this are abandoned.
OAUTH_REQUEST_TTL = 3600


@dataclass(frozen=True)
class User:
    nsid: str
    username: str
    oauth_token: str
    oauth_token_secret: str
    realname: str | None = None
    path_alias: str | None = None
    passphrase: str | None = None


@dataclass(frozen=True)
class OAuthRequest:
    token: str
    token_secret: str
    telnet_session_id: str


class Database:
    def __init__(self, path: str) -> None:
        # The app is single-threaded asyncio and every query is a point lookup,
        # so a single blocking connection is fine.
        self.conn = sqlite3.connect(path, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # Users

    def upsert_user(self, user: User, profile: dict[str, Any] | None = None) -> None:
        """Insert or update a user's profile and tokens, keeping their passphrase."""
        self.conn.execute(
            """
            INSERT INTO users (nsid, username, realname, path_alias, oauth_token, oauth_token_secret, profile)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (nsid) DO UPDATE SET
                username = excluded.username,
                realname = excluded.realname,
                path_alias = excluded.path_alias,
                oauth_token = excluded.oauth_token,
                oauth_token_secret = excluded.oauth_token_secret,
                profile = excluded.profile,
                updated_at = CAST(strftime('%s', 'now') AS INTEGER)
            """,
            (
                user.nsid,
                user.username,
                user.realname,
                user.path_alias,
                user.oauth_token,
                user.oauth_token_secret,
                json.dumps(profile or {}),
            ),
        )

    def get_user(self, nsid: str) -> User | None:
        row = self.conn.execute("SELECT * FROM users WHERE nsid = ?", (nsid,)).fetchone()
        return _user_from_row(row)

    def get_user_by_passphrase(self, passphrase: str) -> User | None:
        row = self.conn.execute("SELECT * FROM users WHERE passphrase = ?", (passphrase,)).fetchone()
        return _user_from_row(row)

    def set_passphrase(self, nsid: str, passphrase: str) -> bool:
        """Assign a passphrase, replacing any old one. False if it's taken by someone else."""
        try:
            self.conn.execute(
                "UPDATE users SET passphrase = ?, updated_at = CAST(strftime('%s', 'now') AS INTEGER) WHERE nsid = ?",
                (passphrase, nsid),
            )
        except sqlite3.IntegrityError:
            return False
        return True

    # OAuth handshakes

    def save_oauth_request(self, req: OAuthRequest) -> None:
        self.conn.execute(
            "DELETE FROM oauth_requests WHERE created_at < ?", (int(time.time()) - OAUTH_REQUEST_TTL,)
        )
        self.conn.execute(
            "INSERT OR REPLACE INTO oauth_requests (token, token_secret, telnet_session_id) VALUES (?, ?, ?)",
            (req.token, req.token_secret, req.telnet_session_id),
        )

    def pop_oauth_request(self, token: str) -> OAuthRequest | None:
        row = self.conn.execute(
            "SELECT * FROM oauth_requests WHERE token = ? AND created_at >= ?",
            (token, int(time.time()) - OAUTH_REQUEST_TTL),
        ).fetchone()
        self.conn.execute("DELETE FROM oauth_requests WHERE token = ?", (token,))
        if row is None:
            return None
        return OAuthRequest(row["token"], row["token_secret"], row["telnet_session_id"])


def _user_from_row(row: sqlite3.Row | None) -> User | None:
    if row is None:
        return None
    return User(
        nsid=row["nsid"],
        username=row["username"],
        realname=row["realname"],
        path_alias=row["path_alias"],
        oauth_token=row["oauth_token"],
        oauth_token_secret=row["oauth_token_secret"],
        passphrase=row["passphrase"],
    )
