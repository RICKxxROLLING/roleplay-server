"""Starts ComfyUI on this PC when the roleplay server asks for a picture, and stops it when idle.

Runs on the PC with ComfyUI, not on the server: the server can't open a Windows program, so it
asks this instead. Standard library only, so any Python works, including ComfyUI's own.

  POST /start    start ComfyUI if it isn't answering, and count as activity
  GET  /status   {"comfyui": "running" | "starting" | "stopped", "idle_s": n}

Only the addresses in "allow" may call it (the server, and this PC). It saves nothing: ComfyUI's
console output goes nowhere, and this logs no prompts, since it never sees one. A ComfyUI it
didn't start (the Desktop app, opened by hand) is never stopped.

Settings come from comfy_waker.json next to this file (see comfy_waker.example.json).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = json.load(open(os.path.join(HERE, "comfy_waker.json"), encoding="utf-8"))
PORT = int(CONFIG.get("port", 8190))
ALLOW = set(CONFIG.get("allow", ["10.2.1.75"])) | {"127.0.0.1", "::1"}
COMFY = CONFIG.get("comfyui_url", "http://127.0.0.1:8188").rstrip("/")
IDLE_S = int(CONFIG.get("idle_minutes", 15)) * 60

state = {"proc": None, "last": time.time(), "starting": False}
lock = threading.Lock()


def answering() -> bool:
    try:
        with urllib.request.urlopen(f"{COMFY}/queue", timeout=3) as r:
            q = json.load(r)
        # A job running or waiting is activity, wherever it came from.
        if q.get("queue_running") or q.get("queue_pending"):
            state["last"] = time.time()
        return True
    except Exception:
        return False


def start() -> str:
    with lock:
        state["last"] = time.time()
        if answering():
            return "running"
        proc = state["proc"]
        if proc is not None and proc.poll() is None:
            return "starting"
        flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
        state["proc"] = subprocess.Popen(
            CONFIG["command"], cwd=CONFIG.get("cwd") or None, env={**os.environ, **CONFIG.get("env", {})},
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
        return "starting"


def stop() -> None:
    proc = state["proc"]
    if proc is None or proc.poll() is not None:
        return
    if os.name == "nt":  # The whole tree: ComfyUI's Python can start children of its own.
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    else:
        proc.terminate()
    state["proc"] = None


def watch() -> None:
    while True:
        time.sleep(30)
        with lock:
            proc = state["proc"]
            if proc is None or proc.poll() is not None:
                continue
            answering()  # refreshes "last" while a job is in the queue
            if time.time() - state["last"] > IDLE_S:
                stop()


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _allowed(self) -> bool:
        if self.client_address[0] in ALLOW:
            return True
        self._send(403, {"error": "not allowed"})
        return False

    def do_GET(self):
        if not self._allowed():
            return
        if self.path != "/status":
            return self._send(404, {})
        proc = state["proc"]
        now = "running" if answering() else "starting" if proc is not None and proc.poll() is None else "stopped"
        self._send(200, {"comfyui": now, "idle_s": int(time.time() - state["last"]), "stops_after_s": IDLE_S})

    def do_POST(self):
        if not self._allowed():
            return
        if self.path != "/start":
            return self._send(404, {})
        self._send(200, {"comfyui": start()})

    def log_message(self, *args):  # Nothing written anywhere.
        pass


if __name__ == "__main__":
    threading.Thread(target=watch, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
