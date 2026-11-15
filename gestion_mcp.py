# -*- coding: utf-8 -*-
"""
Gestionnaire MCP simplifié — un agent de codage = un fichier = un format.

Chaque agent (Claude Code, Claude Desktop, OpenCode, Codex, Gemini CLI, VS Code,
Cursor, Windsurf, Cline…) est décrit par une fiche : emplacement du fichier,
clé qui contient les serveurs, format d'un serveur, façon de le désactiver.
L'application lit/écrit uniquement cette clé : le reste du fichier est conservé.
Les copies d'un agent à l'autre sont converties automatiquement.
"""
from __future__ import annotations

import copy
import datetime as _dt
import glob
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog

try:
    import tomllib
except ImportError:  # Python < 3.11
    tomllib = None

APP_NAME = "Gestionnaire MCP"
APP_VERSION = "1.0.0"
APP_TITLE = "Gestionnaire MCP v1.0 - Serveur MCP de vos agents de codage"
IS_WIN = sys.platform.startswith("win")
NO_WINDOW = 0x08000000 if IS_WIN else 0

HOME = Path.home()
APPDATA = Path(os.environ.get("APPDATA") or HOME)
LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA") or HOME)
DATA_DIR = APPDATA / "GestionMCP"
SETTINGS_FILE = DATA_DIR / "reglages.json"
BACKUP_DIR = DATA_DIR / "sauvegardes"
MAX_BACKUPS = 30
DESKTOP_CFG = "claude_desktop_config.json"


# =========================================================================== #
#  Lecture / écriture de fichiers
# =========================================================================== #
def _skip_string(t: str, i: int) -> int:
    """i pointe sur un guillemet ouvrant ; retourne l'index après le guillemet fermant."""
    j, n = i + 1, len(t)
    while j < n and t[j] != '"':
        j += 2 if t[j] == "\\" else 1
    return j + 1


def strip_jsonc(t: str) -> str:
    """JSONC -> JSON : retire commentaires // et /* */ et virgules finales."""
    out, i, n = [], 0, len(t)
    while i < n:
        c = t[i]
        if c == '"':
            j = _skip_string(t, i)
            out.append(t[i:j])
            i = j
        elif t.startswith("//", i):
            j = t.find("\n", i)
            i = n if j < 0 else j
        elif t.startswith("/*", i):
            j = t.find("*/", i + 2)
            i = n if j < 0 else j + 2
        else:
            out.append(c)
            i += 1
    s, res, i, n = "".join(out), [], 0, 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == '"':
            j = _skip_string(s, i)
            res.append(s[i:j])
            i = j
            continue
        if c == ",":
            j = i + 1
            while j < n and s[j] in " \t\r\n":
                j += 1
            if j < n and s[j] in "}]":
                i += 1
                continue
        res.append(c)
        i += 1
    return "".join(res)


def parse_json_text(text: str):
    """Retourne (objet, avec_commentaires). Lève ValueError avec la ligne en cas d'erreur."""
    try:
        return json.loads(text), False
    except json.JSONDecodeError as e:
        try:
            return json.loads(strip_jsonc(text)), True
        except json.JSONDecodeError:
            raise ValueError(f"JSON invalide (ligne {e.lineno}, colonne {e.colno}) : {e.msg}") from None


def detect_indent(text: str):
    m = re.search(r"^([ \t]+)\S", text, re.M)
    if not m:
        return 2
    s = m.group(1)
    return "\t" if "\t" in s else max(1, min(len(s), 8))


def write_text_atomic(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp_gestionmcp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def now_stamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]


def norm(p) -> str:
    return os.path.normcase(os.path.normpath(str(p)))


def open_path(p: Path):
    if IS_WIN:
        os.startfile(str(p))
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(p)])
    else:
        subprocess.Popen(["xdg-open", str(p)])


RESTART_TARGETS = {
    "claude_code": ("Claude Desktop", ("Claude.exe",), ("Claude",),
                    r"\\Claude-Data\\[^\\]+\\claude-code\\"),
    "claude_desktop": ("Claude Desktop", ("Claude.exe",), ("Claude",),
                       r"\\Claude-Data\\[^\\]+\\claude-code\\"),
    "vscode": ("Visual Studio Code", ("Code.exe",), ("Visual Studio Code",), ""),
    "cline": ("Visual Studio Code", ("Code.exe",), ("Visual Studio Code",), ""),
    "roo": ("Visual Studio Code", ("Code.exe",), ("Visual Studio Code",), ""),
    "kilo": ("Visual Studio Code", ("Code.exe",), ("Visual Studio Code",), ""),
    "cursor": ("Cursor", ("Cursor.exe",), ("Cursor",), ""),
    "windsurf": ("Windsurf", ("Windsurf.exe",), ("Windsurf",), ""),
    "kiro": ("Kiro", ("Kiro.exe",), ("Kiro",), ""),
    "lmstudio": ("LM Studio", ("LM Studio.exe", "LMStudio.exe"), ("LM Studio",), ""),
}


def windows_app_state(target: tuple[str, tuple[str, ...], tuple[str, ...], str]) -> dict:
    """Retourne les processus et les raccourcis Windows connus pour une application."""
    _, process_names, app_names, exclude_path = target
    names_json = json.dumps(process_names)
    apps_json = json.dumps(app_names)
    exclude_json = json.dumps(exclude_path)
    script = (
        f"$names = ConvertFrom-Json -InputObject '{names_json}'; "
        f"$appNames = ConvertFrom-Json -InputObject '{apps_json}'; "
        f"$exclude = ConvertFrom-Json -InputObject '{exclude_json}'; "
        "$processes = @(Get-CimInstance Win32_Process | Where-Object { "
        "$names -contains $_.Name -and "
        "(-not $exclude -or ($_.ExecutablePath -and $_.ExecutablePath -notmatch $exclude)) "
        "} | Select-Object ProcessId, ParentProcessId, Name, ExecutablePath); "
        "$apps = @(Get-StartApps | Where-Object { $appNames -contains $_.Name } "
        "| Select-Object Name, AppID); "
        "[pscustomobject]@{ processes = $processes; apps = $apps } "
        "| ConvertTo-Json -Compress -Depth 3"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=20, creationflags=NO_WINDOW,
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"Impossible de détecter l'application : {detail}")
    try:
        state = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Réponse PowerShell invalide : {e}") from e
    if not isinstance(state, dict):
        raise RuntimeError("Les informations de l'application sont illisibles.")
    for key in ("processes", "apps"):
        value = state.get(key)
        state[key] = value if isinstance(value, list) else ([value] if value else [])
    return state


def restart_windows_app(target: tuple[str, tuple[str, ...], tuple[str, ...], str]):
    """Ferme proprement l'application cible puis la relance si Windows la connaît."""
    name = target[0]
    state = windows_app_state(target)
    processes = state["processes"]
    app_id = next((app.get("AppID") for app in state["apps"] if app.get("AppID")), None)
    executable = next((proc.get("ExecutablePath") for proc in processes
                       if proc.get("ExecutablePath")), None)
    if not app_id and not executable:
        raise RuntimeError(f"{name} n'est pas détecté ou ne possède pas de lanceur connu.")

    pids = {int(proc["ProcessId"]) for proc in processes if proc.get("ProcessId")}
    roots = [int(proc["ProcessId"]) for proc in processes
             if proc.get("ProcessId") and int(proc.get("ParentProcessId") or 0) not in pids]
    if processes and not roots:
        roots = list(pids)
    for pid in roots:
        result = subprocess.run(
            ["taskkill.exe", "/PID", str(pid), "/T"],
            capture_output=True, text=True, timeout=15, creationflags=NO_WINDOW,
        )
        if result.returncode:
            state = windows_app_state(target)
            still_running = any(int(proc.get("ProcessId") or 0) in pids for proc in state["processes"])
            if still_running:
                detail = result.stderr.strip() or result.stdout.strip()
                raise RuntimeError(f"{name} n'a pas pu être fermé proprement : {detail}")

    deadline = time.monotonic() + 12
    while processes and time.monotonic() < deadline:
        time.sleep(0.4)
        processes = windows_app_state(target)["processes"]
    if processes:
        raise RuntimeError(f"{name} est encore ouvert. Fermez ses fenêtres avant de le relancer.")

    if app_id:
        subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app_id}"],
                         creationflags=NO_WINDOW)
    elif executable:
        subprocess.Popen([executable], creationflags=NO_WINDOW)
    else:
        raise RuntimeError(f"Aucun lanceur n'a été trouvé pour {name}.")

    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        if windows_app_state(target)["processes"]:
            return
        time.sleep(0.4)
    raise RuntimeError(f"{name} n'a pas démarré après la demande de lancement.")


# ---------------------------------------------------------------- TOML (Codex)
_BARE = re.compile(r"^[A-Za-z0-9_-]+$")
_TOML_HDR = re.compile(r"^\s*\[\[?\s*([^\]]+?)\s*\]\]?\s*(#.*)?$")


def toml_key(k: str) -> str:
    return k if _BARE.match(k) else json.dumps(k, ensure_ascii=False)


def toml_val(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, list):
        return "[" + ", ".join(toml_val(x) for x in v) + "]"
    if isinstance(v, dict):
        if not v:
            return "{}"
        return "{ " + ", ".join(f"{toml_key(k)} = {toml_val(x)}" for k, x in v.items()) + " }"
    return json.dumps(str(v), ensure_ascii=False)


def toml_replace_mcp(text: str, servers: dict) -> str:
    """Remplace toutes les sections [mcp_servers.*] du texte TOML ; le reste est conservé tel quel."""
    out, skip = [], False
    for ln in text.splitlines():
        m = _TOML_HDR.match(ln)
        if m:
            name = m.group(1).replace('"', "").strip()
            skip = name == "mcp_servers" or name.startswith("mcp_servers.")
        if not skip:
            out.append(ln)
    body = "\n".join(out).rstrip()
    blocks = []
    for n, cfg in servers.items():
        lines = [f"[mcp_servers.{toml_key(n)}]"]
        lines += [f"{toml_key(k)} = {toml_val(v)}" for k, v in cfg.items() if v is not None]
        blocks.append("\n".join(lines))
    parts = ([body] if body else []) + blocks
    return "\n\n".join(parts) + "\n"


def read_root(path: Path, root: tuple, fmt: str = "json") -> tuple[dict, bool]:
    """Lit le dictionnaire de serveurs à l'emplacement `root` du fichier. Retourne (serveurs, avec_commentaires)."""
    if not path.exists():
        return {}, False
    text = path.read_text(encoding="utf-8-sig")
    if not text.strip():
        return {}, False
    if fmt == "toml":
        if tomllib is None:
            raise RuntimeError("Python 3.11 ou plus récent est nécessaire pour lire le TOML.")
        data, lossy = tomllib.loads(text), False
    else:
        data, lossy = parse_json_text(text)
    node = data
    for k in root:
        node = node.get(k) if isinstance(node, dict) else None
    return (node if isinstance(node, dict) else {}), lossy


# =========================================================================== #
#  Format interne commun (« canon »)
# =========================================================================== #
def empty_canon() -> dict:
    return {"type": "stdio", "command": "", "args": [], "env": {}, "cwd": "",
            "url": "", "headers": {}, "enabled": True, "extra": {}}


def canon_summary(c: dict) -> str:
    if c["type"] == "stdio":
        return " ".join([c["command"]] + c["args"])
    return c["url"]


def canon_invalid(c: dict) -> bool:
    return not (c["command"] if c["type"] == "stdio" else c["url"])


KNOWN_KEYS = {"type", "command", "args", "env", "environment", "cwd", "url", "httpUrl", "serverUrl",
              "headers", "http_headers", "enabled", "disabled"}


# =========================================================================== #
#  Fiches des agents
# =========================================================================== #
class Agent:
    def __init__(self, id, name, short, path_fn, *, root=("mcpServers",), fmt="json",
                 stdio_type=None, http_type=None, sse_type=None, url_keys=None, remote=True,
                 disable="park", command_array=False, env_key="env", headers_key="headers",
                 defaults=None, cwd_ok=False, strict=False, clis=(), dirs=None, warnings=None,
                 note="", reload=None):
        self.id, self.name, self.short = id, name, short
        self.default_path = path_fn
        self.root, self.fmt = tuple(root), fmt
        self.stdio_type, self.http_type, self.sse_type = stdio_type, http_type, sse_type
        self.url_keys = url_keys or {"http": "url", "sse": "url"}
        self.remote = remote                # False : l'agent ne lit que des commandes (Claude Desktop)
        self.disable = disable              # "disabled" | "enabled" | "park"
        self.command_array = command_array  # OpenCode : "command": [exe, arg1, …]
        self.env_key, self.headers_key = env_key, headers_key
        self.defaults = defaults or {}
        self.cwd_ok, self.strict = cwd_ok, strict
        self.clis = clis
        self.dirs = dirs or (lambda: [])
        self.warnings = warnings
        self.note = note
        self.reload = reload or f"Relancez {short} pour appliquer."
        self.override: str | None = None

    def path(self) -> Path:
        return Path(self.override) if self.override else Path(self.default_path())

    # ------------------------------------------------------ natif -> canon
    def to_canon(self, native) -> dict:
        n = native if isinstance(native, dict) else {}
        c = empty_canon()
        t = str(n.get("type") or "").lower().replace("-", "").replace("_", "")
        ukey = next((k for k in ("url", "httpUrl", "serverUrl") if isinstance(n.get(k), str) and n.get(k)), None)
        if ukey and not n.get("command"):
            c["url"] = n[ukey]
            if t == "sse":
                c["type"] = "sse"
            elif t in ("http", "streamablehttp", "remote"):
                c["type"] = "http"
            elif ukey == self.url_keys["sse"] and self.url_keys["sse"] != self.url_keys["http"]:
                c["type"] = "sse"
            elif ukey == "url" and not t and c["url"].rstrip("/").endswith("/sse"):
                c["type"] = "sse"
            else:
                c["type"] = "http"
            h = n.get(self.headers_key) or n.get("headers") or n.get("http_headers") or {}
            c["headers"] = {str(k): str(v) for k, v in h.items()} if isinstance(h, dict) else {}
        else:
            cmd = n.get("command", "")
            args = n.get("args") or []
            if isinstance(cmd, list):
                c["command"] = str(cmd[0]) if cmd else ""
                c["args"] = [str(a) for a in cmd[1:]] + [str(a) for a in args]
            else:
                c["command"] = str(cmd or "")
                c["args"] = [str(a) for a in args] if isinstance(args, list) else []
            env = n.get(self.env_key)
            if env is None:
                env = n.get("env") or n.get("environment")
            c["env"] = {str(k): str(v) for k, v in env.items()} if isinstance(env, dict) else {}
            c["cwd"] = str(n.get("cwd") or "")
        if "disabled" in n:
            c["enabled"] = not bool(n["disabled"])
        if "enabled" in n:
            c["enabled"] = bool(n["enabled"])
        c["extra"] = {k: copy.deepcopy(v) for k, v in n.items()
                      if k not in KNOWN_KEYS and k not in (self.env_key, self.headers_key)}
        return c

    # ------------------------------------------------------ canon -> natif
    def from_canon(self, c: dict, keep_extra: bool) -> dict:
        remote = c["type"] in ("http", "sse")
        o: dict = {}
        if self.command_array:  # OpenCode
            if remote:
                o = {"type": "remote", "url": c["url"]}
                if c["headers"]:
                    o["headers"] = dict(c["headers"])
            else:
                o = {"type": "local", "command": [c["command"]] + list(c["args"])}
                if c["env"]:
                    o[self.env_key] = dict(c["env"])
        elif remote and not self.remote:  # Claude Desktop : passerelle mcp-remote
            args = ["-y", "mcp-remote", c["url"]]
            for k, v in c["headers"].items():
                args += ["--header", f"{k}:{v}"]
            o = {"command": "npx", "args": args}
        elif remote:
            t = self.http_type if c["type"] == "http" else self.sse_type
            if t:
                o["type"] = t
            o[self.url_keys[c["type"]]] = c["url"]
            if c["headers"]:
                o[self.headers_key] = dict(c["headers"])
        else:
            if self.stdio_type:
                o["type"] = self.stdio_type
            o["command"] = c["command"]
            o["args"] = list(c["args"])
            if c["env"]:
                o[self.env_key] = dict(c["env"])
            if c["cwd"] and self.cwd_ok:
                o["cwd"] = c["cwd"]
        for k, v in self.defaults.items():
            o.setdefault(k, copy.deepcopy(v))
        if keep_extra:
            for k, v in c["extra"].items():
                o.setdefault(k, copy.deepcopy(v))
        if self.disable == "disabled":
            o["disabled"] = not c["enabled"]
        elif self.disable == "enabled" and (self.command_array or not c["enabled"]):
            o["enabled"] = bool(c["enabled"])
        return o

    def set_enabled(self, native: dict, en: bool) -> dict:
        n = copy.deepcopy(native)
        if self.disable == "disabled":
            n.pop("enabled", None)
            n["disabled"] = not en
        elif self.disable == "enabled":
            n.pop("disabled", None)
            n["enabled"] = en
        return n

    def conversion_notes(self, name: str, c: dict) -> list[str]:
        notes = []
        if c["type"] != "stdio" and not self.remote:
            notes.append(f"{name} → {self.short} : URL convertie en « npx -y mcp-remote … » (Node.js requis).")
        if c["type"] == "stdio" and c["cwd"] and not self.cwd_ok:
            notes.append(f"{name} → {self.short} : dossier de travail (cwd) non pris en charge, ignoré.")
        return notes

    @property
    def disable_note(self) -> str:
        if self.disable == "park":
            return ("Cet agent n'a pas d'option « désactivé » : désactiver retire le serveur du fichier "
                    "et le garde de côté dans le gestionnaire (réactiver le remet).")
        key = "disabled: true" if self.disable == "disabled" else "enabled: false"
        return f"Désactiver utilise l'option native de l'agent ({key})."


# ----------------------------------------------------------- emplacements
def xdg_config() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config")


def vscode_user() -> Path:
    if IS_WIN:
        return APPDATA / "Code" / "User"
    if sys.platform == "darwin":
        return HOME / "Library" / "Application Support" / "Code" / "User"
    return xdg_config() / "Code" / "User"


def desktop_candidates() -> list[Path]:
    if IS_WIN:
        c = [Path(p) for p in sorted(glob.glob(str(LOCALAPPDATA / "Claude-Data" / "claude_*")))]
        c += [Path(p) / "LocalCache" / "Roaming" / "Claude"
              for p in sorted(glob.glob(str(LOCALAPPDATA / "Packages" / "Claude_*")))]
        c.append(APPDATA / "Claude")
        return c
    if sys.platform == "darwin":
        return [HOME / "Library" / "Application Support" / "Claude"]
    return [xdg_config() / "Claude"]


def claude_desktop_file() -> Path:
    """Fichier réellement lu par Claude Desktop (les dossiers abandonnés portent un marqueur .user-data-left*)."""
    cands = desktop_candidates()
    live = [d for d in cands if d.is_dir() and not any(d.glob(".user-data-left*"))]
    for d in live:
        if (d / DESKTOP_CFG).exists():
            return d / DESKTOP_CFG
    return (live[0] if live else cands[0]) / DESKTOP_CFG


def desktop_warnings(a: Agent) -> list[str]:
    out = []
    for d in desktop_candidates():
        f = d / DESKTOP_CFG
        if f.exists() and norm(f) != norm(a.path()):
            try:
                n = len(read_root(f, ("mcpServers",))[0])
            except Exception:
                n = 0
            if n:
                out.append(f"Ancien fichier ignoré par Claude Desktop, contient {n} serveur(s) : {f}")
    return out


def opencode_warnings(a: Agent) -> list[str]:
    out = []
    p = a.path()
    for name in ("config.json", "opencode.json", "opencode.jsonc"):
        f = p.parent / name
        if f.exists() and norm(f) != norm(p):
            try:
                s = read_root(f, ("mcp",))[0]
            except Exception as e:
                out.append(f"{f.name} est aussi lu par OpenCode mais il est illisible : {e}")
                continue
            if s:
                out.append(f"OpenCode lit aussi {f} et le fusionne avec ce fichier : il contient "
                           f"{', '.join(s)}. Évitez de déclarer les serveurs à deux endroits.")
    return out


def _vsix(ext_id: str, filename: str) -> Path:
    return vscode_user() / "globalStorage" / ext_id / "settings" / filename


AGENTS: list[Agent] = [
    Agent("claude_code", "Claude Code", "Claude Code", lambda: HOME / ".claude.json",
          stdio_type="stdio", http_type="http", sse_type="sse",
          clis=("claude",), dirs=lambda: [HOME / ".claude"],
          note="Fichier commun à la CLI, à l'extension VS Code et à l'onglet « Code » de Claude Desktop. "
               "Serveurs de portée utilisateur : disponibles dans tous les projets.",
          reload="Relancez la session Claude Code (ou tapez /mcp)."),
    Agent("claude_desktop", "Claude Desktop (chat)", "Desktop", claude_desktop_file, remote=False,
          dirs=lambda: desktop_candidates() + [LOCALAPPDATA / "AnthropicClaude"],
          warnings=desktop_warnings,
          note="Le chat de Claude Desktop. Il ne lance que des commandes : un serveur distant (URL) "
               "y passe par « npx -y mcp-remote ».",
          reload="Redémarrez complètement Claude Desktop (icône de la barre des tâches › Quitter)."),
    Agent("opencode", "OpenCode", "OpenCode", lambda: xdg_config() / "opencode" / "opencode.json",
          root=("mcp",), command_array=True, env_key="environment", disable="enabled", strict=True,
          clis=("opencode",), dirs=lambda: [xdg_config() / "opencode"], warnings=opencode_warnings,
          note="Format OpenCode : \"type\": \"local\" + \"command\": [exe, arguments…], ou \"type\": \"remote\" + \"url\"."),
    Agent("codex", "Codex CLI (OpenAI)", "Codex", lambda: HOME / ".codex" / "config.toml",
          root=("mcp_servers",), fmt="toml", disable="enabled", headers_key="http_headers", cwd_ok=True,
          clis=("codex",), dirs=lambda: [HOME / ".codex"],
          note="Fichier TOML : sections [mcp_servers.<nom>]. Le reste du fichier est conservé."),
    Agent("gemini", "Gemini CLI", "Gemini", lambda: HOME / ".gemini" / "settings.json",
          url_keys={"http": "httpUrl", "sse": "url"}, cwd_ok=True,
          clis=("gemini",), dirs=lambda: [HOME / ".gemini"]),
    Agent("qwen", "Qwen Code", "Qwen", lambda: HOME / ".qwen" / "settings.json",
          url_keys={"http": "httpUrl", "sse": "url"}, cwd_ok=True,
          clis=("qwen",), dirs=lambda: [HOME / ".qwen"]),
    Agent("vscode", "VS Code — GitHub Copilot", "VS Code", lambda: vscode_user() / "mcp.json",
          root=("servers",), stdio_type="stdio", http_type="http", sse_type="sse",
          clis=("code",), dirs=lambda: [vscode_user()],
          note="Serveurs MCP du chat Copilot de VS Code (clé « servers »).",
          reload="Dans VS Code : « MCP: List Servers » › Redémarrer, ou rechargez la fenêtre."),
    Agent("copilot_cli", "GitHub Copilot CLI", "Copilot CLI", lambda: HOME / ".copilot" / "mcp-config.json",
          stdio_type="local", http_type="http", sse_type="sse", defaults={"tools": ["*"]},
          dirs=lambda: [HOME / ".copilot"]),
    Agent("cursor", "Cursor", "Cursor", lambda: HOME / ".cursor" / "mcp.json",
          clis=("cursor",), dirs=lambda: [HOME / ".cursor"]),
    Agent("windsurf", "Windsurf", "Windsurf", lambda: HOME / ".codeium" / "windsurf" / "mcp_config.json",
          url_keys={"http": "serverUrl", "sse": "serverUrl"}, disable="disabled",
          clis=("windsurf",), dirs=lambda: [HOME / ".codeium" / "windsurf"]),
    Agent("cline", "Cline (extension VS Code)", "Cline",
          lambda: _vsix("saoudrizwan.claude-dev", "cline_mcp_settings.json"),
          stdio_type="stdio", http_type="streamableHttp", sse_type="sse", disable="disabled",
          dirs=lambda: [vscode_user() / "globalStorage" / "saoudrizwan.claude-dev"]),
    Agent("roo", "Roo Code (extension VS Code)", "Roo Code",
          lambda: _vsix("rooveterinaryinc.roo-cline", "mcp_settings.json"),
          stdio_type="stdio", http_type="streamable-http", sse_type="sse", disable="disabled",
          dirs=lambda: [vscode_user() / "globalStorage" / "rooveterinaryinc.roo-cline"]),
    Agent("kilo", "Kilo Code (extension VS Code)", "Kilo Code",
          lambda: _vsix("kilocode.kilo-code", "mcp_settings.json"),
          stdio_type="stdio", http_type="streamable-http", sse_type="sse", disable="disabled",
          dirs=lambda: [vscode_user() / "globalStorage" / "kilocode.kilo-code"]),
    Agent("kiro", "Kiro", "Kiro", lambda: HOME / ".kiro" / "settings" / "mcp.json", disable="disabled",
          clis=("kiro",), dirs=lambda: [HOME / ".kiro"]),
    Agent("lmstudio", "LM Studio", "LM Studio", lambda: HOME / ".lmstudio" / "mcp.json",
          dirs=lambda: [HOME / ".lmstudio"]),
]
AGENT_BY_ID = {a.id: a for a in AGENTS}
# Lecteur « tous formats » pour le JSON collé depuis une documentation
GENERIC = Agent("generic", "Générique", "JSON", lambda: HOME, stdio_type="stdio", http_type="http", sse_type="sse")


def parse_snippet(obj) -> dict:
    """Accepte {"mcpServers": {...}}, {"servers": {...}}, {"mcp": {...}}, {"nom": {...}} ou un serveur seul."""
    def is_srv(v):
        return isinstance(v, dict) and any(k in v for k in ("command", "url", "httpUrl", "serverUrl"))

    if not isinstance(obj, dict):
        raise ValueError("Le JSON doit être un objet { … }.")
    for k in ("mcpServers", "servers", "mcp", "mcp_servers", "context_servers"):
        v = obj.get(k)
        if isinstance(v, dict) and v and all(is_srv(x) for x in v.values()):
            return v
    if is_srv(obj):
        return {"": obj}
    if obj and all(is_srv(v) for v in obj.values()):
        return obj
    raise ValueError("Format non reconnu. Attendu : {\"mcpServers\": {\"nom\": {…}}}, "
                     "{\"nom\": {\"command\": …}} ou un serveur seul {\"command\": …}.")


# =========================================================================== #
#  Réglages, sauvegardes, accès aux fichiers
# =========================================================================== #
class Settings:
    def __init__(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        try:
            self.d = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            if not isinstance(self.d, dict):
                self.d = {}
        except Exception:
            self.d = {}
        self.d.setdefault("paths", {})
        self.d.setdefault("parked", {})
        self.d.setdefault("show_all", False)

    def save(self):
        write_text_atomic(SETTINGS_FILE, json.dumps(self.d, indent=2, ensure_ascii=False) + "\n")

    def parked(self, aid: str) -> dict:
        return self.d["parked"].setdefault(aid, {})

    def parked_of(self, aid: str) -> dict:
        return self.d["parked"].get(aid) or {}


def backup_file(a: Agent, path: Path):
    if not path.exists():
        return
    d = BACKUP_DIR / a.id
    d.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, d / f"{now_stamp()}__{path.name}")
    for old in sorted(d.iterdir())[:-MAX_BACKUPS]:
        try:
            old.unlink()
        except OSError:
            pass


def modify_file(a: Agent, fn) -> bool:
    """Relit le fichier, applique fn(serveurs) puis réécrit seulement si quelque chose a changé."""
    p = a.path()
    text = p.read_text(encoding="utf-8-sig") if p.exists() else ""
    if a.fmt == "toml":
        if tomllib is None:
            raise RuntimeError("Python 3.11 ou plus récent est nécessaire pour le TOML.")
        data = tomllib.loads(text) if text.strip() else {}
        cur = data.get("mcp_servers")
        servers = dict(cur) if isinstance(cur, dict) else {}
        before = json.dumps(servers, default=str)
        fn(servers)
        if json.dumps(servers, default=str) == before:
            return False
        backup_file(a, p)
        write_text_atomic(p, toml_replace_mcp(text, servers))
        return True
    data = parse_json_text(text)[0] if text.strip() else {}
    if not isinstance(data, dict):
        raise ValueError("Le fichier ne contient pas un objet JSON { … }.")
    node = data
    for k in a.root[:-1]:
        if not isinstance(node.get(k), dict):
            node[k] = {}
        node = node[k]
    cur = node.get(a.root[-1])
    servers = cur if isinstance(cur, dict) else {}
    before = json.dumps(servers)
    fn(servers)
    if isinstance(cur, dict) and json.dumps(servers) == before:
        return False
    node[a.root[-1]] = servers
    backup_file(a, p)
    write_text_atomic(p, json.dumps(data, indent=detect_indent(text), ensure_ascii=False) + "\n")
    return True


def replace_key(s: dict, old: str, new: str, val):
    """Remplace s[old] par s[new]=val en gardant la position (supprime un éventuel s[new] existant)."""
    items = [(k, v) for k, v in s.items() if k != new or k == old]
    s.clear()
    for k, v in items:
        s[new if k == old else k] = val if k == old else v


@dataclass
class Srv:
    name: str
    native: dict
    canon: dict
    parked: bool = False


@dataclass
class AgentState:
    servers: list = field(default_factory=list)
    err: str | None = None
    warns: list = field(default_factory=list)
    lossy: bool = False
    detected: bool = False
    exists: bool = False

    def find(self, name: str) -> Srv | None:
        return next((s for s in self.servers if s.name == name), None)


def load_state(a: Agent, settings: Settings) -> AgentState:
    p = a.path()
    st = AgentState(exists=p.exists())
    natives = {}
    try:
        natives, st.lossy = read_root(p, a.root, a.fmt)
    except Exception as e:
        st.err = f"Fichier illisible : {e}"
    for n, v in natives.items():
        if isinstance(v, dict):
            st.servers.append(Srv(n, v, a.to_canon(v)))
        else:
            st.warns.append(f"Entrée « {n} » ignorée : ce n'est pas un objet.")
    for n, v in settings.parked_of(a.id).items():
        if n not in natives:
            c = a.to_canon(v)
            c["enabled"] = False
            st.servers.append(Srv(n, v, c, parked=True))
    if a.warnings:
        try:
            st.warns += a.warnings(a)
        except Exception:
            pass
    st.detected = (st.exists or any(Path(d).exists() for d in a.dirs())
                   or any(shutil.which(c) for c in a.clis))
    return st


# =========================================================================== #
#  Test d'un serveur MCP
# =========================================================================== #
INIT_REQ = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "gestionnaire-mcp", "version": APP_VERSION}}}
TOOLS_REQ = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}


def kill_tree(proc: subprocess.Popen):
    try:
        if IS_WIN:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           capture_output=True, creationflags=NO_WINDOW)
        else:
            proc.kill()
    except Exception:
        pass


def test_stdio(c: dict, log, timeout_init=90, timeout_tools=30):
    cmd = c["command"]
    args = [shutil.which(cmd) or cmd] + c["args"]
    env = os.environ.copy()
    env.update(c["env"])
    log(f"▶ Lancement : {' '.join(args)}\n")
    try:
        proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=env, cwd=c["cwd"] or None, creationflags=NO_WINDOW)
    except Exception as e:
        log(f"✖ Impossible de lancer la commande : {e}\n")
        return
    out_q: queue.Queue = queue.Queue()
    err_lines: list[str] = []

    def rd_out():
        for line in iter(proc.stdout.readline, b""):
            out_q.put(line.decode("utf-8", "replace"))
        out_q.put(None)

    def rd_err():
        for line in iter(proc.stderr.readline, b""):
            err_lines.append(line.decode("utf-8", "replace"))

    threading.Thread(target=rd_out, daemon=True).start()
    threading.Thread(target=rd_err, daemon=True).start()

    def send(msg):
        proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
        proc.stdin.flush()

    def wait_id(i, timeout):
        end = time.time() + timeout
        while time.time() < end:
            try:
                line = out_q.get(timeout=0.5)
            except queue.Empty:
                if proc.poll() is not None and out_q.empty():
                    return None
                continue
            if line is None:
                return None
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                log(f"  (sortie non JSON) {line[:200]}\n")
                continue
            if msg.get("id") == i:
                return msg
        return None

    try:
        send(INIT_REQ)
        resp = wait_id(1, timeout_init)
        if not resp:
            log("✖ Pas de réponse à « initialize ».\n")
        elif "error" in resp:
            log(f"✖ Erreur initialize : {resp['error']}\n")
        else:
            info = resp.get("result", {}).get("serverInfo", {})
            log(f"✔ Connecté : {info.get('name', '?')} {info.get('version', '')}\n")
            send(INITIALIZED)
            send(TOOLS_REQ)
            tools = wait_id(2, timeout_tools)
            if tools and "result" in tools:
                lst = tools["result"].get("tools", [])
                log(f"✔ {len(lst)} outil(s) :\n")
                for t in lst:
                    desc = (t.get("description") or "").strip().splitlines()
                    log(f"   • {t.get('name')}  —  {desc[0][:100] if desc else ''}\n")
            else:
                log("⚠ Pas de réponse à « tools/list ».\n")
    except Exception as e:
        log(f"✖ Erreur : {e}\n")
    finally:
        kill_tree(proc)
        time.sleep(0.3)
        if err_lines:
            log("\n--- stderr (fin) ---\n" + "".join(err_lines[-25:]))
        log("\n■ Terminé.\n")


def test_http(c: dict, log):
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    headers.update(c["headers"])
    log(f"▶ POST {c['url']}\n")
    try:
        req = urllib.request.Request(c["url"], data=json.dumps(INIT_REQ).encode(), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read(200000).decode("utf-8", "replace")
            log(f"✔ HTTP {r.status} ({r.headers.get('Content-Type')})\n")
            msg = None
            for line in body.splitlines():
                line = line.strip()
                if line.startswith("data:"):
                    line = line[5:].strip()
                if line.startswith("{"):
                    try:
                        msg = json.loads(line)
                        break
                    except json.JSONDecodeError:
                        pass
            if msg and "result" in msg:
                info = msg["result"].get("serverInfo", {})
                log(f"✔ Serveur : {info.get('name', '?')} {info.get('version', '')}\n")
            else:
                log(body[:1500] + "\n")
    except urllib.error.HTTPError as e:
        log(f"✖ HTTP {e.code} : {e.reason}\n")
        if e.code == 401:
            log("  (authentification requise : normal pour un serveur OAuth, l'agent s'en charge)\n")
    except Exception as e:
        log(f"✖ {e}\n")
    log("\n■ Terminé.\n")


# =========================================================================== #
#  Fenêtres de dialogue
# =========================================================================== #
class TextDialog(tk.Toplevel):
    def __init__(self, master, title, text="", editable=True, ok_label="Valider", on_ok=None,
                 hint="", width=90, height=24):
        super().__init__(master)
        self.title(title)
        self.transient(master)
        self.on_ok = on_ok
        frm = ttk.Frame(self, padding=8)
        frm.pack(fill="both", expand=True)
        if hint:
            ttk.Label(frm, text=hint, foreground="#555", justify="left").grid(row=0, column=0, sticky="w",
                                                                             pady=(0, 6))
        self.txt = tk.Text(frm, wrap="none", width=width, height=height, font=("Consolas", 10), undo=True)
        ys = ttk.Scrollbar(frm, orient="vertical", command=self.txt.yview)
        xs = ttk.Scrollbar(frm, orient="horizontal", command=self.txt.xview)
        self.txt.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.txt.grid(row=1, column=0, sticky="nsew")
        ys.grid(row=1, column=1, sticky="ns")
        xs.grid(row=2, column=0, sticky="ew")
        frm.rowconfigure(1, weight=1)
        frm.columnconfigure(0, weight=1)
        self.txt.insert("1.0", text)
        if not editable:
            self.txt.configure(state="disabled")
        bar = ttk.Frame(frm)
        bar.grid(row=3, column=0, columnspan=2, sticky="e", pady=(8, 0))
        if on_ok:
            ttk.Button(bar, text=ok_label, command=self._ok).pack(side="right", padx=4)
        ttk.Button(bar, text="Fermer", command=self.destroy).pack(side="right")
        self.bind("<Escape>", lambda e: self.destroy())
        self.txt.focus_set()

    def append(self, s):
        def _do():
            if not self.winfo_exists():
                return
            self.txt.configure(state="normal")
            self.txt.insert("end", s)
            self.txt.see("end")
            self.txt.configure(state="disabled")
        try:
            self.after(0, _do)
        except (tk.TclError, RuntimeError):
            pass

    def _ok(self):
        if self.on_ok(self.txt.get("1.0", "end-1c")) is not False:
            self.destroy()


def parse_kv(text: str, sep: str, what: str) -> dict:
    out = {}
    for ln in text.splitlines():
        if not ln.strip():
            continue
        if sep not in ln:
            raise ValueError(f"{what} : la ligne « {ln.strip()} » doit être de la forme NOM{sep}valeur.")
        k, v = ln.split(sep, 1)
        out[k.strip()] = v.strip()
    return out


class ServerDialog(tk.Toplevel):
    """Édition d'un serveur : formulaire simple + onglet JSON au format exact de l'agent."""

    def __init__(self, master, agent: Agent, name: str = "", native: dict | None = None):
        super().__init__(master)
        self.title(f"{'Modifier' if native is not None else 'Nouveau'} serveur MCP — {agent.name}")
        self.transient(master)
        self.agent = agent
        self.result = None
        canon = agent.to_canon(native) if native is not None else empty_canon()
        self.extra = canon["extra"]
        self._tab = 0
        self._switching = False

        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)
        top = ttk.Frame(frm)
        top.pack(fill="x")
        ttk.Label(top, text="Nom").pack(side="left")
        self.name_var = tk.StringVar(value=name)
        self.name_entry = ttk.Entry(top, textvariable=self.name_var, width=40)
        self.name_entry.pack(side="left", padx=6)
        self.en_var = tk.BooleanVar(value=canon["enabled"])
        ttk.Checkbutton(top, text="Actif", variable=self.en_var).pack(side="left", padx=12)

        self.nb = ttk.Notebook(frm)
        self.nb.pack(fill="both", expand=True, pady=8)
        f = ttk.Frame(self.nb, padding=8)
        self.nb.add(f, text="Formulaire")
        f.columnconfigure(1, weight=1)
        ttk.Label(f, text="Type").grid(row=0, column=0, sticky="w")
        self.type_var = tk.StringVar(value=canon["type"])
        tf = ttk.Frame(f)
        tf.grid(row=0, column=1, sticky="w")
        for v, txt in (("stdio", "Local (commande)"), ("http", "Distant — HTTP"), ("sse", "Distant — SSE")):
            ttk.Radiobutton(tf, text=txt, value=v, variable=self.type_var,
                            command=self._type_changed).pack(side="left", padx=(0, 14))

        self.sf = sf = ttk.Frame(f)
        sf.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(8, 0))
        sf.columnconfigure(1, weight=1)
        ttk.Label(sf, text="Commande").grid(row=0, column=0, sticky="w")
        cf = ttk.Frame(sf)
        cf.grid(row=0, column=1, sticky="ew", pady=2)
        self.cmd_var = tk.StringVar()
        ttk.Entry(cf, textvariable=self.cmd_var).pack(side="left", fill="x", expand=True)
        ttk.Button(cf, text="…", width=3, command=self._browse).pack(side="left", padx=(4, 0))
        ttk.Label(sf, text="Arguments\n(un par ligne)").grid(row=1, column=0, sticky="nw")
        self.args_txt = tk.Text(sf, height=6, font=("Consolas", 10), undo=True)
        self.args_txt.grid(row=1, column=1, sticky="nsew", pady=2)
        ttk.Label(sf, text="Variables\nd'environnement\n(NOM=valeur)").grid(row=2, column=0, sticky="nw")
        self.env_txt = tk.Text(sf, height=4, font=("Consolas", 10), undo=True)
        self.env_txt.grid(row=2, column=1, sticky="nsew", pady=2)
        self.cwd_var = tk.StringVar()
        if agent.cwd_ok:
            ttk.Label(sf, text="Dossier de travail").grid(row=3, column=0, sticky="w")
            ttk.Entry(sf, textvariable=self.cwd_var).grid(row=3, column=1, sticky="ew", pady=2)
        sf.rowconfigure(1, weight=1)

        self.rf = rf = ttk.Frame(f)
        rf.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(8, 0))
        rf.columnconfigure(1, weight=1)
        ttk.Label(rf, text="URL").grid(row=0, column=0, sticky="w")
        self.url_var = tk.StringVar()
        ttk.Entry(rf, textvariable=self.url_var).grid(row=0, column=1, sticky="ew", pady=2)
        ttk.Label(rf, text="En-têtes\n(Nom: valeur)").grid(row=1, column=0, sticky="nw")
        self.hdr_txt = tk.Text(rf, height=4, font=("Consolas", 10), undo=True)
        self.hdr_txt.grid(row=1, column=1, sticky="nsew", pady=2)
        if not agent.remote:
            ttk.Label(rf, foreground="#b35c00", wraplength=560, justify="left",
                      text=f"{agent.name} ne lit que des commandes : l'URL sera enregistrée sous la forme "
                           "« npx -y mcp-remote <url> » (Node.js requis).").grid(row=2, column=1, sticky="w")
        f.rowconfigure(1, weight=1)

        j = ttk.Frame(self.nb, padding=8)
        self.nb.add(j, text=f"JSON ({agent.short})")
        ttk.Label(j, foreground="#555", wraplength=640, justify="left",
                  text="Le serveur tel qu'il sera écrit dans le fichier. Vous pouvez aussi coller ici le JSON "
                       "d'une documentation, dans n'importe quel format (Claude, OpenCode, VS Code…) : "
                       "il sera converti au format de l'agent.").pack(anchor="w", pady=(0, 4))
        self.json_txt = tk.Text(j, font=("Consolas", 10), undo=True, wrap="none", height=16)
        self.json_txt.pack(fill="both", expand=True)
        self.nb.bind("<<NotebookTabChanged>>", self._tab_changed)

        bar = ttk.Frame(frm)
        bar.pack(fill="x")
        ttk.Button(bar, text="Annuler", command=self.destroy).pack(side="right")
        ttk.Button(bar, text="Enregistrer", command=self._ok).pack(side="right", padx=6)

        self._fill(canon)
        self.geometry("760x560")
        self.bind("<Escape>", lambda e: self.destroy())
        self.grab_set()
        self.name_entry.focus_set()

    # ------------------------------------------------------------------ form
    def _fill(self, c: dict):
        self.type_var.set(c["type"])
        self.en_var.set(c["enabled"])
        self.cmd_var.set(c["command"])
        self.cwd_var.set(c["cwd"])
        self.url_var.set(c["url"])
        for w, val in ((self.args_txt, "\n".join(c["args"])),
                       (self.env_txt, "\n".join(f"{k}={v}" for k, v in c["env"].items())),
                       (self.hdr_txt, "\n".join(f"{k}: {v}" for k, v in c["headers"].items()))):
            w.delete("1.0", "end")
            w.insert("1.0", val)
        self._type_changed()

    def _type_changed(self):
        if self.type_var.get() == "stdio":
            self.rf.grid_remove()
            self.sf.grid()
        else:
            self.sf.grid_remove()
            self.rf.grid()

    def _browse(self):
        p = filedialog.askopenfilename(parent=self, title="Choisir l'exécutable du serveur")
        if p:
            self.cmd_var.set(os.path.normpath(p))

    def _form_canon(self) -> dict:
        c = empty_canon()
        c["type"] = self.type_var.get()
        c["enabled"] = self.en_var.get()
        c["command"] = self.cmd_var.get().strip()
        c["args"] = [ln.strip() for ln in self.args_txt.get("1.0", "end-1c").splitlines() if ln.strip()]
        c["env"] = parse_kv(self.env_txt.get("1.0", "end-1c"), "=", "Variables d'environnement")
        c["cwd"] = self.cwd_var.get().strip()
        c["url"] = self.url_var.get().strip()
        c["headers"] = parse_kv(self.hdr_txt.get("1.0", "end-1c"), ":", "En-têtes")
        c["extra"] = copy.deepcopy(self.extra)
        return c

    def _from_json(self) -> tuple[str, dict]:
        txt = self.json_txt.get("1.0", "end-1c").strip()
        if not txt:
            raise ValueError("Le JSON est vide.")
        servers = parse_snippet(parse_json_text(txt)[0])
        if len(servers) != 1:
            raise ValueError("Ce JSON contient plusieurs serveurs : utilisez « Coller du JSON » "
                             "dans la fenêtre principale.")
        (n, nat), = servers.items()
        c = self.agent.to_canon(nat)
        if "enabled" not in nat and "disabled" not in nat:
            c["enabled"] = self.en_var.get()
        self.extra = c["extra"]
        return n, c

    def _tab_changed(self, _e=None):
        if self._switching:
            return
        new = self.nb.index("current")
        try:
            if new == 1 and self._tab == 0:
                nat = self.agent.from_canon(self._form_canon(), keep_extra=True)
                self.json_txt.delete("1.0", "end")
                self.json_txt.insert("1.0", json.dumps(nat, indent=2, ensure_ascii=False))
            elif new == 0 and self._tab == 1:
                n, c = self._from_json()
                if n:
                    self.name_var.set(n)
                self._fill(c)
        except ValueError as e:
            messagebox.showerror(APP_NAME, str(e), parent=self)
            self._switching = True
            self.nb.select(self._tab)
            self._switching = False
            return
        self._tab = new

    def _ok(self):
        try:
            if self.nb.index("current") == 1:
                n, c = self._from_json()
                if n:
                    self.name_var.set(n)
            else:
                c = self._form_canon()
            name = self.name_var.get().strip()
            if not name:
                raise ValueError("Donnez un nom au serveur.")
            if c["type"] == "stdio" and not c["command"]:
                raise ValueError("Indiquez la commande à lancer.")
            if c["type"] != "stdio" and not c["url"]:
                raise ValueError("Indiquez l'URL du serveur.")
            native = self.agent.from_canon(c, keep_extra=True)
        except ValueError as e:
            messagebox.showerror(APP_NAME, str(e), parent=self)
            return
        self.result = (name, native, c["enabled"])
        self.destroy()


class CopyDialog(tk.Toplevel):
    def __init__(self, master, names: list[str], agents: list[Agent], states: dict):
        super().__init__(master)
        self.title("Copier vers d'autres agents")
        self.transient(master)
        self.result: list[Agent] = []
        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text=f"Copier : {', '.join(names)}", wraplength=460, justify="left",
                  font=("Segoe UI", 10, "bold")).pack(anchor="w")
        ttk.Label(frm, text="Le format est converti automatiquement pour chaque agent.",
                  foreground="#555").pack(anchor="w", pady=(2, 8))
        self.vars = []
        for a in agents:
            v = tk.BooleanVar(value=False)
            st = states[a.id]
            have = [n for n in names if st.find(n)]
            txt = a.name + (f"   (déjà présent : {', '.join(have)})" if have else "")
            ttk.Checkbutton(frm, text=txt, variable=v).pack(anchor="w", pady=1)
            self.vars.append((a, v))
        bar = ttk.Frame(frm)
        bar.pack(fill="x", pady=(12, 0))
        ttk.Button(bar, text="Annuler", command=self.destroy).pack(side="right")
        ttk.Button(bar, text="Copier", command=self._ok).pack(side="right", padx=6)
        self.bind("<Escape>", lambda e: self.destroy())
        self.grab_set()

    def _ok(self):
        self.result = [a for a, v in self.vars if v.get()]
        self.destroy()


# =========================================================================== #
#  Fenêtre principale
# =========================================================================== #
OVERVIEW = "__overview__"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1280x740")
        self.minsize(980, 560)
        self.settings = Settings()
        for a in AGENTS:
            a.override = self.settings.d["paths"].get(a.id)
        self.states: dict[str, AgentState] = {}
        self.current: Agent | None = None
        self.rows: dict[str, object] = {}
        self.lossy_ok: set[str] = set()
        self._filling = False

        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Treeview", rowheight=24)
        style.configure("Title.TLabel", font=("Segoe UI", 13, "bold"))
        style.configure("Side.TLabel", font=("Segoe UI", 10, "bold"))

        self._build_menu()
        self._build_ui()
        self.refresh()

    # ----------------------------------------------------------------- menus
    def _build_menu(self):
        m = tk.Menu(self)
        f = tk.Menu(m, tearoff=0)
        f.add_command(label="Coller du JSON dans l'agent…", command=self.paste_json)
        f.add_command(label="Exporter les serveurs de l'agent…", command=self.export_json)
        f.add_separator()
        f.add_command(label="Où sont les fichiers de chaque agent ?", command=self.show_locations)
        f.add_command(label="Ouvrir le dossier des sauvegardes", command=lambda: open_path(self._mk(BACKUP_DIR)))
        f.add_separator()
        f.add_command(label="Quitter", command=self.destroy)
        m.add_cascade(label="Fichier", menu=f)
        a = tk.Menu(m, tearoff=0)
        a.add_command(label="Ouvrir le fichier de configuration", command=self.open_file)
        a.add_command(label="Ouvrir le dossier", command=self.open_folder)
        a.add_separator()
        a.add_command(label="Utiliser un autre fichier…", command=self.change_path)
        a.add_command(label="Revenir à l'emplacement par défaut", command=self.reset_path)
        a.add_separator()
        a.add_command(label="Redémarrer l'application…", command=self.restart_agent)
        a.add_separator()
        a.add_command(label="Restaurer une sauvegarde…", command=self.restore_backup)
        m.add_cascade(label="Agent", menu=a)
        self.config(menu=m)

    @staticmethod
    def _mk(p: Path) -> Path:
        p.mkdir(parents=True, exist_ok=True)
        return p

    # -------------------------------------------------------------------- UI
    def _build_ui(self):
        self.status = ttk.Label(self, anchor="w", padding=(8, 3), relief="sunken")
        self.status.pack(side="bottom", fill="x")
        root = ttk.Frame(self, padding=8)
        root.pack(fill="both", expand=True)
        paned = ttk.PanedWindow(root, orient="horizontal")
        paned.pack(fill="both", expand=True)

        left = ttk.Frame(paned, padding=(0, 0, 8, 0))
        paned.add(left, weight=0)
        ttk.Label(left, text="Agents de codage", style="Side.TLabel").pack(anchor="w", pady=(0, 4))
        self.agent_tree = ttk.Treeview(left, show="tree", selectmode="browse")
        self.agent_tree.column("#0", width=290)
        self.agent_tree.tag_configure("missing", foreground="#999")
        self.agent_tree.tag_configure("err", foreground="#c00000")
        self.agent_tree.pack(fill="both", expand=True)
        self.agent_tree.bind("<<TreeviewSelect>>", self.on_agent_select)
        self.show_all_var = tk.BooleanVar(value=bool(self.settings.d.get("show_all")))
        ttk.Checkbutton(left, text="Afficher aussi les agents non détectés", variable=self.show_all_var,
                        command=self.toggle_show_all).pack(anchor="w", pady=(6, 0))
        ttk.Button(left, text="⟳ Relire les fichiers", command=self.refresh).pack(fill="x", pady=(6, 0))

        right = ttk.Frame(paned)
        paned.add(right, weight=1)
        self.title_lbl = ttk.Label(right, style="Title.TLabel")
        self.title_lbl.pack(anchor="w")
        self.path_lbl = ttk.Label(right, foreground="#2b5797", cursor="hand2")
        self.path_lbl.pack(anchor="w", pady=(2, 0))
        self.path_lbl.bind("<Button-1>", lambda e: self.open_folder())
        self.info_lbl = ttk.Label(right, foreground="#555", justify="left")
        self.info_lbl.pack(anchor="w", fill="x", pady=(4, 0))
        self.warn_lbl = ttk.Label(right, foreground="#b35c00", justify="left")
        self.warn_lbl.pack(anchor="w", fill="x")
        right.bind("<Configure>", lambda e: [w.configure(wraplength=max(300, e.width - 20))
                                             for w in (self.info_lbl, self.warn_lbl)])

        tf = ttk.Frame(right)
        tf.pack(fill="both", expand=True, pady=6)
        self.tree = ttk.Treeview(tf, show="headings", selectmode="extended")
        ys = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=ys.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        tf.rowconfigure(0, weight=1)
        tf.columnconfigure(0, weight=1)
        self.tree.tag_configure("off", foreground="#999")
        self.tree.tag_configure("warn", foreground="#c00000")
        self.tree.bind("<Double-1>", self.on_double)
        self.tree.bind("<Return>", lambda e: self.edit_server())
        self.tree.bind("<Delete>", lambda e: self.delete_servers())

        self.btn_bar = ttk.Frame(right)
        self.btn_bar.pack(fill="x")
        self.agent_btns = []
        for txt, cmd in (("➕ Ajouter", self.new_server), ("✎ Modifier", self.edit_server),
                         ("⏯ Activer / Désactiver", self.toggle_selected), ("⧉ Copier vers…", self.copy_to),
                         ("🗑 Supprimer", self.delete_servers), ("🧪 Tester", self.test_server),
                         ("⇩ Coller du JSON", self.paste_json), ("📄 Ouvrir le fichier", self.open_file)):
            b = ttk.Button(self.btn_bar, text=txt, command=cmd)
            b.pack(side="left", padx=2)
            self.agent_btns.append(b)

    def say(self, msg: str):
        self.status.configure(text=msg)

    # ---------------------------------------------------------------- données
    def visible_agents(self) -> list[Agent]:
        show_all = self.show_all_var.get()
        return [a for a in AGENTS if show_all or self.states[a.id].detected or a is self.current]

    def refresh(self):
        keep = [s.name for s in self.selected_servers()] if self.current else []
        self.states = {a.id: load_state(a, self.settings) for a in AGENTS}
        self._fill_agents()
        self._show(keep)

    def _fill_agents(self):
        self._filling = True
        t = self.agent_tree
        t.delete(*t.get_children())
        t.insert("", "end", iid=OVERVIEW, text="🗂  Vue d'ensemble (tous les agents)")
        for a in self.visible_agents():
            st = self.states[a.id]
            n = len(st.servers)
            if st.err:
                txt = f"{a.name}   ⚠ fichier illisible"
            elif st.exists or n:
                txt = f"{a.name}   ({n})"
            else:
                txt = f"{a.name}   (pas encore de fichier)"
            tag = "err" if st.err else ("" if st.detected else "missing")
            t.insert("", "end", iid=a.id, text=txt, tags=(tag,))
        t.selection_set(self.current.id if self.current else OVERVIEW)
        self._filling = False

    def on_agent_select(self, _e=None):
        if self._filling:
            return
        sel = self.agent_tree.selection()
        if not sel:
            return
        self.current = None if sel[0] == OVERVIEW else AGENT_BY_ID[sel[0]]
        self._show()

    def toggle_show_all(self):
        self.settings.d["show_all"] = self.show_all_var.get()
        self.settings.save()
        self._fill_agents()
        if self.current is None:
            self._show()

    def _set_columns(self, cols: list[tuple]):
        self.tree.delete(*self.tree.get_children())
        self.tree.configure(columns=[c[0] for c in cols], displaycolumns=[c[0] for c in cols])
        for cid, title, width, stretch, anchor in cols:
            self.tree.heading(cid, text=title, anchor=anchor)
            self.tree.column(cid, width=width, minwidth=40, stretch=stretch, anchor=anchor)

    def _show(self, keep: list[str] | None = None):
        self.rows = {}
        if self.current is None:
            self._show_overview()
        else:
            self._show_agent(keep or [])

    def _show_overview(self):
        agents = self.visible_agents()
        self.title_lbl.configure(text="Vue d'ensemble")
        self.path_lbl.configure(text="")
        self.info_lbl.configure(text="✔ actif   ⏸ désactivé   (vide) absent.\nDouble-clic sur une case : "
                                     "active/désactive le serveur dans cet agent, ou l'y copie s'il est absent "
                                     "(le format est converti automatiquement).")
        errs = [f"{a.name} : {self.states[a.id].err}" for a in agents if self.states[a.id].err]
        self.warn_lbl.configure(text="\n".join("⚠ " + e for e in errs))
        cols = [("nom", "Serveur MCP", 220, True, "w")]
        cols += [(a.id, a.short, 92, False, "center") for a in agents]
        self._set_columns(cols)
        names: list[str] = []
        for a in agents:
            for s in self.states[a.id].servers:
                if s.name not in names:
                    names.append(s.name)
        for i, n in enumerate(sorted(names, key=str.lower)):
            vals = [n]
            for a in agents:
                s = self.states[a.id].find(n)
                vals.append("" if not s else "⚠" if canon_invalid(s.canon) else
                            "✔" if s.canon["enabled"] else "⏸")
            iid = f"r{i}"
            self.tree.insert("", "end", iid=iid, values=vals)
            self.rows[iid] = n
        self.say(f"{len(names)} serveur(s) MCP au total sur {len(agents)} agent(s).")

    def _show_agent(self, keep: list[str]):
        a = self.current
        st = self.states[a.id]
        p = a.path()
        self.title_lbl.configure(text=a.name)
        txt = f"📄 {p}"
        if a.override:
            txt += "   (emplacement personnalisé)"
        if not st.exists:
            txt += "   — n'existe pas encore : il sera créé au premier ajout"
        self.path_lbl.configure(text=txt)
        self.info_lbl.configure(text="\n".join(x for x in (a.note, a.disable_note) if x))
        warns = ([st.err + " — corrigez-le (menu Agent › Ouvrir le fichier) puis ⟳."] if st.err else []) + st.warns
        if st.lossy:
            warns.append("Ce fichier contient des commentaires : ils seront supprimés à la prochaine modification.")
        self.warn_lbl.configure(text="\n".join("⚠ " + w for w in warns))
        self._set_columns([("etat", "État", 210, False, "w"), ("nom", "Nom", 200, False, "w"),
                           ("type", "Type", 90, False, "w"), ("cible", "Commande / URL", 500, True, "w")])
        for i, s in enumerate(st.servers):
            c = s.canon
            if canon_invalid(c):
                etat, tag = "⚠ incomplet / mal formé", "warn"
            elif c["enabled"]:
                etat, tag = "✔ actif", ""
            else:
                etat, tag = ("⏸ désactivé (gardé à part)" if s.parked else "⏸ désactivé"), "off"
            typ = {"stdio": "local", "http": "distant HTTP", "sse": "distant SSE"}[c["type"]]
            iid = f"s{i}"
            self.tree.insert("", "end", iid=iid, values=(etat, s.name, typ, canon_summary(c)), tags=(tag,))
            self.rows[iid] = s
            if s.name in keep:
                self.tree.selection_add(iid)
        self.say(f"{a.name} : {len(st.servers)} serveur(s).")

    def selected_servers(self) -> list[Srv]:
        return [self.rows[i] for i in self.tree.selection() if isinstance(self.rows.get(i), Srv)]

    def need_agent(self) -> Agent | None:
        if self.current is None:
            messagebox.showinfo(APP_NAME, "Choisissez d'abord un agent dans la liste de gauche.")
        return self.current

    def need_selection(self, single=False) -> list[Srv]:
        if not self.need_agent():
            return []
        s = self.selected_servers()
        if not s:
            messagebox.showinfo(APP_NAME, "Sélectionnez un serveur dans la liste.")
        elif single and len(s) > 1:
            messagebox.showinfo(APP_NAME, "Sélectionnez un seul serveur.")
            return []
        return s

    # -------------------------------------------------------------- écriture
    def write(self, a: Agent, fn) -> bool:
        st = self.states[a.id]
        if st.err and a.path().exists():
            messagebox.showerror(APP_NAME, f"{a.path()}\n\n{st.err}\n\nCorrigez le fichier avant de le modifier ici.")
            return False
        if st.lossy and a.id not in self.lossy_ok:
            if not messagebox.askyesno(APP_NAME, f"{a.path()} contient des commentaires (JSONC).\n"
                                                 "Ils seront supprimés à l'enregistrement (une sauvegarde est faite).\n\n"
                                                 "Continuer ?"):
                return False
            self.lossy_ok.add(a.id)
        try:
            modify_file(a, fn)
        except Exception as e:
            messagebox.showerror(APP_NAME, f"Impossible d'écrire {a.path()} :\n\n{e}")
            return False
        return True

    def store_many(self, a: Agent, ops: list[dict]) -> bool:
        """ops : {name, native, enabled, old (nom actuel ou None), old_parked}."""
        def fn(s):
            for op in ops:
                old, name = op.get("old"), op["name"]
                if a.disable == "park" and not op["enabled"]:
                    if old is not None and not op.get("old_parked"):
                        s.pop(old, None)
                    s.pop(name, None)
                elif old is not None and not op.get("old_parked") and old in s:
                    replace_key(s, old, name, op["native"])
                else:
                    s[name] = op["native"]

        if not self.write(a, fn):
            return False
        parked = self.settings.parked(a.id)
        for op in ops:
            if op.get("old_parked"):
                parked.pop(op["old"], None)
            if a.disable == "park" and not op["enabled"]:
                parked[op["name"]] = op["native"]
            else:
                parked.pop(op["name"], None)
        if not parked:
            self.settings.d["parked"].pop(a.id, None)
        self.settings.save()
        return True

    def add_canons(self, t: Agent, items: list[tuple[str, dict]], keep_extra: bool) -> list[str] | None:
        """Ajoute des serveurs (format canon) à l'agent t. Retourne les notes de conversion, ou None si annulé."""
        st = self.states[t.id]
        conflicts = [n for n, _ in items if st.find(n)]
        if conflicts:
            r = messagebox.askyesnocancel(
                APP_NAME, f"{t.name} contient déjà : {', '.join(conflicts)}.\n\n"
                          "Oui = remplacer   ·   Non = garder l'existant   ·   Annuler = ne rien faire")
            if r is None:
                return None
            if not r:
                items = [(n, c) for n, c in items if n not in conflicts]
        ops, notes = [], []
        for n, c in items:
            ex = st.find(n)
            ops.append({"name": n, "native": t.from_canon(c, keep_extra), "enabled": c["enabled"],
                        "old": ex.name if ex else None, "old_parked": ex.parked if ex else False})
            notes += t.conversion_notes(n, c)
        if ops and not self.store_many(t, ops):
            return None
        return notes

    def done(self, a: Agent, what: str):
        self.refresh()
        self.say(f"✔ {a.name} : {what}. {a.reload}")

    # ---------------------------------------------------------------- actions
    def new_server(self):
        a = self.need_agent()
        if not a:
            return
        dlg = ServerDialog(self, a)
        self.wait_window(dlg)
        if not dlg.result:
            return
        name, native, en = dlg.result
        ex = self.states[a.id].find(name)
        if ex and not messagebox.askyesno(APP_NAME, f"« {name} » existe déjà dans {a.name}. Le remplacer ?"):
            return
        if self.store_many(a, [{"name": name, "native": native, "enabled": en,
                                "old": ex.name if ex else None, "old_parked": ex.parked if ex else False}]):
            self.done(a, f"« {name} » ajouté")

    def edit_server(self):
        sel = self.need_selection(single=True)
        if not sel:
            return
        a, s = self.current, sel[0]
        dlg = ServerDialog(self, a, s.name, s.native)
        self.wait_window(dlg)
        if not dlg.result:
            return
        name, native, en = dlg.result
        if name != s.name and self.states[a.id].find(name):
            if not messagebox.askyesno(APP_NAME, f"« {name} » existe déjà dans {a.name}. Le remplacer ?"):
                return
        if self.store_many(a, [{"name": name, "native": native, "enabled": en,
                                "old": s.name, "old_parked": s.parked}]):
            self.done(a, f"« {name} » enregistré")

    def toggle(self, a: Agent, srvs: list[Srv]) -> bool:
        ops = []
        for s in srvs:
            en = not s.canon["enabled"]
            nat = s.native if a.disable == "park" else a.set_enabled(s.native, en)
            ops.append({"name": s.name, "native": nat, "enabled": en, "old": s.name, "old_parked": s.parked})
        return self.store_many(a, ops)

    def toggle_selected(self):
        sel = self.need_selection()
        if sel and self.toggle(self.current, sel):
            self.done(self.current, "activation modifiée")

    def delete_servers(self):
        sel = self.need_selection()
        if not sel:
            return
        a = self.current
        if not messagebox.askyesno(APP_NAME, f"Supprimer de {a.name} :\n\n" + "\n".join(f"  • {s.name}" for s in sel)
                                   + "\n\n(une sauvegarde du fichier est faite avant)"):
            return
        in_file = [s.name for s in sel if not s.parked]
        if in_file and not self.write(a, lambda d: [d.pop(n, None) for n in in_file]):
            return
        parked = self.settings.parked(a.id)
        for s in sel:
            if s.parked:
                parked.pop(s.name, None)
        if not parked:
            self.settings.d["parked"].pop(a.id, None)
        self.settings.save()
        self.done(a, f"{len(sel)} serveur(s) supprimé(s)")

    def copy_to(self):
        sel = self.need_selection()
        if not sel:
            return
        a = self.current
        targets = [t for t in AGENTS if t is not a and (self.show_all_var.get() or self.states[t.id].detected)]
        dlg = CopyDialog(self, [s.name for s in sel], targets, self.states)
        self.wait_window(dlg)
        if not dlg.result:
            return
        done, notes = [], []
        for t in dlg.result:
            r = self.add_canons(t, [(s.name, copy.deepcopy(s.canon)) for s in sel], keep_extra=False)
            if r is not None:
                done.append(t)
                notes += r
        self.refresh()
        if done:
            msg = "Copié vers : " + ", ".join(t.name for t in done)
            if notes:
                msg += "\n\nConversions :\n" + "\n".join("• " + n for n in notes)
            msg += "\n\nPensez à relancer : " + ", ".join(t.short for t in done) + "."
            messagebox.showinfo(APP_NAME, msg)
            self.say("✔ " + msg.splitlines()[0])

    def on_double(self, event):
        if self.current is not None:
            if self.tree.identify_row(event.y):
                self.edit_server()
            return
        row, col = self.tree.identify_row(event.y), self.tree.identify_column(event.x)
        if not row or not col or col in ("#0", "#1"):
            return
        t = AGENT_BY_ID[self.tree["columns"][int(col[1:]) - 1]]
        name = self.rows[row]
        s = self.states[t.id].find(name)
        if s:
            if self.toggle(t, [s]):
                self.done(t, f"« {name} » {'désactivé' if s.canon['enabled'] else 'activé'}")
            return
        srcs = [(a, self.states[a.id].find(name)) for a in AGENTS if self.states[a.id].find(name)]
        srcs.sort(key=lambda x: (not x[1].canon["enabled"], canon_invalid(x[1].canon)))
        src_a, src = srcs[0]
        if not messagebox.askyesno(APP_NAME, f"Copier « {name} » de {src_a.name} vers {t.name} ?\n\n"
                                             "(format converti automatiquement)"):
            return
        c = copy.deepcopy(src.canon)
        c["enabled"] = True
        notes = self.add_canons(t, [(name, c)], keep_extra=False)
        if notes is not None:
            self.done(t, f"« {name} » copié depuis {src_a.short}" + (" — " + " ".join(notes) if notes else ""))

    def test_server(self):
        sel = self.need_selection(single=True)
        if not sel:
            return
        c = sel[0].canon
        dlg = TextDialog(self, f"Test de « {sel[0].name} »", "", editable=False, width=100, height=26)
        fn = test_stdio if c["type"] == "stdio" else test_http
        threading.Thread(target=fn, args=(c, dlg.append), daemon=True).start()

    def paste_json(self):
        a = self.need_agent()
        if not a:
            return

        def ok(txt):
            try:
                servers = parse_snippet(parse_json_text(txt.strip())[0])
            except ValueError as e:
                messagebox.showerror(APP_NAME, str(e), parent=dlg)
                return False
            items = []
            for n, nat in servers.items():
                if not n:
                    n = simpledialog.askstring(APP_NAME, "Nom de ce serveur :", parent=dlg)
                    if not n:
                        return False
                c = GENERIC.to_canon(nat)
                if "enabled" not in nat and "disabled" not in nat:
                    c["enabled"] = True
                items.append((n.strip(), c))
            notes = self.add_canons(a, items, keep_extra=not a.strict)
            if notes is None:
                return False
            self.done(a, f"{len(items)} serveur(s) importé(s)" + (" — " + " ".join(notes) if notes else ""))
            return True

        dlg = TextDialog(self, f"Coller du JSON dans {a.name}", "", ok_label="Ajouter", on_ok=ok, height=20,
                         hint="Collez la configuration copiée d'une documentation ou d'un autre agent, par ex. :\n"
                              '  {"mcpServers": {"nom": {"command": "npx", "args": ["-y", "paquet"]}}}\n'
                              "Tous les formats sont acceptés (Claude, OpenCode, VS Code, Cursor…) : "
                              f"ils sont convertis au format de {a.name}.")

    def export_json(self):
        a = self.need_agent()
        if not a:
            return
        st = self.states[a.id]
        p = filedialog.asksaveasfilename(title="Exporter", defaultextension=".json",
                                         initialfile=f"mcp_{a.id}.json", filetypes=[("JSON", "*.json")])
        if not p:
            return
        data = {a.root[-1]: {s.name: s.native for s in st.servers if not s.parked}}
        write_text_atomic(Path(p), json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        self.say(f"✔ Exporté : {p}")

    # ----------------------------------------------------------- fichiers
    def open_file(self):
        a = self.need_agent()
        if not a:
            return
        p = a.path()
        if p.exists():
            open_path(p)
        else:
            messagebox.showinfo(APP_NAME, f"Le fichier n'existe pas encore :\n{p}\n\nIl sera créé au premier ajout.")

    def open_folder(self):
        a = self.need_agent()
        if not a:
            return
        d = a.path().parent
        if d.exists():
            open_path(d)
        else:
            messagebox.showinfo(APP_NAME, f"Le dossier n'existe pas encore :\n{d}")

    def change_path(self):
        a = self.need_agent()
        if not a:
            return
        cur = a.path()
        p = filedialog.asksaveasfilename(title=f"Fichier de configuration de {a.name}",
                                         initialdir=str(cur.parent) if cur.parent.exists() else str(HOME),
                                         initialfile=cur.name, confirmoverwrite=False,
                                         filetypes=[("Configuration", "*.json *.jsonc *.toml"), ("Tous", "*.*")])
        if not p:
            return
        a.override = os.path.normpath(p)
        self.settings.d["paths"][a.id] = a.override
        self.settings.save()
        self.refresh()

    def reset_path(self):
        a = self.need_agent()
        if not a:
            return
        a.override = None
        self.settings.d["paths"].pop(a.id, None)
        self.settings.save()
        self.refresh()

    def restart_agent(self):
        a = self.need_agent()
        if not a:
            return
        target = RESTART_TARGETS.get(a.id)
        if not target:
            messagebox.showinfo(
                APP_NAME,
                f"Le redémarrage automatique de {a.name} n'est pas disponible. "
                f"{a.reload}",
            )
            return
        if not IS_WIN:
            messagebox.showinfo(
                APP_NAME,
                "Le redémarrage automatique est actuellement disponible sous Windows seulement.",
            )
            return
        if not messagebox.askyesno(
                APP_NAME,
                f"Fermer puis relancer {target[0]} ?\n\n"
                "Toutes ses fenêtres seront fermées. Enregistrez votre travail avant de continuer.",
        ):
            return
        try:
            restart_windows_app(target)
        except (OSError, RuntimeError, subprocess.SubprocessError) as e:
            messagebox.showerror(APP_NAME, str(e))
            return
        self.say(f"✔ {target[0]} a été redémarré.")

    def restore_backup(self):
        a = self.need_agent()
        if not a:
            return
        d = BACKUP_DIR / a.id
        files = sorted(d.glob("*"), reverse=True) if d.is_dir() else []
        if not files:
            messagebox.showinfo(APP_NAME, f"Aucune sauvegarde pour {a.name}.")
            return
        win = tk.Toplevel(self)
        win.title(f"Sauvegardes — {a.name}")
        win.transient(self)
        frm = ttk.Frame(win, padding=10)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text=f"Restaurer {a.path()} à partir de :").pack(anchor="w")
        lb = tk.Listbox(frm, width=70, height=14, font=("Consolas", 10))
        lb.pack(fill="both", expand=True, pady=6)
        for f in files:
            ts = f.name.split("__")[0]
            try:
                ts = _dt.datetime.strptime(ts[:15], "%Y%m%d_%H%M%S").strftime("%d/%m/%Y %H:%M:%S")
            except ValueError:
                pass
            lb.insert("end", f"{ts}   ({f.stat().st_size} octets)")
        lb.selection_set(0)

        def go():
            i = lb.curselection()
            if not i:
                return
            src = files[i[0]]
            if not messagebox.askyesno(APP_NAME, "Remplacer le fichier actuel par cette sauvegarde ?\n"
                                                 "(l'état actuel est lui-même sauvegardé)", parent=win):
                return
            p = a.path()
            backup_file(a, p)
            p.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, p)
            win.destroy()
            self.done(a, "sauvegarde restaurée")

        bar = ttk.Frame(frm)
        bar.pack(fill="x")
        ttk.Button(bar, text="Fermer", command=win.destroy).pack(side="right")
        ttk.Button(bar, text="Restaurer", command=go).pack(side="right", padx=6)
        ttk.Button(bar, text="Ouvrir le dossier", command=lambda: open_path(d)).pack(side="left")

    def show_locations(self):
        lines = ["Fichier de configuration MCP de chaque agent (portée utilisateur) :", ""]
        for a in AGENTS:
            st = self.states[a.id]
            etat = ("fichier présent" if st.exists else "agent détecté, pas encore de fichier" if st.detected
                    else "non détecté")
            lines.append(f"{a.name}  —  {etat}")
            lines.append(f"    {a.path()}")
            lines.append(f"    clé : {'.'.join(a.root)}   ·   format : {a.fmt.upper()}")
            lines.append("")
        lines.append("Les configurations propres à un projet (.mcp.json, opencode.json du dossier, "
                     ".vscode/mcp.json…) ne sont pas gérées ici.")
        TextDialog(self, "Où sont les fichiers ?", "\n".join(lines), editable=False, width=110, height=30)


def main():
    App().mainloop()


if __name__ == "__main__":
    main()
