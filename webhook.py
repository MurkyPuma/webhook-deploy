#!/usr/bin/env python3
"""
GitHub webhook server for auto-deploy.
Listens for push events, pulls the repo, and restarts the associated service.
"""

import hashlib
import hmac
import json
import logging
import subprocess
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "config.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("webhook")


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def verify_signature(payload: bytes, signature: str, secret: str) -> bool:
    if not signature:
        return False
    expected = "sha256=" + hmac.new(
        secret.encode(), payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def run(cmd: list[str], cwd: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout
    )


def deploy(repo_name: str, repo_cfg: dict) -> str:
    path = repo_cfg["path"]
    branch = repo_cfg.get("branch", "main")
    # The compose file may live in a subdirectory of the repo (e.g. "drinks")
    # and may be a non-default file (e.g. "docker-compose.production.yml").
    compose_dir = (
        str(Path(path) / repo_cfg["compose_dir"])
        if repo_cfg.get("compose_dir")
        else path
    )
    compose_file = repo_cfg.get("compose_file")
    results = []

    # git pull (always at the repo root)
    log.info(f"[{repo_name}] pulling {branch} in {path}")
    pull = run(["git", "pull", "origin", branch], cwd=path)
    results.append(f"pull: {pull.stdout.strip() or pull.stderr.strip()}")
    if pull.returncode != 0:
        log.error(f"[{repo_name}] pull failed: {pull.stderr}")
        return " | ".join(results)

    # Rebuild + recreate containers. The image bakes code via `COPY . /code/`,
    # so a code change produces a new image → the container is recreated → its
    # CMD re-runs migrations and starts the new server. `--build` can be slow
    # (pip install), so allow up to 15 minutes.
    compose_cmd = ["docker", "compose"]
    if compose_file:
        compose_cmd += ["-f", compose_file]
    compose_cmd += ["up", "-d", "--build"]
    log.info(f"[{repo_name}] {' '.join(compose_cmd)} (cwd={compose_dir})")
    try:
        rebuild = run(compose_cmd, cwd=compose_dir, timeout=900)
    except subprocess.TimeoutExpired:
        results.append("rebuild: TIMEOUT after 900s")
        log.error(f"[{repo_name}] rebuild timed out")
        return " | ".join(results)

    if rebuild.returncode != 0:
        results.append(f"rebuild failed: {rebuild.stderr.strip()}")
        log.error(f"[{repo_name}] rebuild failed: {rebuild.stderr}")
    else:
        results.append("rebuild: ok")
        log.info(f"[{repo_name}] rebuild ok")

    return " | ".join(results)


class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        config = load_config()
        content_length = int(self.headers.get("Content-Length", 0))
        payload = self.rfile.read(content_length)

        # verify signature
        signature = self.headers.get("X-Hub-Signature-256", "")
        if not verify_signature(payload, signature, config["secret"]):
            log.warning("invalid signature")
            self.send_response(403)
            self.end_headers()
            self.wfile.write(b"invalid signature")
            return

        # parse event
        event = self.headers.get("X-GitHub-Event", "")
        if event == "ping":
            log.info("ping received")
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"pong")
            return

        if event != "push":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ignored")
            return

        data = json.loads(payload)
        repo_name = data.get("repository", {}).get("full_name", "")
        ref = data.get("ref", "")

        repo_cfg = config["repos"].get(repo_name)
        if not repo_cfg:
            log.warning(f"unknown repo: {repo_name}")
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"repo not configured")
            return

        # only deploy on the configured branch
        expected_ref = f"refs/heads/{repo_cfg.get('branch', 'main')}"
        if ref != expected_ref:
            log.info(f"[{repo_name}] ignoring push to {ref} (want {expected_ref})")
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"branch ignored")
            return

        result = deploy(repo_name, repo_cfg)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(result.encode())

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"webhook server running")

    def log_message(self, format, *args):
        pass  # suppress default http logging, we use our own


def main():
    config = load_config()
    port = config.get("port", 9000)
    server = HTTPServer(("127.0.0.1", port), WebhookHandler)
    log.info(f"listening on 127.0.0.1:{port}")
    log.info(f"configured repos: {list(config['repos'].keys())}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("shutting down")
        server.server_close()


if __name__ == "__main__":
    main()
