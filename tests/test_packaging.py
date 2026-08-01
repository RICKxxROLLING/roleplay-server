"""Deployment assets. These can't be built here, so validate them statically.

Worth keeping: a malformed compose file or Unraid template fails at install
time on the NAS, which is a slow and annoying place to discover it.
"""
from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET

import pytest
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_yaml(rel):
    with open(os.path.join(ROOT, rel)) as fh:
        return yaml.safe_load(fh)


def read(rel):
    with open(os.path.join(ROOT, rel)) as fh:
        return fh.read()


# --- compose ----------------------------------------------------------------

@pytest.mark.parametrize("path", ["docker-compose.yml", "deploy/unraid/docker-compose.yml"])
def test_model_pull_cannot_wedge_the_stack(path):
    """A bad model name must not stop the app from starting.

    `app` gates on model-pull *completing successfully*, and the Models panel is
    the only place to fix a bad name -- so a hard failure here locks you out of
    the very UI that repairs it. Observed for real: `ollama pull mythomax` fails
    with "file does not exist" because MythoMax isn't in Ollama's official
    library, and it took the whole stack down with it.
    """
    cmd = load_yaml(path)["services"]["model-pull"]["command"][0]
    assert "exit 0" in cmd, "model-pull must always exit 0"
    assert "||" in cmd, "pull failures must be tolerated, not chained with &&"


@pytest.mark.parametrize(
    "path",
    ["docker-compose.yml", "deploy/unraid/docker-compose.yml", ".env.example",
     "deploy/unraid/.env.example"],
)
def test_default_model_is_namespaced(path):
    """Bare `mythomax` resolves to Ollama's official library, where it does not
    exist. Community models must carry their namespace."""
    assert not re.search(r"(?<![\w/-])mythomax", read(path)), (
        "unqualified 'mythomax' will fail to pull; use a namespaced tag"
    )


@pytest.mark.parametrize("path", ["docker-compose.yml", "deploy/unraid/docker-compose.yml"])
def test_compose_parses_with_expected_services(path):
    d = load_yaml(path)
    assert set(d["services"]) == {"ollama", "model-pull", "app"}
    assert d["services"]["app"]["depends_on"]["model-pull"]["condition"] == (
        "service_completed_successfully"
    )


def test_root_compose_declares_its_named_volumes():
    d = load_yaml("docker-compose.yml")
    declared = set(d.get("volumes") or {})
    for name, svc in d["services"].items():
        for vol in svc.get("volumes", []):
            src = vol.split(":")[0]
            if not src.startswith(("/", ".", "$")):
                assert src in declared, f"{name} uses undeclared volume {src}"


def test_unraid_compose_uses_bind_mounts_and_unraid_conventions():
    d = load_yaml("deploy/unraid/docker-compose.yml")
    assert "volumes" not in d, "Unraid should bind-mount appdata, not use named volumes"

    for svc in d["services"].values():
        for vol in svc.get("volumes", []):
            assert vol.startswith(("$", "/")), f"not a bind mount: {vol}"

    # Unraid's Nvidia plugin exposes GPUs through the nvidia runtime
    assert d["services"]["ollama"]["runtime"] == "nvidia"
    # nobody:users
    assert "PUID" in d["services"]["app"]["environment"]


def test_root_compose_requests_gpu():
    d = load_yaml("docker-compose.yml")
    devices = d["services"]["ollama"]["deploy"]["resources"]["reservations"]["devices"]
    assert devices[0]["driver"] == "nvidia"


# --- Dockerfile / entrypoint ------------------------------------------------

def test_dockerfile_is_multistage_and_drops_privileges():
    df = read("Dockerfile")
    assert df.count("FROM ") == 2, "web build stage must not leak into the runtime image"
    assert "gosu" in df
    assert "ENTRYPOINT" in df and "HEALTHCHECK" in df


def test_entrypoint_has_lf_line_endings():
    """Critical on Windows: CRLF here makes the container fail with
    'bad interpreter'. .gitattributes pins it, this catches a regression."""
    with open(os.path.join(ROOT, "docker-entrypoint.sh"), "rb") as fh:
        assert b"\r\n" not in fh.read()


def test_gitattributes_pins_shell_line_endings():
    assert re.search(r"\*\.sh\s+text\s+eol=lf", read(".gitattributes"))


def test_dockerignore_excludes_build_artifacts():
    ignored = read(".dockerignore")
    for entry in ["web/node_modules", "data/", ".env"]:
        assert entry in ignored


# --- Unraid templates -------------------------------------------------------

@pytest.mark.parametrize(
    "path",
    [
        "deploy/unraid/templates/roleplay-server.xml",
        "deploy/unraid/templates/roleplay-ollama.xml",
    ],
)
def test_unraid_template_matches_schema(path):
    root = ET.parse(os.path.join(ROOT, path)).getroot()
    assert root.tag == "Container"

    for tag in ["Name", "Repository", "Network", "Overview", "Category"]:
        assert root.find(tag) is not None, f"missing <{tag}>"

    for cfg in root.findall("Config"):
        for attr in ["Name", "Target", "Default", "Type", "Display", "Required", "Mask"]:
            assert cfg.get(attr) is not None, f"Config {cfg.get('Name')} missing {attr}"
        assert cfg.get("Type") in {"Port", "Path", "Variable", "Device", "Label"}
        assert (cfg.text or "").strip(), f"Config {cfg.get('Name')} has no value"


def test_ollama_template_requests_nvidia_runtime():
    root = ET.parse(
        os.path.join(ROOT, "deploy/unraid/templates/roleplay-ollama.xml")
    ).getroot()
    assert "--runtime=nvidia" in (root.findtext("ExtraParams") or "")


@pytest.mark.parametrize(
    "path",
    [
        "deploy/unraid/docker-compose.yml",
        "deploy/unraid/.env.example",
        "deploy/unraid/templates/roleplay-server.xml",
        "deploy/unraid/templates/roleplay-ollama.xml",
    ],
)
def test_no_unreplaced_owner_placeholders(path):
    """A stray OWNER means an image path that resolves to nothing on the NAS."""
    assert "OWNER" not in read(path)


@pytest.mark.parametrize(
    "path",
    [
        "deploy/unraid/docker-compose.yml",
        "deploy/unraid/.env.example",
        "deploy/unraid/templates/roleplay-server.xml",
    ],
)
def test_ghcr_paths_are_lowercase(path):
    """GHCR rejects uppercase in image paths, but GitHub usernames may contain
    it -- so the two can't simply share a spelling. Easy to get wrong by hand,
    and the failure is a 404 at pull time on the NAS."""
    # Stop at XML tag / quote boundaries -- a greedy \S+ would swallow the
    # closing </Repository> and fail on its capital R.
    for ref in re.findall(r"ghcr\.io/[^\s<>\"']+", read(path)):
        assert ref == ref.lower(), f"{ref} must be lowercase"


def test_app_template_warns_against_container_name_url():
    """Unraid's default bridge has no inter-container DNS, so the app must be
    pointed at the host LAN IP. This is the #1 install mistake."""
    root = ET.parse(
        os.path.join(ROOT, "deploy/unraid/templates/roleplay-server.xml")
    ).getroot()
    overview = root.findtext("Overview") or ""
    assert "LAN IP" in overview or "lan ip" in overview.lower()

    url_cfg = next(
        c for c in root.findall("Config") if c.get("Target") == "RP_LLM_BASE_URL"
    )
    assert "localhost" not in (url_cfg.text or "")


# --- CI ---------------------------------------------------------------------

def test_publish_workflow_can_push_packages():
    wf = load_yaml(".github/workflows/publish.yml")
    job = wf["jobs"]["build-and-push"]
    assert job["permissions"]["packages"] == "write"


def test_image_build_is_gated_on_checks():
    """Both suites must pass before anything reaches GHCR.

    The frontend half matters as much as pytest: `vite build` does no scope
    analysis, so a component referencing an unbound identifier compiles fine
    and only fails when the page renders. That shipped once -- the Memory panel
    went blank in production with every check green.
    """
    wf = load_yaml(".github/workflows/publish.yml")
    assert wf["jobs"]["build-and-push"].get("needs") == "checks"

    run_steps = " ".join(
        s.get("run", "") for s in wf["jobs"]["checks"]["steps"]
    )
    assert "pytest" in run_steps
    assert "npm run lint" in run_steps
