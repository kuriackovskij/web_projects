# DAA Portal

A self-hosted, Docker-based markdown portal with obfuscated URLs and no authentication.
All URLs are hard to guess; only the index URL and direct article links are usable.

## Scope
Markdown files representer as website without coding and without working with html.

## Usage high-level overview
- Create a folder under the content/ - that will be an "article" category
- drop .md file into that folder - that is your article which will be accessible from DAA html portal straight away

---

## How it works (security model)

| Layer | Detail |
|---|---|
| Default root `/` | Returns `204 No Content` — nothing to scrape or attack |
| Index URL | 40-char random hex, generated on first start and stored in `content/.index_secret` |
| Article URLs | SHA-256 of the relative file path, truncated to 32 hex chars, stored in SQLite |
| Hidden files | `.index_secret` and `.mappings.db` can never be served over HTTP (rejected by extension check + hidden-file guard + never registered in the DB) |
| Rate limiting | 240 requests / minute · 8 requests / second per IP (in-app, before the proxy) |
| Security headers | `noindex`, `nofollow`, `noarchive`, strict CSP, no `Server` header |
| Port binding | Bound to `0.0.0.0:6898` — UFW restricts access to the nginx server's IP only |

Brute-forcing article paths: at 240 req/min the SHA-256 space (2¹²⁸ paths) would take longer than the age of the universe.

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
scp -r /path/to/your/website user@your-server:~/portal
```

Or clone / pull directly on the server — whatever you prefer.

### 2 — Build and start

All commands are run from the `website/` directory (where `docker-compose.yml` lives).

```bash
cd ~/portal

docker compose up -d --build
```

The `content/` directory (relative to `docker-compose.yml`) is automatically created if it does not exist, together with the category folders when you add them.

### 3 — Find your index URL

On **first** start the app generates a secret token and writes it to `content/.index_secret`.

```bash
cat content/.index_secret
# Example output: dda9023f4f1851c33f231ae3b4f4598eca1f7c42
```

Your index is reachable at:

```
https://yourdomain.com/dda9023f4f1851c33f231ae3b4f4598eca1f7c42
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
website/                   ← docker compose root (copy this to the server)
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

    location / {
        proxy_pass         http://PORTAL_SERVER_IP:6898;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_redirect     off;

        # Prevent nginx from buffering large markdown responses
        proxy_buffering    off;
    }
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

## Firewall (UFW)

The nginx reverse proxy is on a **separate host**, so the app binds on all interfaces (`0.0.0.0:6898`).
Use UFW to whitelist only the nginx server's IP — everything else is denied.

Replace `NGINX_SERVER_IP` with the actual IP of the machine running nginx.

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow ssh

# Allow only the nginx server to reach the portal on port 6898
sudo ufw allow from NGINX_SERVER_IP to any port 6898 proto tcp

# Do NOT open port 6898 to the world — the above rule is the only one for it
sudo ufw enable
sudo ufw status
```

Expected output for port 6898:
```
6898/tcp        ALLOW IN    NGINX_SERVER_IP
```

To verify after enabling:
```bash
# From the nginx host — should get a response
curl -s -o /dev/null -w "%{http_code}" http://PORTAL_SERVER_IP:6898/

# From any other host — should time out or be refused
curl --connect-timeout 3 http://PORTAL_SERVER_IP:6898/
```

> **Note:** Docker's `iptables` rules bypass UFW by default on Ubuntu.
> To make UFW rules apply to Docker-exposed ports, add the following to
> `/etc/docker/daemon.json` on the portal server and restart Docker:
>
> ```json
> {
>   "iptables": false
> }
> ```
>
> Then restart Docker: `sudo systemctl restart docker` and re-run `docker compose up -d`.
> Without this, UFW `deny` rules will **not** block traffic to Docker-bound ports.

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
