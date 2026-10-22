"""Flickr OAuth 1.0a and REST API client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx2 as httpx
from authlib.integrations.httpx_client import AsyncOAuth1Client

from . import __version__

REQUEST_TOKEN_URL = "https://www.flickr.com/services/oauth/request_token"
AUTHORIZE_URL = "https://www.flickr.com/services/oauth/authorize"
ACCESS_TOKEN_URL = "https://www.flickr.com/services/oauth/access_token"
REST_URL = "https://api.flickr.com/services/rest"

# Flickr's CDN answers the default python-httpx User-Agent with a 502.
HEADERS = {"User-Agent": f"spaceclaw/{__version__} (+https://spaceclaw.net)"}


class FlickrError(Exception):
    pass


@dataclass(frozen=True)
class Photo:
    id: str
    owner: str
    title: str
    url_s: str
    pathalias: str | None = None
    username: str | None = None

    @property
    def page_url(self) -> str:
        return f"https://www.flickr.com/photos/{self.pathalias or self.owner}/{self.id}/"

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Photo:
        return cls(
            id=data["id"],
            owner=data["owner"],
            title=data.get("title") or "",
            url_s=data["url_s"],
            pathalias=data.get("pathalias") or None,
            username=data.get("username"),
        )


@dataclass(frozen=True)
class PhotoPage:
    photos: list[Photo]
    page: int
    pages: int


def unwrap_content(data: Any) -> Any:
    """Replace Flickr's {"_content": x} wrappers with x, recursively."""
    if isinstance(data, dict):
        if "_content" in data:
            return data["_content"]
        return {k: unwrap_content(v) for k, v in data.items()}
    if isinstance(data, list):
        return [unwrap_content(v) for v in data]
    return data


class Flickr:
    def __init__(self, api_key: str, api_secret: str) -> None:
        self.api_key = api_key
        self.api_secret = api_secret

    def _client(self, token: str | None = None, token_secret: str | None = None, **kwargs: Any) -> AsyncOAuth1Client:
        return AsyncOAuth1Client(
            self.api_key, self.api_secret, token=token, token_secret=token_secret, timeout=30, headers=HEADERS, **kwargs
        )

    # OAuth handshake

    async def get_request_token(self, callback_url: str) -> tuple[str, str]:
        async with self._client(redirect_uri=callback_url) as client:
            token = await client.fetch_request_token(REQUEST_TOKEN_URL)
        return token["oauth_token"], token["oauth_token_secret"]

    @staticmethod
    def authorize_url(request_token: str) -> str:
        return f"{AUTHORIZE_URL}?oauth_token={request_token}&perms=read"

    async def get_access_token(self, request_token: str, request_secret: str, verifier: str) -> dict[str, str]:
        """Returns oauth_token, oauth_token_secret, user_nsid, username, fullname."""
        async with self._client(request_token, request_secret) as client:
            return dict(await client.fetch_access_token(ACCESS_TOKEN_URL, verifier=verifier))

    # REST API

    async def call(self, token: str, token_secret: str, method: str, **params: Any) -> dict[str, Any]:
        query = {"method": method, "format": "json", "nojsoncallback": "1", **params}
        try:
            async with self._client(token, token_secret) as client:
                response = await client.get(REST_URL, params=query)
            response.raise_for_status()
            result = response.json()
        except (httpx.HTTPError, ValueError) as e:
            raise FlickrError(str(e)) from e
        if result.get("stat") != "ok":
            raise FlickrError(result.get("message", "unknown error"))
        return result

    async def get_person(self, token: str, token_secret: str, nsid: str) -> dict[str, Any]:
        result = await self.call(token, token_secret, "flickr.people.getInfo", user_id=nsid)
        return unwrap_content(result["person"])

    async def get_photostream(self, token: str, token_secret: str, nsid: str, page: int = 1) -> PhotoPage:
        result = await self.call(
            token, token_secret, "flickr.people.getPhotos",
            user_id=nsid, extras="url_s,path_alias", per_page=25, page=page,
        )
        photos = result["photos"]
        return PhotoPage(_photos(photos["photo"]), int(photos["page"]), int(photos["pages"]))

    async def get_contacts_photos(self, token: str, token_secret: str) -> list[Photo]:
        result = await self.call(
            token, token_secret, "flickr.photos.getContactsPhotos", extras="url_s,path_alias", count=50
        )
        return _photos(result["photos"]["photo"])

    async def download(self, url: str) -> bytes:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers=HEADERS) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.content


def _photos(items: list[dict[str, Any]]) -> list[Photo]:
    # Photos without a small size (e.g. still processing) can't be rendered.
    return [Photo.from_api(p) for p in items if p.get("url_s")]
