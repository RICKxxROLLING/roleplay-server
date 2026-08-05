# Installing on Unraid

Two supported paths. **Compose is the easier one** — it wires the two containers together for
you. Use the templates if you'd rather stay in Unraid's Docker tab or avoid plugins.

---

## Prerequisites (both paths)

1. **Nvidia Driver plugin** — Apps tab → search "Nvidia Driver" → install → **reboot**.
   Nothing GPU-related works until you've rebooted.
2. Confirm the GPU is visible. Settings → Nvidia Driver should list your card. Note the
   **GPU UUID** if you want to pin one specific card.
3. Roughly **10 GB free** on the share holding your appdata, for the model.
4. **Enough VRAM.** The default model is a 13B needing ~11 GB. On an 8 GB card set
   `RP_MODEL=HammerAI/smart-lemon-cookie` in `.env` before the first Compose Up — otherwise
   Ollama silently splits the model across GPU and CPU and replies crawl. See
   [Choosing a model](../../README.md#choosing-a-model).

Sanity check from the Unraid terminal:

```bash
nvidia-smi
```

If that doesn't print your GPU, stop and fix the plugin first — everything below depends on it.

---

## Path A — Docker Compose Manager (recommended)

1. Apps tab → install **Docker Compose Manager**.
2. Docker tab → **Add New Stack** → name it `roleplay`.
3. Click the stack's gear → **Edit Stack** → **Compose File**, and paste the contents of
   [`docker-compose.yml`](docker-compose.yml).
4. Gear → **Edit Stack** → **Env File**, and paste [`.env.example`](.env.example). Edit:
   - `RP_IMAGE` — your GHCR image (see [Publishing the image](#publishing-the-image))
   - `TZ` — your timezone
   - `NVIDIA_VISIBLE_DEVICES` — leave `all`, or paste a GPU UUID
5. **Compose Up**.

First run downloads the model (several GB). Watch progress:

```bash
docker logs -f roleplay-model-pull
```

When it finishes, open `http://<unraid-ip>:8000`. After this, everything — models included —
is managed from the web UI; you shouldn't need the terminal again.

Because everything is in one compose project, the app reaches Ollama at `http://ollama:11434`
over the project's private network — no IP configuration needed.

---

## Path B — Unraid Docker templates

Templates can't express a multi-container dependency, so you install two containers and point
one at the other by IP.

### 1. Install the templates

Copy both XML files to your Unraid server:

```bash
cp roleplay-ollama.xml roleplay-server.xml /boot/config/plugins/dockerMan/templates-user/
```

They'll then appear under Docker → **Add Container** → *Select a template*.

### 2. Ollama first

Add Container → `roleplay-ollama`. Defaults are fine. Apply and wait for it to start.

You don't need to pull a model here — the app's **Models** panel downloads them with a
progress bar once it's connected. (If you'd rather use the terminal:
`docker exec -it roleplay-ollama ollama pull HammerAI/smart-lemon-cookie`.)

### 3. Then the app

Add Container → `roleplay-server`. Set:

- **Repository** — your GHCR image
- **Ollama URL** — `http://<your-unraid-lan-ip>:11434`

> ### The one thing people get wrong here
>
> Set **Ollama URL** to your server's **LAN IP**, e.g. `http://192.168.1.10:11434`.
>
> - `localhost` resolves to *inside the app container*, not the host — nothing is listening there.
> - `http://roleplay-ollama:11434` **does not work on the default bridge network**. Unraid's
>   default bridge has no DNS between containers; only user-defined custom networks do.
>
> If you'd rather use the container name, put both containers on the same custom network
> (Docker → Networks, or `--network=my-net` in Extra Parameters), then
> `http://roleplay-ollama:11434` resolves correctly.

Apply, then click the container's icon → **WebUI**.

---

## Publishing the image

Unraid's Docker tab needs a pullable image; it can't build from source. The repo ships a
GitHub Actions workflow that publishes to GHCR on every push to `main` or `master`:

1. Push this repo to GitHub.
2. Actions tab → let **Publish container image** run.
3. Packages → `roleplay-server` → set visibility **Public** (or configure Unraid with a
   registry credential for a private package).
4. The image is `ghcr.io/rickxxrolling/roleplay-server:latest` — already filled in throughout
   the templates and `.env.example`.

Note the casing: **GHCR image paths must be lowercase** even though the GitHub username isn't.
The workflow lowercases automatically; the hardcoded references already account for it.

The icon URL in the templates points at the `master` branch. If you rename the default branch
to `main`, update that path — and note `deploy/unraid/icon.png` doesn't exist yet, so Unraid
shows its default icon until you add one.

**Prefer not to use a registry?** Compose can build on the NAS instead — put the full source
on the server, then in `docker-compose.yml` comment out `image:` and uncomment `build:`.
Templates still won't work this way; that's the tradeoff.

---

## Developing against the NAS

Unraid runs production; development stays on your workstation. The trick is to point the dev
server at the NAS for inference, so you get a real GPU and real models without running
anything heavy locally:

```
Workstation (Claude Code)  ──git push──►  Actions  ──►  GHCR
   uvicorn + vite, own DB                                 │
        │                                                 ▼
        └────── LAN: http://<unraid-ip>:11434 ──►  Unraid (prod)
```

The `ollama` service publishes port 11434 for exactly this. On your workstation:

```powershell
$env:RP_LLM_BASE_URL = "http://<unraid-ip>:11434"
$env:RP_DATABASE_URL = "sqlite:///./dev.db"
$env:RP_DATA_DIR = "./devdata"
uvicorn app.main:app --reload --port 8000
```

Two things matter here. **Use a separate database** — `dev.db` above — so test chats never
touch `/mnt/user/appdata`. And remember env vars are first-run defaults only: once a setting
is saved from the UI it lives in that database, so dev and prod drift independently, which is
what you want.

This is also how you calibrate the things that have only ever been reasoned about rather than
observed: prompt format, stop sequences, summariser quality, and `RP_RETRIEVAL_MIN_SCORE`.
**Settings → Inspect built prompt** shows exactly what the model receives, with each recalled
message and its score.

**Promoting a change:** push → Actions builds and pushes to GHCR → on Unraid either
`docker compose pull && docker compose up -d`, or **force update** in the Docker tab. Pin
`RP_IMAGE` to a `sha-` or version tag rather than `latest` if you want rollbacks to be a
one-line edit.

**Security:** Ollama has no authentication. Publishing 11434 is fine on a trusted LAN, but
never port-forward it. Set `OLLAMA_PORT=` (blank) in `.env` to keep it off the host.

---

## Where your data lives

| Path | Contents |
|---|---|
| `/mnt/user/appdata/roleplay-server` | SQLite database, chats, imported character images |
| `/mnt/user/appdata/roleplay-ollama` | Downloaded models (large) |

Both are ordinary bind mounts, so **CA Backup / Restore Appdata** picks them up with no extra
configuration. The app's data is small — the models are what take space, and re-downloading
those is easy, so exclude `roleplay-ollama` from backups if you'd rather save the room.

Files are written as `nobody:users` (99:100) via `PUID`/`PGID`, matching Unraid convention, so
they're readable over SMB and manageable from the Unraid file manager.

---

## Troubleshooting

**Stack fails to start: "failed to get device handle from UUID: Not Found"**
The full error mentions a CDI modifier and names no GPU, so it reads like a driver bug. It
almost always means `NVIDIA_VISIBLE_DEVICES` in `.env` holds a GPU UUID that doesn't match a
live card — stale after a driver update, or mistyped. Get the real ones:

```bash
nvidia-smi -L
```

Paste the exact `GPU-...` string, or just set `NVIDIA_VISIBLE_DEVICES=all`, which is right
unless you have several cards and need to pin one.

**Replies are slow and the GPU looks idle**
The model doesn't fit in VRAM, so Ollama split it across GPU and CPU. Confirm with:

```bash
docker exec roleplay-ollama ollama ps
```

Anything other than `100% GPU` in the PROCESSOR column means it's partly on CPU. Pick a
smaller model — see [Choosing a model](../../README.md#choosing-a-model).

**Sidebar status dot is red / "backend offline"**
The app can't reach Ollama. Check `RP_LLM_BASE_URL`. From the Unraid terminal:

```bash
curl http://<unraid-ip>:11434/api/tags     # should return JSON
docker logs roleplay-server --tail 50
```

**"model not found" when sending a message**
The model isn't downloaded, or the active model doesn't match an installed tag. Open the
**Models** panel in the app — it lists what's installed and lets you download or switch.
The sidebar dot turns amber when the selected model is missing.

**Replies are extremely slow**
It's running on CPU — GPU passthrough isn't working. Verify:

```bash
docker exec -it roleplay-ollama nvidia-smi
```

No GPU listed means the Nvidia Driver plugin isn't active, `--runtime=nvidia` is missing from
Extra Parameters, or you skipped the reboot.

**Permission errors in the app log**
Ownership mismatch on appdata. Fix with:

```bash
chown -R 99:100 /mnt/user/appdata/roleplay-server
```

**Container is unhealthy but the UI loads**
The healthcheck only asks whether the web server responds. If the UI works, the app is fine —
check whether Ollama is reachable separately.

**Port 8000 already taken**
Change `RP_PORT` (compose) or the WebUI Port (template). Only the host side changes; leave the
container side at 8000.
