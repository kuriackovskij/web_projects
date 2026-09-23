# DAA Portal article API v1

This is the complete client-facing contract for the current article-management
API. It describes the behavior implemented in `app.py`, not proposed features.
The API manages UTF-8 Markdown files in a private content directory. A human
or agent can also use `clients/daa_client.py`, which implements the same four
operations and manages the SSH tunnel automatically.

## Where to send requests

The public reader URL is **not** an API base URL: the public reverse proxy
blocks `/api/`. The backend speaks plain HTTP on its configured bind address
and port 6898. Remote clients should connect through an SSH local forward, so
the token and article content travel inside the encrypted SSH connection.

The bundled `daa-client` starts and closes that forward for each command:

```text
daa-client list
daa-client put News/example.md /path/to/local/article.md
daa-client get News/example.md /path/to/new-local-copy.md
daa-client delete News/example.md
```

For a different HTTP client, start an equivalent forward in a separate
terminal. Replace every angle-bracketed value with your own deployment
details; none of these are live usernames, paths, addresses, or keys:

```bash
ssh -N -L 127.0.0.1:18998:<backend-bind-address>:6898 \
  -i /path/to/private-article-key \
  -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes \
  -o ExitOnForwardFailure=yes <restricted-ssh-user>@<portal-ssh-host>
```

Here, `<backend-bind-address>:6898` is the destination **as seen by the SSH
server**; it must match that key's `permitopen` restriction. Keep this SSH
session running while making HTTP requests to `http://127.0.0.1:18998` on the
client machine, then close it. Verify the SSH host key before first use. Do
not send the bearer token over unencrypted LAN HTTP or to the public site.

## Authentication and request conventions

Every API request requires this header:

```http
Authorization: Bearer <api-token>
```

`<api-token>` is the same random, at-least-32-character value configured as
`DAA_API_TOKEN` in the portal's protected `.env` and the client's protected
config. The bundled client reads `~/.config/daa/client.env` (mode 0600):

```dotenv
DAA_API_TOKEN=<same-token-as-portal-env>
DAA_SSH_DESTINATION=<restricted-ssh-user>@<portal-ssh-host>
DAA_FORWARD_TARGET=<backend-bind-address>:6898
DAA_SSH_IDENTITY=/path/to/private-article-key
```

The dedicated SSH private key stays on the client machine. Only its public
key goes in the portal host's `authorized_keys`, restricted to the exact
backend target and without shell access. Never commit the token, private key,
client config, `.env`, or real articles. Do not put a real token directly in
shell commands, shell history, an article, or a URL. The SSH key and API token
are separate credentials; neither replaces the other.

For a new deployment, generate a random token with at least 32 characters
(for example, `openssl rand -hex 32`) and put the identical value in the
portal's protected `.env` as `DAA_API_TOKEN=<token>` and in the client config
above. Restrict both files to their respective service/agent account. A
generic `authorized_keys` line for the dedicated article key is:

```text
restrict,port-forwarding,permitopen="<backend-bind-address>:6898",command="/bin/false" ssh-ed25519 <public-key> <comment>
```

The `permitopen` target must match `DAA_FORWARD_TARGET` exactly. The helper
requires Python 3 and OpenSSH; it can be run from a source checkout as
`python3 clients/daa_client.py <command> ...` or installed as `daa-client`.

All paths below are relative to the tunnel's HTTP base URL. All API responses
set `Cache-Control: no-store`. Responses are not guaranteed to have a JSON
body unless the success response below explicitly says so.

## Article path rules

The article identifier is exactly `<Category>/<filename>.md`, for example
`News/example.md`. It has one category component and one filename component;
there is no nested category support. The filename must end in `.md`
case-insensitively. Neither component may be empty, begin with `.`, be `.` or
`..`, contain a backslash, or refer to a symbolic link. Non-file entries are
not articles. Use ordinary names without slashes inside either component;
percent-encode URL characters such as spaces (`%20`) and non-ASCII characters
when constructing an HTTP URL. The path is case-sensitive on a typical Linux
host: `News/example.md` and `news/example.md` are different articles.

A rejected article identifier normally yields `400`; malformed URLs that do
not match a route may yield `404`. Only the API routes below are supported.

## Endpoints

| Use case | Method and path | Request body | Success |
|---|---|---|---|
| List current articles | `GET /api/v1/articles` | None | `200` JSON |
| Read Markdown source | `GET /api/v1/articles/<Category>/<filename>.md` | None | `200` Markdown |
| Create or replace | `PUT /api/v1/articles/<Category>/<filename>.md` | UTF-8 Markdown | `201` new or `200` replaced, JSON |
| Delete | `DELETE /api/v1/articles/<Category>/<filename>.md` | None | `204`, empty body |

### List articles

```http
GET /api/v1/articles HTTP/1.1
Authorization: Bearer <api-token>
```

The response contains a flat `articles` array. Each item has the relative
filesystem `path` and stable path-derived `hash`; no Markdown body, category
object, pagination, or cursor is returned. An empty portal returns
`{"articles":[]}`. The endpoint scans the content directory, so manual file
additions/removals are reflected on the next call.

```http
HTTP/1.1 200 OK
Content-Type: application/json

{"articles":[{"path":"News/example.md","hash":"543cd2358d3db10a0c7d2ac9fc827057"}]}
```

### Read an article

```http
GET /api/v1/articles/News/example.md HTTP/1.1
Authorization: Bearer <api-token>
```

Success is `200` with the raw UTF-8 Markdown source and a `text/markdown`
content type, not rendered HTML or JSON. A missing article returns `404`.

```http
HTTP/1.1 200 OK
Content-Type: text/markdown; charset=utf-8

# Example article
```

### Create or replace an article

```http
PUT /api/v1/articles/News/example.md HTTP/1.1
Authorization: Bearer <api-token>
Content-Type: text/markdown; charset=utf-8

# Example article
```

Send the complete article as the request body. `PUT` creates a missing
category directory, creates the file if absent (`201`), or replaces the entire
file if present (`200`). It is **not** a partial update or append. An empty
UTF-8 body is accepted. The replacement is written to a temporary file in
the same directory and atomically renamed into place. The JSON response is:

```http
HTTP/1.1 201 CREATED
Content-Type: application/json

{"path":"News/example.md","hash":"543cd2358d3db10a0c7d2ac9fc827057"}
```

The `Content-Type` media type must be `text/markdown` (a `charset=utf-8`
parameter is accepted). The body must decode as UTF-8 and is limited to
1,048,576 bytes (1 MiB). Sending a different media type yields `415`; invalid
UTF-8 yields `400`; an oversized request yields `413`. Concurrent writes to
the same path have no version check: the last completed replacement wins.

### Delete an article

```http
DELETE /api/v1/articles/News/example.md HTTP/1.1
Authorization: Bearer <api-token>
```

Success is `204 No Content`, with no response body. A missing file returns
`404`; DELETE is not an idempotent *response* in the sense of returning `204`
again. The empty category directory may remain. The old reader URL stops
working immediately even though its hash-to-path SQLite row can remain.

## Copyable curl examples through a manual tunnel

To keep the real token out of the process command line, put this line in a
private curl config file (mode 0600) on the client machine:

```text
header = "Authorization: Bearer <api-token>"
```

Replace the placeholder inside that file only, and never commit the file.
With the SSH forward above running, set these non-secret shell variables:

```bash
DAA_BASE_URL=http://127.0.0.1:18998
DAA_CURL_CONFIG=/path/to/private-curl.conf
```

Then each use case is a complete HTTP request:

```bash
# List article paths and hashes.
curl --config "$DAA_CURL_CONFIG" --include "$DAA_BASE_URL/api/v1/articles"

# Download raw Markdown; quote URL-encoded paths containing spaces.
curl --config "$DAA_CURL_CONFIG" --fail --output example-copy.md \
  "$DAA_BASE_URL/api/v1/articles/News/example.md"

# Create or completely replace from a local UTF-8 Markdown file.
curl --config "$DAA_CURL_CONFIG" --include --request PUT \
  --header 'Content-Type: text/markdown; charset=utf-8' \
  --data-binary @example.md \
  "$DAA_BASE_URL/api/v1/articles/News/example.md"

# Delete the file; expect 204 and no body.
curl --config "$DAA_CURL_CONFIG" --include --request DELETE \
  "$DAA_BASE_URL/api/v1/articles/News/example.md"
```

The bundled `daa-client` is preferable for automation because it handles the
SSH lifecycle and token loading without a separate curl config. Its `get`
command refuses to overwrite an existing local destination. Its `put` command
reads the source file and prints the JSON result; `list` prints JSON;
`delete` prints `deleted` on success. A failed HTTP request exits nonzero.

## Status codes and operational limits

| Status | Meaning in this API |
|---|---|
| `200` | List, read, or replace succeeded. |
| `201` | New article created. |
| `204` | Article deleted; no response body. |
| `400` | Invalid managed article path or invalid UTF-8 PUT body. |
| `401` | Missing or incorrect bearer token. |
| `404` | Missing article, unmatched path, or unsupported method (intentionally concealed). |
| `413` | Request body exceeds 1 MiB. |
| `415` | PUT body is not `text/markdown`. |
| `429` | Per-client-IP rate limit exceeded. |
| `503` | Portal API token is absent or shorter than 32 characters; no API operation is allowed. |

The in-app rate limits are 240 requests/minute and 8 requests/second per
client IP; a proxy can impose additional limits. Do not assume error bodies
are JSON. Handle status codes first. `GET`/`DELETE` of a missing article
return `404`; no conditional requests, batch operations, search, revisions,
or pagination are available in v1.

There is no rename/move endpoint. To move an article, read its source, `PUT`
the full body to a new path, verify the new item, then `DELETE` the old path.
This changes the reader URL because its 32-character hash is the first 32 hex
characters of SHA-256 of the **relative path** (not the file contents).
Overwriting a file at the same path preserves its reader URL. A URL can be
formed as `https://<reader-host>/<hash>`; the private index URL is separate
and is never returned by this API. Manually adding or removing `.md` files on
the portal host still works; the list/index scan registers new paths, while
missing files disappear from listings and their old reader URLs return `404`.
