# Webhook Deploy

Auto-deploy server that listens for GitHub push events, pulls the latest code, and rebuilds Docker containers. Exposed to the internet via Cloudflare Tunnel.

## How it works

1. You push to a configured repo on GitHub
2. GitHub sends a webhook to this server
3. The server verifies the signature, runs `git pull`, then `docker compose up -d --build`

## Setup

### 1. Clone to server

```bash
git clone https://github.com/MurkyPuma/webhook-deploy.git /prod/webhook-deploy
```

### 2. Configure repos

Copy the example config and fill it in (the real `config.json` is gitignored so
your secret never gets committed):

```bash
cp config.example.json config.json
```

```json
{
  "secret": "your-github-webhook-secret",
  "port": 9000,
  "repos": {
    "user/repo-name": {
      "path": "/prod/repo-name",
      "branch": "main"
    }
  }
}
```

Each repo entry also accepts optional `compose_dir` (run compose from a
subdirectory of the repo) and `compose_file` (use a non-default compose file).

### 3. Install the systemd service

```bash
sudo cp /prod/webhook-deploy/webhook.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now webhook
```

### 4. Add Cloudflare Tunnel route

In your tunnel config or dashboard, add:

```
hostname: webhook.yourdomain.com
service: http://127.0.0.1:9000
```

### 5. Add webhook on GitHub

For each repo: Settings > Webhooks > Add webhook

- **Payload URL:** `https://webhook.yourdomain.com`
- **Content type:** `application/json`
- **Secret:** same as `config.json`
- **Events:** Just the push event

## Logs

```bash
sudo journalctl -u webhook -f
```

## Adding a new repo

1. Add an entry to `config.json`
2. Clone the repo on the server
3. Add the webhook on GitHub with the same secret
