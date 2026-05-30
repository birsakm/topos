"""Three-layer config overlay (defaults < user-global < repo-local < env vars).

Public entry points:

    load_effective_config() -> dict
    effective_config_with_sources() -> tuple[dict, dict]   # value tree + parallel source tree
    user_config_path() -> Path
    repo_config_path(start: Path | None = None) -> Path | None
    set_config_value(dotted_key: str, value, *, scope="user") -> Path
    get_config_value(dotted_key: str) -> tuple[Any, str]   # (value, source label)

Env var format: TOPOS__BLENDER__BINARY=/usr/bin/blender
                TOPOS__BACKENDS__CLAUDE__AUTH=api_key
Double-underscore separates nesting levels. Values are parsed as YAML scalars,
so `true`, `1`, `1.5`, JSON-style strings all work.
"""

from __future__ import annotations

import copy
import os
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

_DEFAULTS_RESOURCE = ("topos", "config_defaults.yaml")
_USER_CONFIG_REL = "topos/config.yaml"
_REPO_CONFIG_NAME = "topos.config.yaml"
_ENV_PREFIX = "TOPOS__"
_ENV_SEP = "__"


# ---------- paths ----------

def user_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / _USER_CONFIG_REL


def repo_config_path(start: Path | None = None) -> Path | None:
    """Walk up from `start` (or cwd) looking for ./topos.config.yaml."""
    cur = (start or Path.cwd()).resolve()
    for d in [cur, *cur.parents]:
        candidate = d / _REPO_CONFIG_NAME
        if candidate.is_file():
            return candidate
    return None


# ---------- loaders ----------

def _load_defaults() -> dict:
    pkg, name = _DEFAULTS_RESOURCE
    with resources.files(pkg).joinpath(name).open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _load_yaml_file(path: Path | None) -> dict:
    if path is None or not path.is_file():
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data or {}


def _env_overlay() -> dict:
    """Build a nested dict from TOPOS__A__B__C=value env vars."""
    out: dict[str, Any] = {}
    for key, raw in os.environ.items():
        if not key.startswith(_ENV_PREFIX):
            continue
        path = key[len(_ENV_PREFIX):].split(_ENV_SEP)
        if not path or any(p == "" for p in path):
            continue
        try:
            value = yaml.safe_load(raw)
        except yaml.YAMLError:
            value = raw
        cursor = out
        for piece in path[:-1]:
            piece_l = piece.lower()
            cursor = cursor.setdefault(piece_l, {})
            if not isinstance(cursor, dict):
                # conflict; replace with dict, losing previous scalar
                cursor = {}
        cursor[path[-1].lower()] = value
    return out


# ---------- merging ----------

def _deep_merge(base: dict, overlay: dict) -> dict:
    """Return a new dict; overlay scalars/lists replace, dicts recurse."""
    out = copy.deepcopy(base)
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _deep_merge_with_sources(
    base: dict,
    base_source: dict,
    overlay: dict,
    overlay_label: str,
) -> tuple[dict, dict]:
    """Merge and produce a parallel `source` dict labelling where each leaf came from."""
    out = copy.deepcopy(base)
    src = copy.deepcopy(base_source)
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            merged_v, merged_s = _deep_merge_with_sources(
                out[k], src.get(k, {}), v, overlay_label
            )
            out[k] = merged_v
            src[k] = merged_s
        else:
            out[k] = copy.deepcopy(v)
            src[k] = overlay_label
    return out, src


def _make_source_tree(d: dict, label: str) -> dict:
    out: dict = {}
    for k, v in d.items():
        out[k] = _make_source_tree(v, label) if isinstance(v, dict) else label
    return out


# ---------- public API ----------

def effective_config_with_sources(
    *, start: Path | None = None
) -> tuple[dict, dict]:
    """Return (effective_config, source_tree).

    source_tree is a parallel dict whose leaves are strings naming the layer
    that supplied that value: 'defaults', 'user', 'repo', or 'env'.
    """
    defaults = _load_defaults()
    cfg = copy.deepcopy(defaults)
    sources = _make_source_tree(defaults, "defaults")

    user_path = user_config_path()
    user = _load_yaml_file(user_path)
    if user:
        cfg, sources = _deep_merge_with_sources(cfg, sources, user, "user")

    repo = _load_yaml_file(repo_config_path(start))
    if repo:
        cfg, sources = _deep_merge_with_sources(cfg, sources, repo, "repo")

    env = _env_overlay()
    if env:
        cfg, sources = _deep_merge_with_sources(cfg, sources, env, "env")

    return cfg, sources


def load_effective_config(*, start: Path | None = None) -> dict:
    cfg, _ = effective_config_with_sources(start=start)
    return cfg


def _walk(obj: Any, dotted: str) -> Any:
    cur = obj
    for piece in dotted.split("."):
        if not isinstance(cur, dict) or piece not in cur:
            raise KeyError(dotted)
        cur = cur[piece]
    return cur


def get_config_value(dotted_key: str) -> tuple[Any, str]:
    cfg, src = effective_config_with_sources()
    value = _walk(cfg, dotted_key)
    try:
        source = _walk(src, dotted_key)
    except KeyError:
        source = "defaults"
    if not isinstance(source, str):
        source = "defaults"
    return value, source


def set_config_value(
    dotted_key: str,
    value: Any,
    *,
    scope: str = "user",
) -> Path:
    """Write a value to the user-global or repo-local config file.

    scope: "user" -> ~/.config/topos/config.yaml
           "repo" -> ./topos.config.yaml (creates if missing)
    """
    if scope == "user":
        path = user_config_path()
    elif scope == "repo":
        path = Path.cwd() / _REPO_CONFIG_NAME
    else:
        raise ValueError(f"unknown scope: {scope}")

    data = _load_yaml_file(path) if path.is_file() else {}
    cursor = data
    pieces = dotted_key.split(".")
    for piece in pieces[:-1]:
        nxt = cursor.get(piece)
        if not isinstance(nxt, dict):
            nxt = {}
            cursor[piece] = nxt
        cursor = nxt
    cursor[pieces[-1]] = value

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False)
    return path
