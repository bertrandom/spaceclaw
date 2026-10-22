"""The telnet BBS: one TelnetSession per connection, walking through screens."""

from __future__ import annotations

import asyncio
import json
import logging
import random
import secrets
from dataclasses import dataclass
from functools import cache
from importlib import resources

from . import ansi
from .config import Config
from .db import Database, User
from .flickr import Flickr, FlickrError, Photo
from .telnet import Line, TelnetConnection, WindowSize

log = logging.getLogger(__name__)

# How long to wait for the client to report its window size before assuming 80x24.
NAWS_TIMEOUT = 3.0
MAX_LOGIN_ATTEMPTS = 3
HICCUPS = "Flickr has the hiccups, please try again later."

COLOR_DEPTH_NAMES = {4: "ANSI", 8: "256 colors", 24: "24-bit color"}
# Option [4] cycles 8 -> 24 -> 4 -> 8.
NEXT_COLOR_DEPTH = {8: 24, 24: 4, 4: 8}


@cache
def _logos() -> dict[int, str]:
    data = resources.files("spaceclaw").joinpath("data/logo.json").read_text()
    return {int(width): art for width, art in json.loads(data).items()}


@cache
def _words() -> list[str]:
    return json.loads(resources.files("spaceclaw").joinpath("data/words.json").read_text())


def generate_passphrase() -> str:
    return "-".join(secrets.choice(_words()) for _ in range(4))


def normalize_passphrase(line: str) -> str:
    return "-".join(line.lower().split())


@dataclass(frozen=True)
class AuthComplete:
    """Posted to a session's event queue by the web app when its OAuth login finishes."""

    user: User


class Disconnect(Exception):
    pass


class BBS:
    """Shared state for all telnet sessions."""

    def __init__(self, config: Config, db: Database, flickr: Flickr) -> None:
        self.config = config
        self.db = db
        self.flickr = flickr
        self.sessions: dict[str, TelnetSession] = {}

    def new_session_id(self) -> str:
        while True:
            session_id = str(random.randint(100000, 999999))
            if session_id not in self.sessions:
                return session_id

    def on_connect(self, conn: TelnetConnection) -> None:
        session = TelnetSession(self, conn)
        self.sessions[session.id] = session
        task = asyncio.create_task(session.run())
        task.add_done_callback(lambda _: self.sessions.pop(session.id, None))

    def complete_auth(self, session_id: str, user: User) -> bool:
        """Hand a freshly authenticated user to the waiting telnet session."""
        session = self.sessions.get(session_id)
        if session is None:
            return False
        session.conn.events.put_nowait(AuthComplete(user))
        return True


class TelnetSession:
    def __init__(self, bbs: BBS, conn: TelnetConnection) -> None:
        self.bbs = bbs
        self.conn = conn
        self.id = bbs.new_session_id()
        self.width = 80
        self.height = 24
        self.colors = False
        self.color_depth = 8
        self.user: User | None = None

    # I/O

    def send(self, text: str) -> None:
        self.conn.send(text)

    def send_line(self, line: str = "") -> None:
        self.conn.send_line(line)

    async def next_event(self) -> Line | AuthComplete:
        """Next line or auth event; window size changes are absorbed here."""
        while True:
            event = await self.conn.events.get()
            match event:
                case None:
                    raise Disconnect
                case WindowSize(width, height):
                    self._resize(width, height)
                case _:
                    return event

    async def read_line(self) -> str:
        while True:
            event = await self.next_event()
            if isinstance(event, Line):
                return event.text

    def _resize(self, width: int, height: int) -> None:
        if width > 0 and height > 0:
            self.width, self.height = width, height

    # Flow

    async def run(self) -> None:
        log.info("connect %s session=%s", self.conn.peer, self.id)
        try:
            await self._wait_for_window_size()
            self.display_logo()
            self.display_login_prompt()
            if await self.login():
                await self.main_menu()
        except Disconnect:
            pass
        except Exception:
            log.exception("session %s crashed", self.id)
        finally:
            self.conn.close()
            log.info("disconnect %s session=%s", self.conn.peer, self.id)

    async def _wait_for_window_size(self) -> None:
        try:
            event = await asyncio.wait_for(self.conn.events.get(), NAWS_TIMEOUT)
        except TimeoutError:
            return
        match event:
            case None:
                raise Disconnect
            case WindowSize(width, height):
                self._resize(width, height)
            case _:
                # Client typed before reporting a size (no NAWS support); don't lose the input.
                self.conn.events.put_nowait(event)

    def display_logo(self) -> None:
        self.send_line()
        if self.width >= 80:
            self.send(_logos()[min(self.width, 200)])
        self.send(ansi.RESET)
        self.send_line()

    def display_login_prompt(self) -> None:
        url = self.bbs.config.login_url(self.id)
        self.send_line()
        self.send_line(f"Please go to this URL to login: {ansi.bold(url)} or enter your passphrase.")
        self.send_line()

    async def login(self) -> bool:
        remaining = MAX_LOGIN_ATTEMPTS
        while True:
            event = await self.next_event()

            if isinstance(event, AuthComplete):
                passphrase = self._assign_passphrase(event.user)
                self.user = event.user
                self.welcome(passphrase)
                return True

            if not event.text.strip():
                continue

            passphrase = normalize_passphrase(event.text)
            config = self.bbs.config
            if config.dev and config.backdoor_passphrase and passphrase == config.backdoor_passphrase:
                user = self.bbs.db.get_user(config.admin_nsid or "")
            else:
                user = self.bbs.db.get_user_by_passphrase(passphrase)

            if user is not None:
                self.user = user
                self.send_line()
                self.welcome()
                return True

            remaining -= 1
            self.send_line()
            if remaining == 0:
                self.send_line(
                    ansi.bold(ansi.red("Invalid passphrase."))
                    + " If you're happy and you know it, Connection closed by foreign host."
                )
                self.send_line()
                return False
            self.send_line(
                ansi.bold(ansi.red("Invalid passphrase, please try again."))
                + f" ({remaining} login attempts remaining)"
            )
            self.send_line()

    def _assign_passphrase(self, user: User) -> str:
        while True:
            passphrase = generate_passphrase()
            if self.bbs.db.set_passphrase(user.nsid, passphrase):
                return passphrase

    def welcome(self, new_passphrase: str | None = None) -> None:
        assert self.user is not None
        self.send_line(ansi.bold(ansi.green("Login successful.")))
        self.send_line()
        self.send_line(f"Hi {self.user.username}!")
        if new_passphrase:
            self.send_line()
            self.send_line("If you'd like to easily login in the future, you can use this passphrase:")
            self.send_line(new_passphrase.replace("-", " "))

    # Main menu

    def display_options(self) -> None:
        self.send_line()
        self.send_line(f"[{ansi.bold('1')}] Your Photostream")
        self.send_line(f"[{ansi.bold('2')}] Photos from your contacts")
        self.send_line(f"[{ansi.bold('3')}] Color: {ansi.green('ON') if self.colors else 'OFF'}")
        self.send_line(f"[{ansi.bold('4')}] Color Depth: {COLOR_DEPTH_NAMES[self.color_depth]}")
        self.send_line(f"[{ansi.bold('5')}] About")
        self.send_line(f"[{ansi.bold('6')}] Sign out")
        self.send_line()

    async def main_menu(self) -> None:
        self.display_options()
        while True:
            match (await self.read_line()).strip():
                case "1":
                    if not await self.photostream():
                        return
                case "2":
                    if not await self.contacts_photos():
                        return
                case "3":
                    self.colors = not self.colors
                    self.send_line()
                    self.send_line(f"Color is now {ansi.green('ON') if self.colors else 'OFF'}.")
                case "4":
                    self.color_depth = NEXT_COLOR_DEPTH[self.color_depth]
                    self.send_line(f"Color depth is now {COLOR_DEPTH_NAMES[self.color_depth]}.")
                case "5":
                    self.display_about()
                case "6":
                    self.send_line()
                    self.send_line("NO CARRIER")
                    self.send_line()
                    return
                case _:
                    self.send_line()
                    self.send_line(ansi.red("Invalid option."))
            self.display_options()

    def display_about(self) -> None:
        for line in [
            "",
            "space claw v2.0",
            "",
            "Bertrand Fan <bert@bert.org>",
            "https://bert.org",
            "Twitter: @bertrandom",
            "",
            "Source code available on github: https://github.com/bertrandom/spaceclaw",
            "",
        ]:
            self.send_line(line)

    # Photo browsers. Each returns False if the session should end.

    async def photostream(self) -> bool:
        assert self.user is not None
        user, flickr = self.user, self.bbs.flickr
        self.send_line()
        self.send_line(ansi.bold("Your photostream"))
        self.send_line()

        try:
            first = await flickr.get_photostream(user.oauth_token, user.oauth_token_secret, user.nsid)
        except FlickrError:
            log.exception("photostream failed for %s", user.nsid)
            self.send_line(HICCUPS)
            return False
        photos, page, pages = first.photos, first.page, first.pages
        if not photos:
            self.send_line("No photos yet.")
            return True

        cursor = 0
        while True:
            await self.display_photo(photos[cursor], show_owner=False)
            if not await self._next_or_quit():
                return True
            cursor += 1
            if cursor >= len(photos):
                if page < pages:
                    try:
                        more = await flickr.get_photostream(
                            user.oauth_token, user.oauth_token_secret, user.nsid, page=page + 1
                        )
                    except FlickrError:
                        log.exception("photostream page %d failed for %s", page + 1, user.nsid)
                        self.send_line(HICCUPS)
                        return False
                    photos.extend(more.photos)
                    page = more.page
                if cursor >= len(photos):
                    # Loop back around.
                    cursor = 0

    async def contacts_photos(self) -> bool:
        assert self.user is not None
        self.send_line()
        self.send_line(ansi.bold("Photos from your contacts"))
        self.send_line()

        try:
            photos = await self.bbs.flickr.get_contacts_photos(self.user.oauth_token, self.user.oauth_token_secret)
        except FlickrError:
            log.exception("contacts photos failed for %s", self.user.nsid)
            self.send_line(HICCUPS)
            return False
        if not photos:
            self.send_line("Your contacts haven't posted any photos.")
            return True

        cursor = 0
        while True:
            await self.display_photo(photos[cursor], show_owner=True)
            if not await self._next_or_quit():
                return True
            cursor = (cursor + 1) % len(photos)

    async def _next_or_quit(self) -> bool:
        """Wait for [enter] (True) or [q] (False)."""
        while True:
            match (await self.read_line()).strip().lower():
                case "":
                    return True
                case "q":
                    return False
                case _:
                    self.send_line()
                    self.send_line(ansi.red("Invalid option."))
                    self.send_line()

    async def display_photo(self, photo: Photo, show_owner: bool) -> None:
        art = await self.render_ascii(photo.url_s)
        longest = max((len(ansi.strip(line)) for line in art.splitlines()), default=0)

        title = photo.title.strip() or "Untitled"
        if show_owner:
            title = f"{title} by {ansi.bold(photo.username or photo.owner)}"

        self.send_line()
        self.send(art)
        self.send_line()
        self.send_line(title)
        self.send_line(photo.page_url.rjust(longest))
        self.send_line()
        self.send_line(f"[{ansi.bold('enter')}] to go to the next photo, [{ansi.bold('q')}] to return to main menu")

    async def render_ascii(self, url: str) -> str:
        # Multiply by Monaco font proportions to determine whether the window is wider or taller.
        if self.width * 7 > self.height * 17:
            args = [f"--height={max(self.height - 7, 1)}"]
        else:
            args = [f"--width={self.width}"]
        if self.colors:
            args += ["--colors", f"--color-depth={self.color_depth}"]

        try:
            image = await self.bbs.flickr.download(url)
            proc = await asyncio.create_subprocess_exec(
                "jp2a", *args, "-",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate(image)
        except Exception:
            log.exception("failed to render %s", url)
            return "(couldn't render this photo)\n"
        if proc.returncode != 0:
            log.warning("jp2a failed for %s: %s", url, stderr.decode(errors="replace").strip())
            return "(couldn't render this photo)\n"
        return stdout.decode("utf-8", errors="replace")
