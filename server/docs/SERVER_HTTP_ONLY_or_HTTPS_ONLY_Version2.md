# AISignX Server: HTTP-only or HTTPS-only

Pick **one deploy mode** in `config.py` (`AISIGNX_DEPLOY_MODE = 'http'` or `'https'`), or run:

```bash
cd server
python generate_config.py --interactive
python generate_config.py --show
```

Presets are defined in `deploy_modes.py`. You no longer need to hand-edit `TRUST_PROXY` and cookie flags unless you have an unusual proxy chain.

---

Pick ONE mode at a time.

---

## Mode A: HTTP-only (no TLS, no reverse proxy)

Run Flask/Waitress directly and expose HTTP.

1) Start your app
- Flask (dev):
  ```python
  # app.py
  app.run(host="0.0.0.0", port=5000)
  ```
- Waitress (recommended for prod-like HTTP):
  ```bash
  waitress-serve --host=0.0.0.0 --port=5000 app:app
  ```

2) Optional but safe in both modes: enable ProxyFix
- Harmless when not behind a proxy and useful if you later switch to HTTPS behind nginx.
  ```python
  from werkzeug.middleware.proxy_fix import ProxyFix
  app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)
  ```

3) Generate URLs
- Best: return relative paths in API responses (e.g., `/uploads/...`) so clients prepend their configured base URL.
- If you need absolute links, use:
  ```python
  from flask import url_for
  url_for("your_endpoint", ..., _external=True)  # emits http:// when running direct
  ```

4) Make sure nginx is not binding ports (if installed)
- Stop/disable nginx so it doesn’t conflict:
  ```bash
  sudo systemctl stop nginx
  sudo systemctl disable nginx
  ```

5) Client config for HTTP-only
- On Raspberry Pi (`~/signage-client/config.ini`):
  ```ini
  [server]
  url = http://YOUR_HOSTNAME_OR_IP:5000
  api_key = YOUR_API_KEY
  ```
- No TLS settings are needed (remove any `tls_verify` / `ca_bundle` lines).

---

## Mode B: HTTPS-only (behind nginx reverse proxy)

Terminate TLS at nginx on 443. Do not accept HTTP on 80 if you truly want HTTPS-only.

1) Backend stays on localhost HTTP
- Run Flask/Waitress on 127.0.0.1:5000 (no TLS):
  ```bash
  waitress-serve --host=127.0.0.1 --port=5000 app:app
  # or app.run(host="127.0.0.1", port=5000) for dev
  ```

2) Enable ProxyFix in your app (required for correct https links)
```python
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)
```

3) nginx config (443 only)
- Do NOT define a port 80 server block if you want strict HTTPS-only.
- Example `/etc/nginx/sites-available/aisignx`:
  ```
  server {
      listen 443 ssl;
      server_name YOUR_HOSTNAME;  # e.g., dt-nmtva-s04979.spd.mli.corp

      ssl_certificate      /etc/nginx/ssl/cert.pem;       # or fullchain.pem
      ssl_certificate_key  /etc/nginx/ssl/key.pem;

      ssl_protocols TLSv1.2 TLSv1.3;
      ssl_ciphers HIGH:!aNULL:!MD5;

      location / {
          proxy_pass http://127.0.0.1:5000;
          proxy_set_header Host $host;
          proxy_set_header X-Real-IP $remote_addr;
          proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
          proxy_set_header X-Forwarded-Proto $scheme;  # critical
      }
  }
  ```
- Enable and reload:
  ```bash
  sudo ln -sf /etc/nginx/sites-available/aisignx /etc/nginx/sites-enabled/aisignx
  sudo nginx -t && sudo systemctl reload nginx
  ```

4) Certificates and trust
- Use a CA-signed cert or a self-signed cert.
- Only distribute the public cert (PEM) or CA root to clients (never the private key).

5) Client config for HTTPS-only
- On Raspberry Pi (`~/signage-client/config.ini`):
  ```ini
  [server]
  url = https://YOUR_HOSTNAME:443
  api_key = YOUR_API_KEY

  # If cert is self-signed or internal CA:
  tls_verify = true
  ca_bundle = /home/signage/signage-ca.pem   ; your CA root or server public cert (PEM)
  # (Temporary fallback, not recommended: tls_verify = false)
  ```

6) Hostname match
- The certificate SAN must include `YOUR_HOSTNAME`. Use that same hostname in the client URL.

---

## Notes (applies to both modes)

- Prefer returning relative paths (e.g., `/uploads/...`) in API responses. It keeps clients scheme-agnostic.
- If you return absolute URLs, using `url_for(..., _external=True)` ensures:
  - HTTP when running direct.
  - HTTPS when behind nginx + ProxyFix.
- If you switch modes, bump your playlist version or clear Pi cache so it refreshes links:
  ```bash
  rm -f ~/signage-client/cache/playlist.json
  rm -rf ~/signage-client/cache/playlist_*
  sudo systemctl restart signage-client
  ```