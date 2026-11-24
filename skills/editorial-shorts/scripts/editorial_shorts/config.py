"""Workspace configuration: user settings live in <workspace>/config.json, never in the skill folder."""
import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[2]
EXAMPLE_CONFIG = SKILL_DIR / "config.example.json"

KNOWN_PLATFORMS = {"youtube", "tiktok", "instagram", "facebook", "mock"}
KNOWN_ADAPTERS = {"mock", "local_export", "buffer"}
SECRET_HINTS = ("secret", "token", "password", "api_key", "apikey", "access_key")


class ConfigError(Exception):
    pass


def deep_merge(base, over):
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def defaults():
    return json.loads(EXAMPLE_CONFIG.read_text(encoding="utf-8"))


def _find_secrets(obj, path=""):
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else k
            if any(h in k.lower() for h in SECRET_HINTS) and isinstance(v, str) and v.strip():
                found.append(p)
            found += _find_secrets(v, p)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            found += _find_secrets(v, f"{path}[{i}]")
    return found


def validate(cfg):
    secrets = _find_secrets(cfg)
    if secrets:
        raise ConfigError("config.json must not hold secrets (use environment variables): " + ", ".join(secrets))
    if cfg["publishing"]["mode"] not in ("simulate", "live"):
        raise ConfigError("publishing.mode must be 'simulate' or 'live'")
    ids = [p.get("id") for p in cfg.get("pages", [])]
    if not ids or any(not i for i in ids) or len(set(ids)) != len(ids):
        raise ConfigError("pages: every page needs a unique, non-empty id")
    for page in cfg["pages"]:
        for d in page.get("destinations", []):
            if d.get("platform") not in KNOWN_PLATFORMS:
                raise ConfigError(f"page {page['id']}: unknown platform {d.get('platform')!r}")
            if d.get("adapter", "mock") not in KNOWN_ADAPTERS:
                raise ConfigError(f"page {page['id']}: unknown adapter {d.get('adapter')!r}")
            if not d.get("account"):
                raise ConfigError(f"page {page['id']}: every destination needs an 'account' label")


def load(workspace):
    ws = Path(workspace).resolve()
    p = ws / "config.json"
    if not p.exists():
        raise ConfigError(f"No config.json in {ws}. Run: es.py init {ws}")
    try:
        user = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ConfigError(f"config.json is not valid JSON: {e}") from e
    cfg = deep_merge(defaults(), user)
    validate(cfg)
    cfg["_workspace"] = str(ws)
    return cfg


def page(cfg, page_id):
    for p in cfg["pages"]:
        if p["id"] == page_id:
            return p
    raise ConfigError(f"page {page_id!r} is not configured")


def tz(cfg):
    off = cfg.get("timezone_offset", "+00:00")
    sign = -1 if off.startswith("-") else 1
    h, m = off.lstrip("+-").split(":")
    return timezone(sign * timedelta(hours=int(h), minutes=int(m)))


def now_iso(cfg=None):
    return datetime.now(tz(cfg) if cfg else timezone.utc).isoformat(timespec="seconds")


def lang_code(cfg_or_lang):
    lang = cfg_or_lang if isinstance(cfg_or_lang, str) else cfg_or_lang.get("language", "en")
    return lang.split("-")[0].lower()
