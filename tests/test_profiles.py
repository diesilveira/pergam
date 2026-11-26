from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from pergam_profiles import HOSTED_URL, ProfileError, Server, config_path, load, resolve
from support import REPO_ROOT

SCRIPT = REPO_ROOT / "skills" / "post-pergam" / "pergam_profiles.py"


def write_config(folder: Path, data: Any) -> dict[str, str]:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "servers.json"
    path.write_text(json.dumps(data) if not isinstance(data, str) else data)
    return {"PERGAM_CONFIG": str(path)}


@pytest.fixture
def team_ca(tmp_path: Path) -> Path:
    path = tmp_path / "cfg" / "team-ca.pem"
    path.parent.mkdir(parents=True)
    path.write_text("-----BEGIN CERTIFICATE-----\n")
    return path


@pytest.fixture
def env(tmp_path: Path, team_ca: Path) -> dict[str, str]:
    return write_config(
        tmp_path / "cfg",
        {
            "default": "local",
            "servers": {
                "local": {"url": "http://localhost:1111/"},
                "team": {"url": "https://pergam.example.com", "ca": "team-ca.pem"},
            },
        },
    )


def test_config_path_order(tmp_path: Path) -> None:
    assert config_path({"PERGAM_CONFIG": "~/p.json"}) == Path.home() / "p.json"
    assert config_path({"XDG_CONFIG_HOME": str(tmp_path)}) == tmp_path / "pergam" / "servers.json"
    assert config_path({}) == Path.home() / ".config" / "pergam" / "servers.json"


def test_named_server_wins_over_pergam_url(env: dict[str, str], team_ca: Path) -> None:
    server = resolve("team", {**env, "PERGAM_URL": "http://other.example.com"})

    assert server == Server("team", "https://pergam.example.com", str(team_ca))


def test_pergam_url_wins_over_the_default(env: dict[str, str], team_ca: Path) -> None:
    server = resolve(None, {**env, "PERGAM_URL": "http://other.example.com", "PERGAM_CA": str(team_ca)})

    assert server == Server("PERGAM_URL", "http://other.example.com", str(team_ca))


def test_default_then_hosted(env: dict[str, str], tmp_path: Path) -> None:
    assert resolve(None, env) == Server("local", "http://localhost:1111")
    assert resolve(None, write_config(tmp_path / "plain", {"servers": {}})) == Server("hosted", HOSTED_URL)


def test_pergam_url_works_without_a_file(tmp_path: Path) -> None:
    missing = {"PERGAM_CONFIG": str(tmp_path / "none.json")}

    assert resolve(None, {**missing, "PERGAM_URL": "http://localhost:1111"}).url == "http://localhost:1111"
    assert resolve(None, missing) == Server("hosted", HOSTED_URL)
    assert resolve("hosted", missing).url == HOSTED_URL


def test_a_url_is_accepted_as_the_name(tmp_path: Path) -> None:
    assert resolve("https://pergam.example.com/", {}) == Server("https://pergam.example.com/", "https://pergam.example.com")


def test_ca_is_expanded_from_home(tmp_path: Path, team_ca: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(team_ca.parent))
    env = write_config(tmp_path / "other", {"servers": {"team": {"url": "https://pergam.example.com", "ca": "~/team-ca.pem"}}})

    assert resolve("team", env).ca == str(team_ca)


@pytest.mark.parametrize(
    "data",
    [
        "not json",
        [],
        {"servers": []},
        {"servers": {"team": "https://pergam.example.com"}},
        {"servers": {"team": {"url": "pergam.example.com"}}},
        {"default": "nowhere", "servers": {}},
    ],
)
def test_invalid_config_is_reported(tmp_path: Path, data: Any) -> None:
    with pytest.raises(ProfileError):
        load(Path(write_config(tmp_path / "bad", data)["PERGAM_CONFIG"]))


def test_unknown_name_lists_the_known_ones(env: dict[str, str]) -> None:
    with pytest.raises(ProfileError, match="hosted, local, team"):
        resolve("staging", env)


def run(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    clean = {"PATH": "/usr/bin:/bin", "HOME": str(Path.home()), "PYTHONDONTWRITEBYTECODE": "1", **env}
    return subprocess.run([sys.executable, str(SCRIPT), *args], env=clean, capture_output=True, text=True)


def test_env_command_prints_shell_assignments(env: dict[str, str], team_ca: Path) -> None:
    result = run(env, "env", "team")

    assert result.returncode == 0
    assigned = dict(line.split("=", 1) for line in result.stdout.splitlines())
    assert {key: shlex.split(value)[0] if value else "" for key, value in assigned.items()} == {
        "PERGAM_SERVER": "team",
        "PERGAM_URL": "https://pergam.example.com",
        "PERGAM_CA": str(team_ca),
    }


def test_env_command_reports_errors(env: dict[str, str]) -> None:
    result = run(env, "env", "staging")

    assert result.returncode == 2
    assert "unknown server 'staging'" in result.stderr


def test_list_command_marks_the_default(env: dict[str, str]) -> None:
    lines = run(env, "list").stdout.splitlines()

    assert lines[1] == "* local\thttp://localhost:1111"
    assert [line.split("\t")[0].strip("* ") for line in lines] == ["hosted", "local", "team"]


def test_a_missing_ca_only_breaks_the_profile_that_uses_it(tmp_path: Path) -> None:
    env = write_config(
        tmp_path / "partly",
        {
            "default": "local",
            "servers": {
                "local": {"url": "http://localhost:1111"},
                "team": {"url": "https://pergam.example.com", "ca": "missing.pem"},
            },
        },
    )

    assert resolve(None, env) == Server("local", "http://localhost:1111")
    assert resolve("local", env).url == "http://localhost:1111"
    with pytest.raises(ProfileError, match="team: ca file not found"):
        resolve("team", env)
    assert "(missing)" in run(env, "list").stdout
