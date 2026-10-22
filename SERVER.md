# spaceclaw server setup

How to build a production box for spaceclaw from scratch.

- The app runs under [uv](https://docs.astral.sh/uv/) in a virtualenv,
  supervised by a systemd unit.
- The unit grants `CAP_NET_BIND_SERVICE` to the service alone, so it can bind
  telnet's port 23 without root and without `setcap` on the Python binary.
- **Data is one SQLite file** at `/var/lib/spaceclaw/spaceclaw.db`, outside the
  code directory so deploys can't clobber it.
- **The web app binds to 127.0.0.1:8080**, behind nginx for TLS.

## Architecture

```
telnet :23  ──────────────────────────────►  spaceclaw serve (systemd)  ──► /var/lib/spaceclaw/spaceclaw.db
https  :443 ─► nginx (TLS, certbot) ─► 127.0.0.1:8080 ─┘                ──► jp2a (subprocess, per photo)
http   :80  ─► nginx ─► 301 to https
```

- One Python process (`spaceclaw serve`) runs both the telnet BBS on **23** and
  the Starlette web app on **127.0.0.1:8080**. The web app only handles the
  Flickr OAuth login (`/login/<id>/`, `/callback`) and the landing pages.
- The public hostname comes from `public_url` (default `https://spaceclaw.net`).
  The OAuth callback is `<public_url>/callback`. If the domain changes, set
  `SPACECLAW_PUBLIC_URL` **and** update the callback URL in the Flickr API app
  settings.
- SQLite holds two tables: `users` (Flickr profile, OAuth tokens, unique
  passphrase) and `oauth_requests` (in-flight logins, expired after an hour).
  There are no web sessions or cookies.

## Setup steps

Written for **Ubuntu 24.04 LTS** (its Python is 3.12, which the app needs).
Run as root unless noted.

### 1. User and sudo

```sh
adduser bertrand
usermod -aG sudo bertrand
echo 'bertrand ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/91-bertrand-nopasswd
chmod 440 /etc/sudoers.d/91-bertrand-nopasswd
```

As `bertrand`, install SSH keys:

```sh
mkdir -p ~/.ssh && chmod 700 ~/.ssh
curl https://github.com/<github-user>.keys >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```

### 2. SSH hardening

Move sshd off port 22. This doc writes the port as `<ssh-port>`; pick your own
and keep it out of the repo. `/etc/ssh/sshd_config.d/10-port.conf`:

```
Port <ssh-port>
```

In `/etc/ssh/sshd_config`:

```
PasswordAuthentication no
PermitEmptyPasswords no
```

On 24.04 sshd is socket-activated, so a port change also needs the socket reloaded:

```sh
systemctl daemon-reload
systemctl restart ssh.socket ssh.service
```

**Test a new session on `<ssh-port>` before closing the current one.**

On your laptop, add a host alias so the rest of this doc can just say `ssh spaceclaw`
(`~/.ssh/config`):

```
Host spaceclaw
    HostName spaceclaw.net
    User bertrand
    Port <ssh-port>
```

### 3. Firewall + fail2ban

```sh
apt update && apt upgrade -y
apt install -y ufw fail2ban

ufw allow <ssh-port>       # ssh, do this BEFORE enabling
ufw allow 23/tcp           # telnet BBS
ufw allow "Nginx Full"     # 80,443/tcp (profile exists once nginx is installed, step 6)
ufw enable
ufw status verbose
```

Port 8080 needs no rule. The app binds it to localhost only.

fail2ban stays stock (just the default `sshd` jail).

### 4. Packages

```sh
apt install -y curl ca-certificates rsync jp2a sqlite3 man-db
```

- `jp2a` is **required**. The app pipes each downloaded photo through it to
  make the ASCII art. Ubuntu's build supports `--colors` and `--color-depth`.
- `sqlite3` (the CLI) is for backups and poking at the DB. The app uses
  Python's built-in `sqlite3` module.

### 5. uv (as `bertrand`)

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh    # installs to ~/.local/bin/uv
source ~/.local/bin/env
uv --version
```

uv uses the system Python 3.12, or downloads a managed one if needed.

### 6. nginx + TLS (certbot via snap)

```sh
apt install -y nginx snapd
snap install --classic certbot
ln -s /snap/bin/certbot /usr/bin/certbot
```

`/etc/nginx/sites-available/spaceclaw.net`:

```nginx
server {
    server_name spaceclaw.net;
    listen 80;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```sh
ln -s /etc/nginx/sites-available/spaceclaw.net /etc/nginx/sites-enabled/
rm /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
certbot --nginx -d spaceclaw.net     # DNS must already point at this box
```

Run it without a terminal, or to skip the prompts, with
`--non-interactive --agree-tos --register-unsafely-without-email --redirect`.

Certbot adds the `listen 443 ssl` block and the http→https redirect. Renewal
runs automatically via `snap.certbot.renew.timer`.

### 7. Deploy the app (as `bertrand`)

The code lives at `/home/bertrand/spaceclaw`. Copy it up from your local checkout:

```sh
# from the local repo
rsync -av --delete \
  --exclude .git --exclude .venv --exclude __pycache__ --exclude .DS_Store \
  --exclude '*.db*' --exclude config/secrets.json \
  ./ spaceclaw:~/spaceclaw/
```

(Or `git clone` it on the server.)

On the server:

```sh
cd ~/spaceclaw
uv sync --frozen --no-dev        # creates ~/spaceclaw/.venv from uv.lock
cp config/secrets.json.example.json config/secrets.json
chmod 600 config/secrets.json
nano config/secrets.json
```

`config/secrets.json` keys:

- `flickr_api_key`
- `flickr_api_secret`

`backdoor_passphrase` is ignored outside dev mode. Any key can also be set as a
`SPACECLAW_<KEY>` env var in the systemd unit.

### 8. systemd service

The unit file is in the repo at `deploy/spaceclaw.service`:

```sh
sudo cp ~/spaceclaw/deploy/spaceclaw.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now spaceclaw
systemctl status spaceclaw
journalctl -u spaceclaw -f          # logs (see step 10)
```

What the unit does:

- Runs `~/spaceclaw/.venv/bin/spaceclaw serve` as `bertrand` and restarts it if it dies.
- `AmbientCapabilities=CAP_NET_BIND_SERVICE` lets it bind port 23 without root.
- `StateDirectory=spaceclaw` creates `/var/lib/spaceclaw` owned by `bertrand`
  (mode 700, files 600 via `UMask=0077`, since the DB holds OAuth tokens), and
  `SPACECLAW_DB_PATH` points the DB there. The schema is created on first start.
- Home is mounted read-only for the service, so code and secrets can't be modified by it.

**Redeploying:** from the local repo, run:

```sh
./deploy.sh
```

It runs the tests, rsyncs the code (never touching the server's
`config/secrets.json` or database), runs `uv sync`, installs the systemd unit
if it changed, restarts the service, and checks that port 23 and
`https://spaceclaw.net` respond. `SPACECLAW_HOST` overrides the `spaceclaw`
SSH alias, and `SKIP_TESTS=1` skips the tests.

### 9. Backups

The whole state is one file. A nightly online backup, keeping a week, as
`bertrand` (`crontab -e`):

```cron
15 4 * * * sqlite3 /var/lib/spaceclaw/spaceclaw.db ".backup /var/lib/spaceclaw/backup-$(date +\%a).db"
```

`.backup` is safe while the app is running. Copy the backups off-box if you care
about them (the DB holds Flickr OAuth tokens, so keep copies private).

### 10. Log rotation

spaceclaw logs to stdout, which systemd stores in the journal. Cap the journal
so it can't fill the disk (`/etc/systemd/journald.conf.d/spaceclaw.conf`):

```ini
[Journal]
SystemMaxUse=200M
SystemKeepFree=1G
MaxRetentionSec=3month
```

```sh
systemctl restart systemd-journald
usermod -aG systemd-journal bertrand     # read the journal without sudo
```

Certbot keeps up to 1000 old logs by default; trim that:

```sh
echo "max-log-backups = 30" >> /etc/letsencrypt/cli.ini
```

Everything else is already handled by Ubuntu's stock logrotate config:
nginx keeps 14 days, and syslog, auth, fail2ban and ufw rotate weekly. Backups
from step 9 overwrite themselves after a week.

## Verify

```sh
sudo ufw status verbose
sudo ss -tlnp            # expect :23 spaceclaw (0.0.0.0), 127.0.0.1:8080 spaceclaw, :80/:443 nginx, :<ssh-port> sshd
systemctl is-enabled spaceclaw nginx fail2ban ufw
systemctl status spaceclaw
curl -I https://spaceclaw.net
telnet spaceclaw.net 23  # log in via the Flickr link, then try [1] and [2]
sqlite3 /var/lib/spaceclaw/spaceclaw.db 'select nsid, username, passphrase is not null from users'
sudo certbot renew --dry-run   # without a TTY it first sleeps a random 0–8 min
```

## Notes

- Telnet clients that don't send their window size (NAWS) get the login screen
  after 3 seconds, sized as 80×24.
