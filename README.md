# space claw

Almost certainly the best Flickr BBS.

If you'd like to see it in action, open a terminal and type:

	telnet spaceclaw.net

<img width="640" height="487" alt="687474703a2f2f6661726d392e737461746963666c69636b722e636f6d2f383234392f383539343933383134325f333933643333646538305f7a2e6a7067" src="https://github.com/user-attachments/assets/82a6ec9d-c9d2-47b5-a51c-16178fc25098" />

This is a Python rewrite of the original [Node.js app](https://github.com/bertrandom/spaceclaw-nodejs). A single process runs both
the telnet BBS (port 23) and the small web app that handles Flickr OAuth login
(port 8080). Users and their passphrases live in SQLite.

## Development

Requirements: [uv](https://docs.astral.sh/uv/) and `jp2a` (`brew install jp2a` /
`apt install jp2a`).

```sh
uv sync
cp config/secrets.json.example.json config/secrets.json   # add your Flickr API key/secret
SPACECLAW_DEV=1 uv run spaceclaw serve
```

In dev mode telnet listens on port 2323 and the login links point at
`http://localhost:8080`, so set your Flickr app's callback to
`http://localhost:8080/callback` (or set `SPACECLAW_PUBLIC_URL`). Then:

```sh
telnet localhost 2323
```

Once you've logged in via Flickr, the `backdoor_passphrase` from `secrets.json`
logs in as `SPACECLAW_ADMIN_NSID` (dev mode only).

Run the tests with `uv run pytest`.

## Configuration

Settings come from `config/secrets.json` (path overridable with `--secrets` or
`SPACECLAW_SECRETS`), and any key can be overridden by a `SPACECLAW_<KEY>`
environment variable.

| Key | Default | |
|---|---|---|
| `flickr_api_key`, `flickr_api_secret` | required | Flickr app credentials |
| `public_url` | `https://spaceclaw.net` | Base URL for login links and the OAuth callback |
| `db_path` | `spaceclaw.db` | SQLite database file |
| `telnet_host`, `telnet_port` | `0.0.0.0`, `23` (`2323` in dev) | |
| `web_host`, `web_port` | `127.0.0.1`, `8080` | Put nginx in front for TLS |
| `dev` | off | Also enabled by `SPACE_CLAW_DEV` |
| `backdoor_passphrase`, `admin_nsid` | | Dev-only shortcut login |

## Deployment

See [SERVER.md](SERVER.md) for setting up a server. To ship changes to it:

```sh
./deploy.sh
```
