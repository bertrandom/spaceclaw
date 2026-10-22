"""End-to-end: real telnet socket, real SQLite, fake Flickr and jp2a."""

import asyncio

import pytest

from spaceclaw import bbs as bbs_module
from spaceclaw.bbs import BBS
from spaceclaw.config import Config
from spaceclaw.db import Database, User
from spaceclaw.flickr import Photo, PhotoPage
from spaceclaw.telnet import IAC, OPT_NAWS, SB, SE, TelnetConnection

ALICE = User(nsid="1@N00", username="alice", oauth_token="t", oauth_token_secret="s")


class FakeFlickr:
    def __init__(self):
        self.pages = {
            1: PhotoPage([Photo("p1", "1@N00", "First", "u1", pathalias="alice")], 1, 2),
            2: PhotoPage([Photo("p2", "1@N00", "", "u2")], 2, 2),
        }

    async def get_photostream(self, token, secret, nsid, page=1):
        return self.pages[page]

    async def get_contacts_photos(self, token, secret):
        return [Photo("c1", "9@N00", "Cat", "u3", username="bob")]

    async def download(self, url):
        return b"jpeg:" + url.encode()


@pytest.fixture
def bbs(tmp_path, monkeypatch):
    async def fake_render(self, url):
        return f"<ascii {url} {self.width}x{self.height} colors={self.colors}>\n"

    monkeypatch.setattr(bbs_module.TelnetSession, "render_ascii", fake_render)
    db = Database(str(tmp_path / "test.db"))
    db.upsert_user(ALICE)
    db.set_passphrase(ALICE.nsid, "able-about-above-act")
    config = Config(flickr_api_key="k", flickr_api_secret="s", public_url="https://example.test")
    return BBS(config, db, FakeFlickr())


class Client:
    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer

    async def expect(self, text: str) -> str:
        data = await asyncio.wait_for(self.reader.readuntil(text.encode()), 5)
        return data.decode(errors="replace")

    def line(self, text: str) -> None:
        self.writer.write(text.encode() + b"\r\n")


@pytest.fixture
async def connect(bbs):
    server = await asyncio.get_running_loop().create_server(
        lambda: TelnetConnection(bbs.on_connect), "127.0.0.1", 0
    )
    port = server.sockets[0].getsockname()[1]
    clients = []

    async def _connect(width=100, height=40):
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(bytes([IAC, SB, OPT_NAWS, 0, width, 0, height, IAC, SE]))
        clients.append(writer)
        return Client(reader, writer)

    yield _connect
    for w in clients:
        w.close()
    server.close()


async def test_passphrase_login_and_browse(connect):
    c = await connect()
    out = await c.expect("or enter your passphrase.")
    assert "https://example.test/login/" in out

    c.line("Able About  Above act")
    out = await c.expect("Sign out")
    assert "Hi alice!" in out
    assert "If you'd like to easily login" not in out

    c.line("3")
    await c.expect("Color is now")
    await c.expect("Sign out")

    c.line("1")
    out = await c.expect("return to main menu")
    assert "<ascii u1 100x40 colors=True>" in out
    assert "First" in out and "https://www.flickr.com/photos/alice/p1/" in out

    c.line("")  # loads page 2
    out = await c.expect("return to main menu")
    assert "Untitled" in out and "/photos/1@N00/p2/" in out

    c.line("")  # wraps back to the start
    out = await c.expect("return to main menu")
    assert "<ascii u1" in out

    c.line("q")
    await c.expect("Sign out")
    c.line("2")
    out = await c.expect("return to main menu")
    assert "Cat by" in out and "bob" in out

    c.line("q")
    await c.expect("Sign out")
    c.line("6")
    await c.expect("NO CARRIER")
    # Server hangs up after the trailing blank line.
    assert await asyncio.wait_for(c.reader.read(), 5) == b"\r\n\r\n"


async def test_bad_passphrase_disconnects_after_three(connect):
    c = await connect()
    await c.expect("or enter your passphrase.")
    for remaining in (2, 1):
        c.line("nope")
        await c.expect(f"({remaining} login attempts remaining)")
    c.line("nope")
    await c.expect("Connection closed by foreign host.")


async def test_web_auth_assigns_new_passphrase(connect, bbs):
    c = await connect()
    out = await c.expect("or enter your passphrase.")
    session_id = out.split("/login/")[1].split("/")[0]

    assert bbs.complete_auth(session_id, ALICE)
    out = await c.expect("Sign out")
    assert "If you'd like to easily login in the future" in out
    phrase = out.split("use this passphrase:")[1].strip().splitlines()[0].strip()

    assert bbs.db.get_user_by_passphrase("able-about-above-act") is None
    assert bbs.db.get_user_by_passphrase(phrase.replace(" ", "-")).nsid == ALICE.nsid
