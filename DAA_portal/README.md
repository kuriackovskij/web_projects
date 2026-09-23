# DAA Portal

A self-hosted, Docker-based Markdown portal with private index and article URLs.
Readers use those URLs; article management requires an authenticated API.
DAA = Dynamic Articles Aggregator

## Deployment model

The reader site is `https://daa.aleksk.eu`. Private runtime content is a host
bind mount. Keeping `.index_secret` and `.mappings.db` with the Markdown files
preserves existing index and article URLs when moving the service. Articles
added directly as `<Category>/<filename>.md` under the mounted content
directory appear on the next index load without a rebuild or restart.

Do not commit live content, `.index_secret`, `.mappings.db`, `.env`, backups, or
credentials to this public repository. The committed welcome article is a dummy
sample only. The tracked `content/.index_secret.example` shows the one-line,
40-hex-character format using an all-zero **dummy** value; it is not an active
secret and must never be copied into `content/.index_secret` for a deployment.
Let the app generate a fresh private index secret instead, or preserve the
existing private one during migration. `.gitignore` and `.dockerignore`
exclude runtime content; keep private data in the host bind mount, outside a
Git checkout.

## Hermes article API

For the complete request/response contract, curl examples for every operation,
status codes, path rules, and limitations, see [API.md](API.md). This README
covers deployment and the bundled client setup.

Hermes invokes `daa-client`, which sends bearer-authenticated requests over
HTTPS to an internal-only API listener. No SSH tunnel or SSH key is required.
The public reader listener on port 443 continues to block `/api/`, even for
readers admitted through an internet access gate. This deployment's API
origin is `https://daa.aleksk.eu:8443`, available only through LAN or an
approved Tailnet subnet route. Tailnet clients need internal DNS resolution
for `daa.aleksk.eu`; do not disable TLS verification to work around DNS.

The API token has two copies: `DAA_API_TOKEN` in a protected `.env` beside the
portal's `docker-compose.yml`, and the same value in the agent host's
`~/.config/daa/client.env` (mode 0600), or the path named by
`DAA_CLIENT_CONFIG`. The client config needs only the HTTPS origin and token;
the same setup works on another LAN/Tailnet VM. Never put the token in Git,
an article, a skill, a URL, or a command line.

Example agent config (placeholders only; do not commit a real copy):

```dotenv
DAA_API_TOKEN=<same-random-token-as-portal-env>
DAA_API_BASE_URL=https://daa.aleksk.eu:8443
```

Keep the client config readable only by its agent account (mode 0600), and
keep the portal `.env` readable only by its administrator. The client verifies
the server certificate/hostname, refuses redirects, and bypasses environment
HTTP proxies. The API returns `401` for missing/invalid tokens and fails
closed with `503` if no token is configured. A stolen token also needs access
to the private API network. Rotate the token if it may have been exposed.

```bash
daa-client list
daa-client put News/example.md /path/to/local/article.md
daa-client get News/example.md /path/to/new-local-copy.md
daa-client move News/example.md Research/renamed.md
daa-client delete Research/renamed.md
```

`put` creates or fully updates a Markdown article and returns its stable path
hash; `get` reads its raw Markdown. `move` renames an article, changes its
category, or both, without overwriting an occupied destination. Moving changes
the path hash and reader URL.
The corresponding HTTP endpoints are `GET /api/v1/articles` and `GET`, `PUT`,
`DELETE /api/v1/articles/<Category>/<filename>.md`, plus
`POST /api/v1/articles/<Category>/<filename>.md/move`; `PUT` requires
`Content-Type: text/markdown` and has a 1 MiB body limit. Only one category
level is accepted. Existing direct filesystem writes on the portal host continue to
work. Pluto terminates HTTPS and forwards to the portal backend over the LAN.
Per the deployment policy, the portal host's existing direct LAN HTTP port remains
unchanged; clients should use the private HTTPS origin, not that port.

For a new deployment, generate a random 32-byte or longer token and place it
as `DAA_API_TOKEN=<value>` in a protected `.env` beside `docker-compose.yml`.
The application runs as UID/GID 10001, so the private content directory must
be writable by that identity. Set `DAA_BIND_IP` in the protected `.env` for a
LAN binding; the Compose default is loopback. The helper additionally needs
`DAA_API_BASE_URL` in its protected client config. Block `/api/` on the public
listener. Put the private HTTPS listener on a LAN-only interface/port, deny
non-LAN sources there, and do not forward that port from the internet.

### Cutover and recovery checks

Before a migration, stop writes, archive the entire private content tree, and
compare a sorted per-file hash manifest on source and target. The root endpoint
should return HTTP 204; the private index should render the expected article
count, and the helper's `list` command should enumerate live paths.
For recovery, stop the container before restoring the archived content
tree, preserve the index secret and mapping database together, then restart and
verify the index and a direct article URL. Never publish the archive or its
contents to Git.

## Scope
Markdown files representer as website without coding and without working with html.

## Usage high-level overview
- Create a folder under the content/ - that will be an "article" category
- drop .md file into that folder - that is your article which will be accessible from DAA html portal straight away

## Use-cases
Might be plenty of use-cases like hosting a simple website, blog etc
My use-case includes AI-generated Markdown articles written directly to the filesystem or published remotely through the authenticated API.

---

## How it works (security model)

| Layer | Detail |
|---|---|
| Default root `/` | Returns `204 No Content` with an empty body |
| Unknown reader path | Returns `404 Not Found` with an empty body; DAA does not use NGINX's non-standard `444` |
| Index URL | 40-char random hex, generated on first start and stored in `content/.index_secret` |
| Article URLs | Deterministic SHA-256 of the relative file path, truncated to 32 hex chars, stored in SQLite; the hash is not authentication |
| Hidden files | `.index_secret` and `.mappings.db` can never be served over HTTP (rejected by extension check + hidden-file guard + never registered in the DB) |
| Rate limiting | Nominally 240 requests / minute and 8 requests / second per reported client address, in-app and in-memory; not a hard security boundary |
| Security headers | `noindex`, `nofollow`, `noarchive`, CSP, and other response headers set by the app |
| Port binding | Configurable via `DAA_BIND_IP`; restrict upstream network access separately from application auth |

Blindly guessing a 32-hex-character article hash is impractical, but anyone
who guesses an article's category and filename can calculate its hash offline.
The private index URL is separately generated at random. The existing empty
`204`/`404` reader behavior is intentional; switching to `444` would not make
predictable article paths harder to discover.

---

## Prerequisites (Ubuntu 24.04)

```bash
# Docker Engine
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
newgrp docker

# Verify
docker --version
docker compose version
```

---

## Deployment

### 1 — Copy the repo to the server

```bash
# On your local machine
scp -r /path/to/web_projects/DAA_portal user@your-server:~/portal
```

Or clone / pull directly on the server — whatever you prefer.

### 2 — Build and start

All commands are run from the copied `DAA_portal/` directory (where
`docker-compose.yml` lives).

```bash
cd ~/portal

docker compose up -d --build
```

The `content/` directory (relative to `docker-compose.yml`) is automatically created if it does not exist, together with the category folders when you add them.

### 3 — Find your index URL

On **first** start the app generates a secret token and writes it to `content/.index_secret`.

```bash
cat content/.index_secret
# Example output: <generated-40-hex-character-value>
```

Your index is reachable at:

```
https://yourdomain.com/<generated-40-hex-character-value>
```

**Keep this value private.** It never changes unless you delete the file.

---

## Managing content

### Add a category

```bash
mkdir ~/portal/content/Photography
```

Reload the index page — the new category appears immediately.
No restart required.

### Add an article

```bash
cp my-article.md ~/portal/content/Photography/paris-trip.md
```

The article appears on the index at the next page load.
The article URL (hash) is computed from the file path; copy it from the index to share.

To share a direct link to someone:
1. Open the index page
2. Right-click the article → **Copy link address**
3. Send that URL — they see only that article, with no navigation to anything else

### Edit an article

Edit the `.md` file directly on the filesystem:

```bash
nano ~/portal/content/Photography/paris-trip.md
# or
vim ~/portal/content/Photography/paris-trip.md
```

Changes are live immediately on the next page load.
The article URL does not change when you edit the content.

### Rename or move an article

```bash
mv ~/portal/content/Photography/paris-trip.md \
   ~/portal/content/Leisure/paris-trip.md
```

**Note:** renaming changes the file path, which changes the SHA-256 hash, which changes the URL.
The old URL stops working. The new URL appears on the index on the next load.

### Remove an article

```bash
rm ~/portal/content/Photography/paris-trip.md
```

The article disappears from the index on the next load.
Its stale hash entry stays in `content/.mappings.db` (harmless — the file no longer exists so requests to it return 404).

### Remove a category

```bash
rm -rf ~/portal/content/Photography
```

The category disappears from the index on the next load.

---

## Directory structure

```
DAA_portal/                ← docker compose root (copy this to the server)
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── app.py
└── content/               ← mapped volume (persists across container rebuilds)
    ├── .index_secret      ← auto-generated; contains the secret index path
    ├── .mappings.db       ← SQLite; hash → file path mappings
    ├── News/
    │   └── my-article.md
    ├── General/
    ├── Tech articles/
    ├── Philosophy/
    ├── Health/
    ├── Leisure/
    └── Other/
```

Everything inside `content/` survives `docker compose down`, `docker compose up --build`, and image rebuilds because it is a bind-mounted host directory.

---

## Nginx reverse proxy (Optional)

Install nginx on the host:

```bash
sudo apt install -y nginx certbot python3-certbot-nginx
```

Create `/etc/nginx/sites-available/portal`:

```nginx
# Rate-limit zone — defined in http context (add to /etc/nginx/nginx.conf
# inside the http { } block if not using sites-available includes)
# limit_req_zone $binary_remote_addr zone=portal:10m rate=60r/m;

server {
    listen 80;
    server_name yourdomain.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name yourdomain.com;

    ssl_certificate     /etc/letsencrypt/live/yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yourdomain.com/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;

    # Hide nginx version
    server_tokens off;

    # Additional rate limiting at the nginx layer (optional but recommended)
    # limit_req zone=portal burst=20 nodelay;

    # Public reader ingress must never forward article-management calls.
    location ^~ /api/ { return 404; }

    location / {
        proxy_pass         http://PORTAL_SERVER_IP:6898;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $remote_addr;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_redirect     off;

        # Prevent nginx from buffering large markdown responses
        proxy_buffering    off;
    }
}

# Separate listener: bind only to the proxy's LAN address. Never forward this
# port from the internet. Approved Tailnet subnet-route traffic must arrive
# from the trusted LAN range (the usual SNAT setup).
server {
    listen PORTAL_PROXY_LAN_IP:8443 ssl;
    server_name yourdomain.com;
    ssl_certificate     /etc/letsencrypt/live/yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yourdomain.com/privkey.pem;
    allow TRUSTED_LAN_CIDR;
    deny all;

    location ^~ /api/v1/ {
        proxy_pass http://PORTAL_SERVER_IP:6898;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto https;
    }
    location / { return 404; }
}
```

Enable and reload:

```bash
sudo ln -s /etc/nginx/sites-available/portal /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx

# Obtain TLS certificate
sudo certbot --nginx -d yourdomain.com
```

---

## Network boundary

The deployed API listener is on the reverse proxy's LAN address and is
allowed only from the trusted LAN CIDR by both proxy access rules and the
proxy host firewall. Only reader port 443 is forwarded from the internet;
the public listener always returns 404 for `/api/`, even after IP-Beamer
admits a reader. Verify the private listener from a LAN/Tailnet client and
verify the public URL's `/api/` is still blocked. Tailnet clients must have
the approved subnet route and internal DNS resolution for the certificate
hostname.

The portal backend port 6898 remains LAN-bound and unchanged in this
deployment, per operator choice. It is plain HTTP, so management clients
should use the private HTTPS proxy address. A stricter backend firewall is
an optional future change, not a prerequisite for this setup. Docker-published
ports may bypass ordinary UFW input rules, so do not assume a UFW rule alone
protects a published backend.

---

## Useful commands

```bash
# View live logs
docker compose logs -f

# Restart the app (e.g. after a code change)
docker compose restart

# Rebuild after updating app.py or requirements.txt
docker compose up -d --build

# Stop
docker compose down

# Check what's running
docker compose ps
```

---

## Updating the application

```bash
cd ~/portal

# Pull latest code (or copy updated files manually)
git pull   # if using git

# Rebuild and restart
docker compose up -d --build
```

Content in `content/` is never touched by a rebuild.

---

## Recovering the index URL

If you lose the index URL:

```bash
cat ~/portal/content/.index_secret
```

The secret does not change unless you delete that file.
If you delete it, a new one is generated on the next container start and all previous index links stop working (article links are unaffected).
