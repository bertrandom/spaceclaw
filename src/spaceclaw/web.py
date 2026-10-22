"""The web side: Flickr OAuth login for telnet sessions, plus the static landing pages."""

from __future__ import annotations

import logging
from importlib import resources

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, PlainTextResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .bbs import BBS
from .db import OAuthRequest, User

log = logging.getLogger(__name__)

STATIC_DIR = resources.files("spaceclaw") / "static"


def create_app(bbs: BBS) -> Starlette:
    async def index(request: Request) -> Response:
        return FileResponse(STATIC_DIR / "index.html")

    async def loggedin(request: Request) -> Response:
        return FileResponse(STATIC_DIR / "loggedin.html")

    async def login(request: Request) -> Response:
        session_id = request.path_params["session_id"]
        if session_id not in bbs.sessions:
            return PlainTextResponse(
                "That login link has expired. Reconnect with telnet to get a new one.", status_code=404
            )
        try:
            token, secret = await bbs.flickr.get_request_token(bbs.config.callback_url)
        except Exception:
            log.exception("request token failed")
            return PlainTextResponse("Flickr has the hiccups, please try again later.", status_code=502)
        bbs.db.save_oauth_request(OAuthRequest(token, secret, session_id))
        return RedirectResponse(bbs.flickr.authorize_url(token), status_code=302)

    async def callback(request: Request) -> Response:
        token = request.query_params.get("oauth_token", "")
        verifier = request.query_params.get("oauth_verifier", "")
        pending = bbs.db.pop_oauth_request(token)
        if pending is None or not verifier:
            return PlainTextResponse(
                "That login has expired. Reconnect with telnet to get a new link.", status_code=400
            )

        try:
            access = await bbs.flickr.get_access_token(pending.token, pending.token_secret, verifier)
            person = await bbs.flickr.get_person(
                access["oauth_token"], access["oauth_token_secret"], access["user_nsid"]
            )
        except Exception:
            log.exception("OAuth callback failed")
            return PlainTextResponse(
                "There was a problem connecting Space Claw to Flickr, please try again later.", status_code=502
            )

        user = User(
            nsid=person["nsid"],
            username=person.get("username") or access.get("username") or person["nsid"],
            realname=person.get("realname") or None,
            path_alias=person.get("path_alias") or None,
            oauth_token=access["oauth_token"],
            oauth_token_secret=access["oauth_token_secret"],
        )
        profile = {k: v for k, v in person.items() if k not in ("timezone", "photos")}
        bbs.db.upsert_user(user, profile)

        if not bbs.complete_auth(pending.telnet_session_id, user):
            return PlainTextResponse(
                "You're signed in, but your telnet session has disconnected. "
                "Reconnect and log in again to get a passphrase."
            )
        return RedirectResponse("/loggedin", status_code=302)

    return Starlette(
        routes=[
            Route("/", index),
            Route("/loggedin", loggedin),
            Route("/login/{session_id}/", login),
            Route("/login/{session_id}", login),
            Route("/callback", callback),
            Mount("/webfonts", StaticFiles(directory=str(STATIC_DIR / "webfonts")), name="webfonts"),
        ]
    )
