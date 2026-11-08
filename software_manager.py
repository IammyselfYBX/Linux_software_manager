#!/usr/bin/env python3
"""
Software Manager - read-only-first terminal inventory and cleanup assistant.

Designed for a Linux workstation. It inventories:
  * native package managers (pacman, apt/dpkg, dnf/yum/rpm, zypper, apk,
    emerge, xbps, nix)
  * application stores (Flatpak, Snap, Homebrew)
  * language ecosystems (pip/pipx, npm/yarn/pnpm, cargo, gem, go, conda,
    composer, luarocks)
  * AppImage files and their launchers
  * source/manual installations referenced by desktop entries or ~/.local/bin
  * orphaned user data/cache left after an application was removed

The TUI is dependency-free and uses only the Python standard library.
"""

from __future__ import annotations

import argparse
import configparser
import curses
import datetime as dt
import glob
import importlib.metadata as importlib_metadata
import json
import locale
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import textwrap
import time
import unicodedata
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

APP_NAME = "Software Manager"
APP_VERSION = "1.2.1"
STATE_DIR = Path.home() / ".local" / "state" / "software_manager"
DELETE_LOG = STATE_DIR / "delete.log"

# Names that are shared framework/system state rather than an individual app.
GENERIC_USERDATA = {
    "dconf", "gtk-2.0", "gtk-3.0", "gtk-4.0", "glib-2.0", "fontconfig",
    "pulse", "systemd", "menus", "goa-1.0", "ibus", "tracker", "tracker3",
    "thumbnails", "mesa_shader_cache", "gstreamer-1.0", "qtshadercache",
    "qtproject", "qt", "kde.org", "kdeglobals", "kiorc", "kconf_updaterc",
    "ksycoca5", "trolltech", "trolltech.conf", "samba", "node-gyp", "pip",
    "yarn", "npm", "go", "go-build", "matplotlib", "jedi", "pylint",
    "typescript", "debuginfod_client", "vim", "vimtex", "tags", "mc",
    "ranger", "mimeapps.list", "user-dirs.dirs", "user-dirs.locale",
    "trashrc", "xss-lock.log", "audioCache.kcache", "icon-cache.kcache",
    "session", "procps", "configstore", "cef_user_data", "ksycoca5_zh",
    "ksycoca5_en", "event-sound-cache", "simple-scan", "gnome-help",
    "gnome-calculator", "gnome-screenshot", "gnome-desktop-thumbnailer",
    "gnome-control-center", "gnome-session", "org.gnome.nautiluspreviewer",
    "libgweather", "geocode-glib", "folks", "evolution", "gegl-0.4", "babl",
    "glycin", "sushi", "tracker", "yelp", "obexd", "ibus", "pamac",
    "systemd", "menus", "filemanager-actions", "xchm.conf", "Trolltech.conf",
    "logs", "RecentDocuments", "icc", "desktop-directories", "applications",
    "recently-used.xbel", "user-places.xbel", "user-places.xbel.bak",
    "autostart", "tmux.conf", "qt5ct", "qt6ct", "qtproject.conf", "kitware",
}

GENERIC_PREFIXES = (
    ".fr-", "ksycoca5_", "event-sound-cache.", "qtshadercache-",
    "com.", "org.", "io.", "net.", "dev.",  # desktop IDs are matched when installed
)

STOP_ALIASES = {
    "appimage", "bin", "local", "usr", "linux", "x86", "x8664", "x64",
    "tool", "tools", "package", "application", "desktop", "source", "manual",
}

KNOWN_PLUGIN_DESCRIPTIONS = {
    "coc.nvim": "Vim/Neovim 的语言服务、代码补全、诊断和扩展框架。",
    "vim-plug": "Vim 插件管理器，负责安装、更新和清理 Plug 声明的插件。",
    "termdebug": "Vim 内置的 GDB 调试界面插件。",
    "editorconfig": "EditorConfig 的 Vim 支持插件，用于统一缩进和编码规则。",
    "nerdtree": "Vim 文件树浏览与文件管理插件。",
    "vim-airline": "Vim 状态栏和标签栏增强插件。",
    "fzf": "Vim/Neovim 模糊查找插件。",
    "fzf.vim": "基于 fzf 的 Vim 模糊搜索集成插件。",
    "ale": "Vim/Neovim 异步语法检查和修复插件。",
    "tagbar": "Vim 代码结构/标签浏览侧栏。",
    "undotree": "Vim 撤销历史可视化插件。",
    "vim-rainbow": "Vim 括号层级彩色高亮插件。",
    "asyncrun.vim": "Vim 异步运行命令和构建任务插件。",
    "vim-live-server": "Vim 中启动实时预览 Web 服务器的插件。",
    "iciba-translate": "Vim 划词/选中文本翻译插件。",
    "vim-snippets": "Vim 通用代码片段集合。",
    "ultisnips": "Vim 代码片段引擎。",
    "coc-snippets": "coc.nvim 的代码片段扩展。",
    "coc-ccls": "coc.nvim 的 C/C++/CUDA 语言服务扩展。",
    "coc-python": "coc.nvim 的 Python 语言支持扩展。",
    "coc-tsserver": "coc.nvim 的 JavaScript/TypeScript 语言服务扩展。",
    "coc-json": "coc.nvim 的 JSON 语言支持扩展。",
    "coc-texlab": "coc.nvim 的 LaTeX 语言服务扩展。",
    "coc-vimlsp": "coc.nvim 的 Vimscript 语言支持扩展。",
    "coc-marketplace": "在 coc.nvim 中浏览和安装扩展的客户端。",
    "vscodevim.vim": "VS Code 的 Vim 键位模拟扩展。",
    "ms-python.python": "VS Code 的 Python 语言支持扩展。",
    "github.copilot": "VS Code 的 GitHub Copilot AI 编程助手。",
    "james-yu.latex-workshop": "VS Code 的 LaTeX 编辑、编译和预览扩展。",
    "golang.go": "VS Code 的 Go 语言支持扩展。",
    "ms-vscode.cpptools": "VS Code 的 C/C++ IntelliSense、调试和代码浏览扩展。",
}

KNOWN_ALIASES = {
    "wps": {"kingsoft", "wpsoffice", "wps", "office6", "kingsoftoffice"},
    "tencentmeeting": {"wemeet", "wemeetapp", "tencentmeeting"},
    "vscode": {"code", "vscode", "visualstudiocode", "vscodelinuxx64"},
    "vscodium": {"vscodium", "codium"},
    "googlechrome": {"googlechrome", "chrome"},
    "microsoftedge": {"microsoftedge", "edge"},
    "motrix": {"motrix"},
    "zotero": {"zotero"},
    "rustdesk": {"rustdesk"},
    "qq": {"qq", "tencentqq"},
    "wechat": {"wechat", "weixin"},
    "uuremote": {"uuremote", "uuyc", "uuycwine"},
}


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

@dataclass
class Artifact:
    role: str
    path: str
    exists: bool = True
    size: Optional[int] = None
    note: str = ""


@dataclass
class Software:
    id: str
    name: str
    method: str
    method_title: str
    version: str = ""
    status: str = "installed"          # installed / unregistered / residual
    summary: str = ""
    package_name: str = ""
    package_group: str = ""
    install_reason: str = ""
    dependencies: list[str] = field(default_factory=list)
    optional_dependencies: list[str] = field(default_factory=list)
    required_by: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    artifacts: list[Artifact] = field(default_factory=list)
    data_artifacts: list[Artifact] = field(default_factory=list)
    cache_artifacts: list[Artifact] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def is_residual(self) -> bool:
        return self.status == "residual"

    @property
    def status_label(self) -> str:
        return {
            "installed": "已安装",
            "unregistered": "未发现入口",
            "residual": "疑似残留",
            "plugin_body_missing": "插件本体缺失",
            "plugin_not_installed": "仅配置声明",
            "plugin_host_missing": "宿主已卸载",
        }.get(self.status, self.status)

    @property
    def is_plugin(self) -> bool:
        return self.method in {"vim-plugin", "vscode-extension", "browser-extension", "browser-plugin"}

    @property
    def total_known_size(self) -> int:
        return sum(a.size or 0 for a in (*self.artifacts, *self.data_artifacts, *self.cache_artifacts))


@dataclass
class Method:
    id: str
    title: str
    description: str
    softwares: list[Software] = field(default_factory=list)
    residual: bool = False


@dataclass
class Inventory:
    generated_at: str
    hostname: str
    os_name: str
    home: str
    methods: list[Method]
    ignored_userdata: list[str] = field(default_factory=list)
    scan_seconds: float = 0.0

    def all_software(self) -> list[Software]:
        return [s for m in self.methods for s in m.softwares]

    def counts(self) -> dict[str, int]:
        return {m.id: len(m.softwares) for m in self.methods}


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def run(cmd: list[str], *, timeout: int = 30, env: Optional[dict[str, str]] = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            cmd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            env=env,
            errors="replace",
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))


def which(name: str) -> Optional[str]:
    return shutil.which(name)


def norm(value: str) -> str:
    value = value.casefold().strip()
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", value)


def words(value: str) -> set[str]:
    parts = re.split(r"[^A-Za-z0-9\u4e00-\u9fff]+", value.casefold())
    return {p for p in parts if len(p) >= 2}


def display_width(value: str) -> int:
    width = 0
    for ch in value:
        if unicodedata.combining(ch):
            continue
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def clip_display(value: str, width: int) -> str:
    if width <= 0:
        return ""
    out: list[str] = []
    used = 0
    for ch in value:
        w = 0 if unicodedata.combining(ch) else (2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1)
        if used + w > width:
            break
        out.append(ch)
        used += w
    return "".join(out)


def pad_display(value: str, width: int) -> str:
    clipped = clip_display(value, width)
    return clipped + " " * max(0, width - display_width(clipped))


def wrap_display(value: str, width: int) -> list[str]:
    """Wrap by terminal cells, including CJK wide characters."""
    if width <= 0:
        return [value]
    result: list[str] = []
    for paragraph in value.splitlines() or [""]:
        if paragraph == "":
            result.append("")
            continue
        current: list[str] = []
        used = 0
        for ch in paragraph:
            w = 0 if unicodedata.combining(ch) else (2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1)
            if used + w > width and current:
                result.append("".join(current).rstrip())
                current = []
                used = 0
            current.append(ch)
            used += w
        result.append("".join(current).rstrip())
    return result


def human_size(size: Optional[int]) -> str:
    if size is None:
        return "未知"
    if size < 1024:
        return f"{size} B"
    for unit in ("KiB", "MiB", "GiB", "TiB"):
        size /= 1024.0
        if size < 1024 or unit == "TiB":
            return f"{size:.1f} {unit}"
    return f"{size:.1f} TiB"


def path_size(path: str) -> Optional[int]:
    try:
        result = run(["du", "-sb", "--", path], timeout=20)
        if result.returncode == 0:
            return int(result.stdout.split("\t", 1)[0])
    except (ValueError, IndexError):
        pass
    try:
        p = Path(path)
        if p.is_symlink() or p.is_file():
            return p.lstat().st_size
        total = 0
        for root, dirs, files in os.walk(p, followlinks=False):
            for name in files:
                try:
                    total += (Path(root) / name).lstat().st_size
                except OSError:
                    pass
        return total
    except OSError:
        return None


def is_elf(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(4) == b"\x7fELF"
    except OSError:
        return False


def parse_pacman_qi(output: str) -> dict[str, dict[str, str]]:
    packages: dict[str, dict[str, str]] = {}
    current: dict[str, str] = {}
    last_key = ""
    for raw in output.splitlines():
        if not raw.strip():
            if current.get("Name"):
                packages[current["Name"]] = current
            current = {}
            last_key = ""
            continue
        if raw.startswith(" ") and last_key:
            current[last_key] = (current.get(last_key, "") + " " + raw.strip()).strip()
            continue
        if ":" in raw:
            key, value = raw.split(":", 1)
            key, value = key.strip(), value.strip()
            current[key] = value
            last_key = key
    if current.get("Name"):
        packages[current["Name"]] = current
    return packages


def split_list(value: str) -> list[str]:
    value = value.strip()
    if not value or value == "None":
        return []
    return [x.strip() for x in value.split() if x.strip() and x.strip() != "None"]


def desktop_entries(home: Path) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    roots = [home / ".local/share/applications", Path("/usr/share/applications")]
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.glob("*.desktop"):
            try:
                lines = path.read_text(errors="replace").splitlines()
            except OSError:
                continue
            data: dict[str, str] = {"path": str(path)}
            in_main = False
            for line in lines:
                stripped = line.strip()
                if stripped == "[Desktop Entry]":
                    in_main = True
                    continue
                if stripped.startswith("[") and stripped != "[Desktop Entry]":
                    in_main = False
                if in_main and "=" in line and not line.lstrip().startswith("#"):
                    key, value = line.split("=", 1)
                    data.setdefault(key.strip(), value.strip())
            if data.get("Exec"):
                entries.append(data)
    return entries


def exec_path(exec_line: str) -> Optional[str]:
    try:
        tokens = shlex.split(exec_line, posix=True)
    except ValueError:
        tokens = exec_line.split()
    for token in tokens:
        if token.startswith("/"):
            return os.path.expanduser(token)
    return None


def candidate_aliases(*values: str) -> set[str]:
    out: set[str] = set()
    for value in values:
        if not value:
            continue
        n = norm(value)
        if n:
            out.add(n)
        for word in words(value):
            if len(word) >= 3:
                out.add(norm(word))
        base = re.sub(r"(?i)(\.appimage|\.desktop|\.sh|\.bin)$", "", value)
        base = re.sub(r"[-_ ]v?\d+(?:\.\d+){1,4}.*$", "", base)
        if base:
            out.add(norm(base))
    expanded = set(out)
    for key, aliases in KNOWN_ALIASES.items():
        if any(key in a or a in key for a in out if len(a) >= 4):
            expanded.update(norm(x) for x in aliases)
    expanded.discard("")
    expanded.difference_update(STOP_ALIASES)
    return expanded


def alias_matches(entry_name: str, aliases: set[str]) -> bool:
    n = norm(entry_name)
    if not n:
        return False
    if n in aliases:
        return True
    for alias in aliases:
        if len(alias) >= 5 and (n.startswith(alias) or alias.startswith(n)):
            return True
        if len(n) >= 5 and len(alias) >= 5 and alias in n:
            return True
    return False


# ---------------------------------------------------------------------------
# Scanners
# ---------------------------------------------------------------------------

def collect_pacman(home: Path) -> list[Software]:
    if not which("pacman"):
        return []
    qi = run(["pacman", "-Qi"], timeout=40)
    if qi.returncode != 0:
        return []
    infos = parse_pacman_qi(qi.stdout)
    explicit = set(run(["pacman", "-Qqe"], timeout=20).stdout.split())
    foreign = set(run(["pacman", "-Qqm"], timeout=20).stdout.split())
    result: list[Software] = []
    for name, info in infos.items():
        group = "AUR/本地包" if name in foreign else "官方仓库"
        raw_reason = info.get("Install Reason", "")
        reason = (
            "用户主动安装" if raw_reason.casefold().startswith("explicit")
            else "作为其他软件的依赖安装" if "dependency" in raw_reason.casefold()
            else raw_reason
        )
        aliases = candidate_aliases(name)
        sw = Software(
            id=f"pacman:{name}",
            name=name,
            method="pacman",
            method_title="系统包管理器 · pacman",
            version=info.get("Version", ""),
            status="installed",
            summary=info.get("Description", ""),
            package_name=name,
            package_group=group,
            install_reason=reason,
            dependencies=split_list(info.get("Depends On", "")),
            optional_dependencies=split_list(info.get("Optional Deps", "")),
            required_by=split_list(info.get("Required By", "")),
            aliases=sorted(aliases),
            metadata={
                "manager": "pacman",
                "architecture": info.get("Architecture", ""),
                "url": info.get("URL", ""),
                "licenses": info.get("Licenses", ""),
                "installed_size": info.get("Installed Size", ""),
                "build_date": info.get("Build Date", ""),
                "install_date": info.get("Install Date", ""),
                "explicit": name in explicit,
                "foreign": name in foreign,
                "packager": info.get("Packager", ""),
                "validation": info.get("Validated By", ""),
            },
        )
        result.append(sw)
    return result


def parse_rpm_query(output: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for line in output.splitlines():
        parts = line.split("\t", 5)
        if len(parts) < 5:
            continue
        name, version, summary, install_epoch, size = parts[:5]
        try:
            installed = dt.datetime.fromtimestamp(int(install_epoch)).strftime("%Y-%m-%d %H:%M")
        except (ValueError, OSError):
            installed = install_epoch
        result.append({
            "name": name,
            "version": version,
            "summary": summary,
            "install_date": installed,
            "size": int(size) if size.isdigit() else 0,
        })
    return result


def collect_rpm(home: Path, manager: str) -> list[Software]:
    """Collect RPM database packages for dnf, yum or zypper."""
    if not which("rpm"):
        return []
    fmt = "%{NAME}\\t%{VERSION}-%{RELEASE}\\t%{SUMMARY}\\t%{INSTALLTIME}\\t%{SIZE}\\n"
    q = run(["rpm", "-qa", "--queryformat", fmt], timeout=45)
    if q.returncode != 0:
        return []
    explicit: set[str] = set()
    if manager in {"dnf", "yum"}:
        uq = run([manager, "repoquery", "--userinstalled", "--qf", "%{name}\\n"], timeout=20)
        if uq.returncode == 0:
            explicit = {line.strip() for line in uq.stdout.splitlines() if line.strip()}
    titles = {
        "dnf": "系统包管理器 · dnf/rpm",
        "yum": "系统包管理器 · yum/rpm",
        "zypper": "系统包管理器 · zypper/rpm",
    }
    groups = {"dnf": "Fedora/RHEL/dnf", "yum": "CentOS/RHEL/yum", "zypper": "openSUSE/zypper"}
    result: list[Software] = []
    for item in parse_rpm_query(q.stdout):
        name = item["name"]
        known_explicit = (name in explicit) if explicit else None
        result.append(Software(
            id=f"{manager}:{name}", name=name, method=manager,
            method_title=titles[manager], version=item["version"],
            status="installed", summary=item["summary"], package_name=name,
            package_group=groups[manager],
            install_reason=("手动安装" if known_explicit else ("作为依赖安装" if known_explicit is False else "")),
            aliases=sorted(candidate_aliases(name)),
            metadata={
                "manager": manager,
                "install_date": item["install_date"],
                "installed_size_bytes": item["size"],
                "explicit": known_explicit,
            },
        ))
    return result


def collect_apt(home: Path) -> list[Software]:
    if not which("dpkg-query"):
        return []
    fmt = "${Package}\\t${Version}\\t${Installed-Size}\\t${binary:Summary}\\n"
    q = run(["dpkg-query", "-W", "-f", fmt], timeout=45)
    if q.returncode != 0:
        return []
    manual_q = run(["apt-mark", "showmanual"], timeout=20)
    manual = {line.strip() for line in manual_q.stdout.splitlines() if line.strip()} if manual_q.returncode == 0 else set()
    result: list[Software] = []
    for line in q.stdout.splitlines():
        parts = line.split("\t", 3)
        if len(parts) < 3:
            continue
        name, version, kib = parts[:3]
        summary = parts[3] if len(parts) > 3 else ""
        known_explicit = (name in manual) if manual else None
        result.append(Software(
            id=f"apt:{name}", name=name, method="apt", method_title="系统包管理器 · apt/dpkg",
            version=version, summary=summary, package_name=name, package_group="Ubuntu/Debian/apt",
            install_reason=("手动安装" if known_explicit else ("作为依赖安装" if known_explicit is False else "")),
            aliases=sorted(candidate_aliases(name)),
            metadata={
                "manager": "apt",
                "installed_size_bytes": int(kib) * 1024 if kib.isdigit() else 0,
                "explicit": known_explicit,
            },
        ))
    return result


def split_package_version(value: str) -> tuple[str, str]:
    match = re.match(r"^(.+?)-(\d.*)$", value)
    return (match.group(1), match.group(2)) if match else (value, "")


def collect_apk(home: Path) -> list[Software]:
    if not which("apk"):
        return []
    q = run(["apk", "info", "-v"], timeout=30)
    if q.returncode != 0:
        return []
    result: list[Software] = []
    for raw in q.stdout.splitlines():
        name, version = split_package_version(raw.strip())
        if not name:
            continue
        result.append(Software(
            id=f"apk:{name}", name=name, method="apk", method_title="系统包管理器 · apk",
            version=version, summary="Alpine APK package", package_name=name,
            package_group="Alpine/apk", aliases=sorted(candidate_aliases(name)),
            metadata={"manager": "apk"},
        ))
    return result


def collect_emerge(home: Path) -> list[Software]:
    if not which("emerge") and not Path("/var/db/pkg").is_dir():
        return []
    world = ""
    try:
        world = Path("/var/lib/portage/world").read_text(errors="replace")
    except OSError:
        pass
    world_atoms = {line.strip() for line in world.splitlines() if line.strip() and not line.startswith("#")}
    result: list[Software] = []
    root = Path("/var/db/pkg")
    if not root.is_dir():
        return result
    for category in root.iterdir():
        if not category.is_dir():
            continue
        for package_dir in category.iterdir():
            if not package_dir.is_dir():
                continue
            name, version = split_package_version(package_dir.name)
            atom = f"{category.name}/{name}"
            explicit = atom in world_atoms or any(w.startswith(atom + "-") for w in world_atoms)
            result.append(Software(
                id=f"emerge:{atom}", name=atom, method="emerge",
                method_title="系统包管理器 · Portage/emerge", version=version,
                summary="Gentoo Portage package", package_name=atom,
                package_group=category.name,
                install_reason="world 集合（显式）" if explicit else "依赖/系统包",
                aliases=sorted(candidate_aliases(name, atom)),
                metadata={"manager": "emerge", "atom": atom, "explicit": explicit},
            ))
    return result


def collect_xbps(home: Path) -> list[Software]:
    if not which("xbps-query"):
        return []
    q = run(["xbps-query", "-l"], timeout=30)
    if q.returncode != 0:
        return []
    result: list[Software] = []
    for line in q.stdout.splitlines():
        match = re.match(r"^[a-z?]{2}\s+(\S+)\s*(.*)$", line)
        if not match:
            continue
        name, version = split_package_version(match.group(1))
        result.append(Software(
            id=f"xbps:{name}", name=name, method="xbps", method_title="系统包管理器 · XBPS",
            version=version, summary=match.group(2), package_name=name,
            package_group="Void Linux/XBPS", aliases=sorted(candidate_aliases(name)),
            metadata={"manager": "xbps"},
        ))
    return result


def collect_nix(home: Path) -> list[Software]:
    if not which("nix-env"):
        return []
    q = run(["nix-env", "-q", "--out-path"], timeout=30)
    if q.returncode != 0 or not q.stdout.strip():
        return []
    result: list[Software] = []
    seen: set[str] = set()
    for line in q.stdout.splitlines():
        parts = line.split()
        if not parts:
            continue
        name_version = parts[0]
        match = re.match(r"^(.+)-(\d+(?:\.\d+)+(?:[a-z0-9.-]*))$", name_version)
        name, version = (match.group(1), match.group(2)) if match else (name_version, "")
        if name in seen:
            continue
        seen.add(name)
        sw = Software(
            id=f"nix:{name}", name=name, method="nix", method_title="Nix profile",
            version=version, summary="Nix user profile package", package_name=name,
            package_group="Nix", aliases=sorted(candidate_aliases(name)),
            metadata={"manager": "nix", "store_path": parts[1] if len(parts) > 1 else ""},
        )
        if len(parts) > 1:
            sw.artifacts.append(Artifact("Nix store path", parts[1]))
        result.append(sw)
    return result


def collect_brew(home: Path) -> list[Software]:
    if not which("brew"):
        return []
    q = run(["brew", "list", "--versions"], timeout=40)
    if q.returncode != 0:
        return []
    leaves_q = run(["brew", "leaves"], timeout=20)
    leaves = {line.strip() for line in leaves_q.stdout.splitlines() if line.strip()} if leaves_q.returncode == 0 else set()
    prefix_q = run(["brew", "--prefix"], timeout=10)
    prefix = prefix_q.stdout.strip() if prefix_q.returncode == 0 else ""
    result: list[Software] = []
    for line in q.stdout.splitlines():
        parts = line.split()
        if not parts:
            continue
        name = parts[0]
        version = parts[-1] if len(parts) > 1 else ""
        sw = Software(
            id=f"brew:{name}", name=name, method="brew", method_title="Homebrew / Linuxbrew",
            version=version, summary="Homebrew formula", package_name=name,
            package_group="Homebrew", install_reason="显式安装" if name in leaves else "依赖",
            aliases=sorted(candidate_aliases(name)),
            metadata={"manager": "brew", "explicit": name in leaves if leaves else None},
        )
        if prefix:
            sw.artifacts.append(Artifact("Homebrew prefix", f"{prefix}/Cellar/{name}", note="Cellar"))
        result.append(sw)
    return result

def _read_small_text(path: Path, limit: int = 4000) -> str:
    try:
        return path.read_text(errors="replace")[:limit]
    except OSError:
        return ""


def _plugin_description(name: str, body: Optional[Path], fallback: str) -> str:
    key = norm(name)
    for known, description in KNOWN_PLUGIN_DESCRIPTIONS.items():
        known_key = norm(known)
        if key == known_key or key.endswith(known_key) or known_key.endswith(key):
            return description
    if body and body.is_dir():
        for readme_name in ("README.md", "README.rst", "README.txt", "readme.md"):
            readme = body / readme_name
            if not readme.is_file():
                continue
            text = _read_small_text(readme, 8000)
            lines = []
            for raw in text.splitlines():
                line = raw.strip()
                if not line or line.startswith(("#", "[!", "![", "<", "=", "-", "|")):
                    continue
                if line.lower().startswith(("badge", "build status", "license")):
                    continue
                lines.append(re.sub(r"[`*_]", "", line))
                if len(" ".join(lines)) >= 180:
                    break
            if lines:
                return " ".join(lines)[:300]
    return fallback


def _host_executable(home: Path, names: Iterable[str], extra_paths: Iterable[Path] = ()) -> bool:
    if any(which(name) for name in names):
        return True
    if any(path.is_file() and os.access(path, os.X_OK) for path in extra_paths):
        return True
    return False


def _vim_config_files(home: Path) -> list[Path]:
    candidates = [
        home / ".vimrc", home / ".vim/.vimrc", home / ".vim/vimrc",
        home / ".config/nvim/init.vim", home / ".config/nvim/init.lua",
    ]
    for root in (home / ".vim", home / ".config/nvim"):
        if root.is_dir():
            for pattern in ("plugin/*.vim", "vimconfigs/*.vim", "lua/**/*.lua", "lua/**/*.vim"):
                candidates.extend(root.glob(pattern))
    result: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        try:
            real = str(path.resolve())
        except OSError:
            continue
        if real not in seen and path.is_file():
            seen.add(real)
            result.append(path)
    return sorted(result)


def _vim_plugin_bodies(home: Path) -> list[dict[str, str]]:
    roots: list[tuple[Path, str, int]] = [
        (home / ".vim/plugged", "vim-plug", 1),
        (home / ".config/nvim/plugged", "vim-plug", 1),
        (home / ".local/share/nvim/lazy", "lazy.nvim", 1),
        (home / ".cache/dein/repos/github.com", "dein.vim", 2),
        (home / ".local/share/dein/repos/github.com", "dein.vim", 2),
    ]
    result: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(path: Path, manager: str) -> None:
        try:
            real = str(path.resolve())
        except OSError:
            return
        if real in seen or not path.is_dir():
            return
        seen.add(real)
        result.append({"path": real, "manager": manager})

    for root, manager, depth in roots:
        if not root.is_dir():
            continue
        for path in root.glob("*/" * max(0, depth - 1) + "*"):
            if path.is_dir():
                add(path, manager)
    for base in (home / ".vim/pack", home / ".local/share/nvim/site/pack"):
        if not base.is_dir():
            continue
        for package_root in base.glob("*/*/*"):
            if package_root.is_dir() and package_root.parent.name in {"start", "opt"}:
                add(package_root, f"Vim package ({package_root.parent.name})")
    system_pack_bases: list[Path] = []
    system_pack_bases.extend(Path(p) for p in glob.glob("/usr/share/vim/vim*/pack"))
    system_pack_bases.extend([Path("/usr/share/nvim/runtime/pack"), Path("/usr/local/share/nvim/runtime/pack")])
    for base in system_pack_bases:
        if not base.is_dir():
            continue
        for package_root in base.glob("*/*/*"):
            if package_root.is_dir() and package_root.parent.name in {"start", "opt"}:
                add(package_root, f"系统内置 Vim package ({package_root.parent.name})")
    return result


def _parse_vim_declarations(files: list[Path]) -> dict[str, dict[str, Any]]:
    declarations: dict[str, dict[str, Any]] = {}
    repo_pattern = re.compile(r"['\"]([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)['\"]")
    packadd_pattern = re.compile(r"\bpackadd\s+([A-Za-z0-9_.-]+)")
    for path in files:
        text = _read_small_text(path, 200_000)
        for raw in text.splitlines():
            stripped = raw.strip()
            if not stripped or stripped.startswith(('"', "#")):
                continue
            repo = ""
            manager = ""
            if re.search(r"\bPlug\b", stripped):
                match = repo_pattern.search(stripped)
                if match:
                    repo, manager = match.group(1), "vim-plug"
            elif re.search(r"\buse\b", stripped):
                match = repo_pattern.search(stripped)
                if match:
                    repo, manager = match.group(1), "lazy.nvim/packer/dein"
            if not repo:
                match = packadd_pattern.search(stripped)
                if match:
                    repo, manager = match.group(1), "Vim package (packadd)"
            if not repo:
                continue
            base = repo.rstrip("/").split("/")[-1]
            item = declarations.setdefault(repo, {
                "repo": repo, "name": base, "manager": manager,
                "configs": set(),
            })
            item["configs"].add(str(path))
            if manager and not item.get("manager"):
                item["manager"] = manager
    return declarations


def _body_matches_plugin(body: dict[str, str], repo: str, name: str) -> bool:
    body_name = norm(Path(body["path"]).name)
    candidates = {norm(name), norm(repo.split("/")[-1]), norm(repo.replace("/", "-"))}
    return body_name in candidates or any(
        len(candidate) >= 4 and (body_name.endswith(candidate) or candidate.endswith(body_name))
        for candidate in candidates
    )


def _coc_extensions(home: Path) -> list[str]:
    manifest = home / ".config/coc/extensions/package.json"
    try:
        data = json.loads(manifest.read_text(errors="replace"))
        deps = data.get("dependencies", {})
        return sorted(str(k) for k in deps) if isinstance(deps, dict) else []
    except (OSError, json.JSONDecodeError):
        return []


def collect_vim_plugins(home: Path) -> list[Software]:
    configs = _vim_config_files(home)
    declarations = _parse_vim_declarations(configs)
    bodies = _vim_plugin_bodies(home)
    vim_installed = _host_executable(home, ["vim"], [home / ".vim/main"])
    nvim_installed = _host_executable(home, ["nvim"], [home / ".local/bin/nvim"])
    hosts = [name for name, ok in (("Vim", vim_installed or (home / ".vim").is_dir()), ("Neovim", nvim_installed or (home / ".config/nvim").is_dir())) if ok]
    host_installed = bool(hosts)

    consumed_bodies: set[str] = set()
    result: list[Software] = []
    seen_names: set[str] = set()

    def build(repo: str, name: str, body: Optional[dict[str, str]], declared: Optional[dict[str, Any]]) -> None:
        manager = (declared or {}).get("manager") or (body or {}).get("manager", "")
        config_paths = sorted((declared or {}).get("configs", set()))
        data_paths: list[Path] = []
        metadata: dict[str, Any] = {
            "plugin_type": "Vim / Neovim 插件",
            "host_apps": hosts,
            "host_installed": host_installed,
            "plugin_manager": manager,
            "repo": repo,
            "body_installed": body is not None,
            "config_installed": bool(config_paths),
        }
        if norm(name) == norm("coc.nvim"):
            data_paths.append(home / ".config/coc")
            for cfg in (home / ".vim/coc-settings.json", home / ".vim/coc-settings.json.back", home / ".config/nvim/coc-settings.json"):
                if cfg.is_file() and str(cfg) not in config_paths:
                    config_paths.append(str(cfg))
            sub_extensions = _coc_extensions(home)
            if sub_extensions:
                metadata["sub_extensions"] = sub_extensions
        for config_path in config_paths:
            text = _read_small_text(Path(config_path), 200_000).casefold()
            if norm(name) in norm(text):
                continue
        data_existing = [p for p in data_paths if p.exists()]
        if not host_installed:
            status = "plugin_host_missing"
        elif body:
            status = "installed"
        elif data_existing:
            status = "plugin_body_missing"
        else:
            status = "plugin_not_installed"
        summary = _plugin_description(name, Path(body["path"]) if body else None, "Vim/Neovim 插件。")
        sw = Software(
            id=f"vim-plugin:{repo or name}", name=name, method="vim-plugin",
            method_title="Vim / Neovim 插件", status=status, summary=summary,
            package_name=name, package_group="Vim/Neovim",
            aliases=sorted(candidate_aliases(name, repo)), metadata=metadata,
        )
        if body:
            sw.artifacts.append(Artifact("插件本体", body["path"]))
        elif manager == "vim-plug":
            sw.artifacts.append(Artifact("预期插件位置", str(home / ".vim/plugged" / name), exists=False, note="未找到"))
        for config_path in config_paths:
            sw.artifacts.append(Artifact("插件配置", str(config_path)))
        for data_path in data_paths:
            if data_path.exists():
                sw.data_artifacts.append(Artifact("插件数据", str(data_path), size=path_size(str(data_path))))
        if status == "plugin_body_missing":
            sw.warnings.append("插件配置或数据仍然存在，但没有找到插件本体目录。不要直接按普通软件残留删除。")
        elif status == "plugin_host_missing":
            sw.warnings.append("宿主编辑器未检测到，这个插件目录可能是编辑器卸载后的残留。")
        result.append(sw)
        seen_names.add(norm(name))

    coc_subextensions = {norm(item) for item in _coc_extensions(home)}
    for repo, declared in declarations.items():
        name = declared["name"]
        if name.startswith("coc-") and norm(name) in coc_subextensions:
            # coc extensions are represented as children of coc.nvim, not as
            # independent Vim plugins.
            continue
        body = next((item for item in bodies if _body_matches_plugin(item, repo, name)), None)
        if name == "vim-plug":
            plug_file = home / ".vim/autoload/plug.vim"
            if plug_file.is_file():
                body = {"path": str(plug_file), "manager": "vim-plug"}
        if body:
            consumed_bodies.add(body["path"])
        build(repo, name, body, declared)

    for body in bodies:
        if body["path"] in consumed_bodies:
            continue
        name = Path(body["path"]).name
        build(name, name, body, None)
    return result


def _code_host_specs(home: Path) -> list[dict[str, Any]]:
    return [
        {
            "name": "Visual Studio Code", "extensions": home / ".vscode/extensions",
            "config": home / ".config/Code", "commands": ["code"],
            "extra": [home / ".local/bin/code", home / ".application/VSCode-linux-x64/code"],
        },
        {
            "name": "VSCodium / Code OSS", "extensions": home / ".vscode-oss/extensions",
            "config": home / ".config/VSCodium", "commands": ["codium", "code-oss"],
            "extra": [home / ".local/bin/codium"],
        },
        {
            "name": "VS Code Server", "extensions": home / ".vscode-server/extensions",
            "config": home / ".vscode-server", "commands": ["code-server"],
            "extra": [],
        },
        {
            "name": "Cursor", "extensions": home / ".cursor/extensions",
            "config": home / ".config/Cursor", "commands": ["cursor"],
            "extra": [],
        },
    ]


def collect_vscode_extensions(home: Path) -> list[Software]:
    result: list[Software] = []
    for spec in _code_host_specs(home):
        ext_root: Path = spec["extensions"]
        if not ext_root.is_dir():
            continue
        host_installed = _host_executable(home, spec["commands"], spec["extra"]) or (spec["config"] / "User").is_dir()
        installed_ids: set[str] = set()
        for ext_dir in [p for p in ext_root.iterdir() if p.is_dir()]:
            manifest = ext_dir / "package.json"
            if not manifest.is_file():
                continue
            try:
                data = json.loads(manifest.read_text(errors="replace"))
            except (OSError, json.JSONDecodeError):
                continue
            publisher = str(data.get("publisher", ""))
            package_name = str(data.get("name", ext_dir.name))
            ext_id = f"{publisher}.{package_name}" if publisher and "." not in package_name else package_name
            installed_ids.add(ext_id)
            display_name = str(data.get("displayName", "")).strip()
            if not display_name or display_name.startswith("%"):
                display_name = str(data.get("name", ext_dir.name))
            description = str(data.get("description", "")).strip()
            if not description or description.startswith("%"):
                description = KNOWN_PLUGIN_DESCRIPTIONS.get(ext_id.casefold(), "")
            if not description:
                description = "VS Code 扩展。"
            dependencies = [str(x) for x in data.get("extensionDependencies", []) if x]
            extensions_pack = [str(x) for x in data.get("extensionPack", []) if x]
            status = "installed" if host_installed else "plugin_host_missing"
            sw = Software(
                id=f"vscode-extension:{ext_id}", name=display_name,
                method="vscode-extension", method_title="VS Code 扩展",
                version=str(data.get("version", "")), status=status,
                summary=description, package_name=ext_id,
                package_group=str(spec["name"]), dependencies=dependencies,
                aliases=sorted(candidate_aliases(display_name, ext_id, package_name)),
                metadata={
                    "plugin_type": "VS Code 扩展",
                    "host_app": spec["name"], "host_installed": host_installed,
                    "extension_id": ext_id, "publisher": publisher,
                    "body_installed": True, "extension_pack": extensions_pack,
                },
            )
            sw.artifacts.append(Artifact("扩展本体", str(ext_dir)))
            data_path = spec["config"] / "User" / "globalStorage" / ext_id
            if data_path.exists():
                sw.data_artifacts.append(Artifact("扩展数据", str(data_path), size=path_size(str(data_path))))
            result.append(sw)

        global_root = spec["config"] / "User" / "globalStorage"
        if global_root.is_dir():
            for data_path in global_root.iterdir():
                if not data_path.is_dir() or data_path.name in installed_ids:
                    continue
                status = "plugin_body_missing" if host_installed else "plugin_host_missing"
                sw = Software(
                    id=f"vscode-extension-data:{spec['name']}:{data_path.name}",
                    name=f"扩展 {data_path.name}", method="vscode-extension",
                    method_title="VS Code 扩展", status=status,
                    summary="VS Code 扩展用户数据；未找到对应扩展本体",
                    package_name=data_path.name, package_group=str(spec["name"]),
                    aliases=sorted(candidate_aliases(data_path.name)),
                    metadata={
                        "plugin_type": "VS Code 扩展", "host_app": spec["name"],
                        "host_installed": host_installed, "extension_id": data_path.name,
                        "body_installed": False,
                    },
                )
                sw.data_artifacts.append(Artifact("扩展数据", str(data_path), size=path_size(str(data_path))))
                sw.warnings.append("只找到 VS Code 扩展数据，没有找到对应的扩展安装目录。")
                result.append(sw)
    return result


def _resolve_manifest_message(messages: dict[str, Any], value: str) -> str:
    match = re.match(r"^__MSG_(.+)__$", value or "", flags=re.IGNORECASE)
    if match:
        key = match.group(1)
        for candidate in (key, key.lower(), key.casefold()):
            item = messages.get(candidate)
            if isinstance(item, dict) and item.get("message"):
                return str(item["message"])
    return value


def _chrome_manifest_metadata(manifest_path: Path) -> dict[str, str]:
    try:
        data = json.loads(manifest_path.read_text(errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}
    messages: dict[str, Any] = {}
    locale_name = data.get("default_locale") or "en"
    messages_path = manifest_path.parent / "_locales" / locale_name / "messages.json"
    try:
        messages = json.loads(messages_path.read_text(errors="replace"))
    except (OSError, json.JSONDecodeError):
        pass
    name = _resolve_manifest_message(messages, str(data.get("name", "")))
    description = _resolve_manifest_message(messages, str(data.get("description", "")))
    return {
        "name": name or manifest_path.parent.parent.name,
        "description": description,
        "version": str(data.get("version", "")),
        "manifest_version": str(data.get("manifest_version", "")),
    }


def _browser_host_specs(home: Path) -> list[dict[str, Any]]:
    return [
        {"name": "Google Chrome", "root": home / ".config/google-chrome", "commands": ["google-chrome", "google-chrome-stable"]},
        {"name": "Chromium", "root": home / ".config/chromium", "commands": ["chromium", "chromium-browser"]},
        {"name": "Brave", "root": home / ".config/BraveSoftware/Brave-Browser", "commands": ["brave-browser", "brave"]},
        {"name": "Microsoft Edge", "root": home / ".config/microsoft-edge", "commands": ["microsoft-edge", "microsoft-edge-stable"]},
        {"name": "Vivaldi", "root": home / ".config/vivaldi", "commands": ["vivaldi", "vivaldi-stable"]},
    ]


def _host_available(commands: list[str]) -> bool:
    return any(which(name) for name in commands)


def collect_browser_extensions(home: Path) -> list[Software]:
    result: list[Software] = []
    for spec in _browser_host_specs(home):
        root: Path = spec["root"]
        if not root.is_dir():
            continue
        host_installed = _host_available(spec["commands"])
        installed_by_profile: dict[str, set[str]] = {}
        for profile in [p for p in root.iterdir() if p.is_dir() and (p / "Extensions").is_dir()]:
            installed_ids: set[str] = set()
            for manifest_path in profile.glob("Extensions/*/*/manifest.json"):
                parts = manifest_path.parts
                try:
                    ext_index = parts.index("Extensions")
                    ext_id = parts[ext_index + 1]
                except (ValueError, IndexError):
                    continue
                meta = _chrome_manifest_metadata(manifest_path)
                if not meta:
                    continue
                installed_ids.add(ext_id)
                data_path = profile / "Local Extension Settings" / ext_id
                status = "installed" if host_installed else "plugin_host_missing"
                sw = Software(
                    id=f"browser-extension:{spec['name']}:{profile.name}:{ext_id}",
                    name=meta["name"], method="browser-extension",
                    method_title="浏览器扩展", version=meta["version"],
                    status=status, summary=meta["description"] or "浏览器扩展",
                    package_name=ext_id, package_group=str(spec["name"]),
                    aliases=sorted(candidate_aliases(meta["name"], ext_id)),
                    metadata={
                        "plugin_type": "浏览器扩展", "host_app": spec["name"],
                        "host_installed": host_installed, "extension_id": ext_id,
                        "profile": profile.name, "manifest_version": meta["manifest_version"],
                        "body_path": str(manifest_path.parent),
                    },
                )
                sw.artifacts.append(Artifact("扩展本体", str(manifest_path.parent)))
                if data_path.exists():
                    sw.data_artifacts.append(Artifact("扩展数据", str(data_path), size=path_size(str(data_path))))
                result.append(sw)
            installed_by_profile[profile.name] = installed_ids

        # Local Extension Settings can outlive the extension body.
        for profile in [p for p in root.iterdir() if p.is_dir()]:
            settings_root = profile / "Local Extension Settings"
            if not settings_root.is_dir():
                continue
            installed = installed_by_profile.get(profile.name, set())
            for data_path in settings_root.iterdir():
                if not data_path.is_dir() or data_path.name in installed:
                    continue
                status = "plugin_body_missing" if host_installed else "plugin_host_missing"
                sw = Software(
                    id=f"browser-extension-data:{spec['name']}:{profile.name}:{data_path.name}",
                    name=f"扩展 {data_path.name}", method="browser-extension",
                    method_title="浏览器扩展", status=status,
                    summary="浏览器扩展数据；未找到对应扩展本体",
                    package_name=data_path.name, package_group=str(spec["name"]),
                    aliases=sorted(candidate_aliases(data_path.name)),
                    metadata={
                        "plugin_type": "浏览器扩展", "host_app": spec["name"],
                        "host_installed": host_installed, "extension_id": data_path.name,
                        "profile": profile.name, "body_installed": False,
                    },
                )
                sw.data_artifacts.append(Artifact("扩展数据", str(data_path), size=path_size(str(data_path))))
                sw.warnings.append("只找到扩展数据目录，没有找到对应的 manifest.json / 扩展本体。")
                result.append(sw)

    # Firefox extensions and their browser-extension-data.
    firefox_root = home / ".mozilla/firefox"
    firefox_installed = _host_executable(home, ["firefox"])
    if firefox_root.is_dir():
        for profile in [p for p in firefox_root.iterdir() if p.is_dir()]:
            ext_root = profile / "extensions"
            installed_ids: set[str] = set()
            if ext_root.is_dir():
                for entry in ext_root.iterdir():
                    if not (entry.is_file() or entry.is_dir()):
                        continue
                    ext_id = entry.stem if entry.suffix.lower() == ".xpi" else entry.name
                    meta: dict[str, str] = {}
                    try:
                        if entry.suffix.lower() == ".xpi" and zipfile.is_zipfile(entry):
                            with zipfile.ZipFile(entry) as archive:
                                if "manifest.json" in archive.namelist():
                                    meta = json.loads(archive.read("manifest.json").decode(errors="replace"))
                    except (OSError, zipfile.BadZipFile, json.JSONDecodeError):
                        pass
                    ext_id = str((meta.get("browser_specific_settings") or {}).get("gecko", {}).get("id", ext_id))
                    installed_ids.add(ext_id)
                    name = str(meta.get("name", "")).strip() or ext_id
                    description = str(meta.get("description", "")).strip()
                    status = "installed" if firefox_installed else "plugin_host_missing"
                    sw = Software(
                        id=f"browser-extension:Firefox:{profile.name}:{ext_id}",
                        name=name, method="browser-extension", method_title="浏览器扩展",
                        version=str(meta.get("version", "")), status=status,
                        summary=description or "Firefox 扩展", package_name=ext_id,
                        package_group="Firefox", aliases=sorted(candidate_aliases(name, ext_id)),
                        metadata={
                            "plugin_type": "浏览器扩展", "host_app": "Firefox",
                            "host_installed": firefox_installed, "extension_id": ext_id,
                            "profile": profile.name, "body_path": str(entry),
                        },
                    )
                    sw.artifacts.append(Artifact("扩展本体", str(entry)))
                    data_path = profile / "browser-extension-data" / ext_id
                    if data_path.exists():
                        sw.data_artifacts.append(Artifact("扩展数据", str(data_path), size=path_size(str(data_path))))
                    result.append(sw)
            data_root = profile / "browser-extension-data"
            if data_root.is_dir():
                for data_path in data_root.iterdir():
                    if not data_path.is_dir() or data_path.name in installed_ids:
                        continue
                    if data_path.name.endswith("@search.mozilla.org"):
                        continue
                    status = "plugin_body_missing" if firefox_installed else "plugin_host_missing"
                    sw = Software(
                        id=f"browser-extension-data:Firefox:{profile.name}:{data_path.name}",
                        name=f"Firefox 扩展 {data_path.name}", method="browser-extension",
                        method_title="浏览器扩展", status=status,
                        summary="Firefox 扩展数据；未找到对应扩展本体",
                        package_name=data_path.name, package_group="Firefox",
                        aliases=sorted(candidate_aliases(data_path.name)),
                        metadata={
                            "plugin_type": "浏览器扩展", "host_app": "Firefox",
                            "host_installed": firefox_installed, "extension_id": data_path.name,
                            "profile": profile.name, "body_installed": False,
                        },
                    )
                    sw.data_artifacts.append(Artifact("扩展数据", str(data_path), size=path_size(str(data_path))))
                    sw.warnings.append("只找到扩展数据目录，没有找到对应 XPI / 扩展本体。")
                    result.append(sw)
    return result


def collect_flash_plugins(home: Path) -> list[Software]:
    candidates: list[Path] = [
        home / ".application/flashplayer",
        home / ".mozilla/plugins/libflashplayer.so",
        Path("/usr/lib/mozilla/plugins/libflashplayer.so"),
        Path("/usr/lib/browser-plugins/libflashplayer.so"),
        Path("/usr/lib64/mozilla/plugins/libflashplayer.so"),
    ]
    patterns = [
        str(home / ".application/*/libflashplayer.so"),
        str(home / ".application/*/usr/lib*/mozilla/plugins/libflashplayer.so"),
        str(home / ".config/chromium/*/Pepper Data/Shockwave Flash/*/manifest.json"),
        str(home / ".config/google-chrome/*/Pepper Data/Shockwave Flash/*/manifest.json"),
    ]
    for pattern in patterns:
        candidates.extend(Path(p) for p in glob.glob(pattern))
    paths = []
    seen: set[str] = set()
    for path in candidates:
        try:
            real = str(path.resolve()) if path.exists() else str(path)
        except OSError:
            real = str(path)
        if path.exists() and real not in seen:
            seen.add(real)
            paths.append(path)
    if not paths:
        return []
    hosts = []
    for name, command in (("Firefox", "firefox"), ("Chromium", "chromium"), ("Google Chrome", "google-chrome")):
        if which(command):
            hosts.append(name)
    host_installed = bool(hosts)
    status = "installed" if host_installed else "plugin_host_missing"
    sw = Software(
        id="browser-plugin:adobe-flash", name="Adobe Flash Player",
        method="browser-plugin", method_title="浏览器插件 · Flash/NPAPI",
        status=status,
        summary="Adobe Flash Player NPAPI/PPAPI 浏览器插件，用于播放旧式 Flash 内容。Adobe 已于 2020 年停止支持。",
        package_name="flashplayer", package_group="浏览器插件 · NPAPI/PPAPI",
        aliases=sorted(candidate_aliases("flash", "flashplayer", "adobe flash player")),
        metadata={
            "plugin_type": "浏览器插件（NPAPI/PPAPI）",
            "host_apps": hosts,
            "host_installed": host_installed,
            "deprecated": True,
            "active_status": "历史兼容插件；现代浏览器已停止支持 Flash",
        },
    )
    added_dirs: set[str] = set()
    for path in paths:
        role = "浏览器插件本体" if path.suffix == ".so" or path.name == "flashplayer" else "Flash 配置/清单"
        if "Pepper Data" in str(path):
            role = "PPAPI Flash 清单"
        sw.artifacts.append(Artifact(role, str(path), size=path_size(str(path))))
        if path.name == "libflashplayer.so" and path.parent.name.startswith("flash_player"):
            install_dir = path.parent
            if str(install_dir) not in added_dirs:
                sw.artifacts.append(Artifact("解压安装目录", str(install_dir)))
                added_dirs.add(str(install_dir))
    sw.warnings.append("Flash 已停止支持。该文件可能仅供 Firefox/Chromium 的旧插件配置使用，不建议按普通源码目录删除。")
    return [sw]


def remove_source_plugin_overlaps(methods: list[Method]) -> None:
    plugin_paths: list[str] = []
    for method in methods:
        if method.id not in {"editor-plugins", "browser-plugins"}:
            continue
        for sw in method.softwares:
            for artifact in sw.artifacts + sw.data_artifacts:
                plugin_paths.append(os.path.realpath(artifact.path))
    for method in methods:
        if method.id != "source":
            continue
        kept: list[Software] = []
        for sw in method.softwares:
            root = os.path.realpath(str(sw.metadata.get("install_root", "")))
            if root and any(p == root or p.startswith(root + os.sep) or root.startswith(p + os.sep) for p in plugin_paths):
                continue
            kept.append(sw)
        method.softwares = kept


def _software_location(manager: str, package: str, version: str, *, title: str,
                       summary: str = "", deps: Optional[list[str]] = None,
                       location: str = "", group: str = "") -> Software:
    sw = Software(
        id=f"{manager}:{package}", name=package, method=manager, method_title=title,
        version=version, summary=summary or package, package_name=package,
        package_group=group or manager, dependencies=deps or [],
        install_reason="显式安装", aliases=sorted(candidate_aliases(package)),
        metadata={"manager": manager, "explicit": True},
    )
    if location:
        sw.artifacts.append(Artifact("安装位置", location))
    return sw


def collect_pip_user(home: Path) -> list[Software]:
    q = run([sys.executable, "-m", "pip", "list", "--user", "--format=json", "--disable-pip-version-check"], timeout=40)
    if q.returncode != 0:
        return []
    try:
        packages = json.loads(q.stdout)
    except json.JSONDecodeError:
        return []
    result: list[Software] = []
    for item in packages:
        name = item.get("name", "")
        if not name:
            continue
        deps: list[str] = []
        location = ""
        try:
            dist = importlib_metadata.distribution(name)
            deps = [req.split(";", 1)[0].strip() for req in (dist.requires or [])]
            location = str(dist.locate_file(""))
        except importlib_metadata.PackageNotFoundError:
            pass
        sw = _software_location(
            "pip", name, item.get("version", ""), title="Python · pip --user",
            summary="Python user package", deps=deps, location=location, group="Python/pip",
        )
        result.append(sw)
    return result


def collect_pipx(home: Path) -> list[Software]:
    if not which("pipx"):
        return []
    q = run(["pipx", "list", "--json"], timeout=30)
    if q.returncode != 0:
        return []
    try:
        data = json.loads(q.stdout)
    except json.JSONDecodeError:
        return []
    venvs = data.get("venvs", data if isinstance(data, dict) else {})
    result: list[Software] = []
    for name, info in venvs.items():
        metadata = info.get("metadata", {}) if isinstance(info, dict) else {}
        main = metadata.get("main_package", {}) if isinstance(metadata, dict) else {}
        version = main.get("package_version", "") if isinstance(main, dict) else ""
        location = info.get("venv_path", "") if isinstance(info, dict) else ""
        sw = _software_location(
            "pipx", name, version, title="Python · pipx",
            summary="Python isolated application", location=location, group="Python/pipx",
        )
        apps = info.get("apps", []) if isinstance(info, dict) else []
        for app in apps:
            if isinstance(app, str):
                sw.artifacts.append(Artifact("命令入口", str(home / ".local/bin" / app)))
        result.append(sw)
    return result


def _npm_json_packages(data: Any, manager: str, title: str) -> list[Software]:
    items: dict[str, Any] = {}
    if isinstance(data, dict):
        if isinstance(data.get("dependencies"), dict):
            items.update(data["dependencies"])
        else:
            items.update(data)
    elif isinstance(data, list):
        for entry in data:
            if isinstance(entry, dict) and isinstance(entry.get("dependencies"), dict):
                items.update(entry["dependencies"])
    result: list[Software] = []
    for name, info in items.items():
        version = info.get("version", "") if isinstance(info, dict) else ""
        result.append(_software_location(manager, name, version, title=title, group="Node.js"))
    return result


def _node_modules_packages(root: Path, manager: str, title: str) -> list[Software]:
    if not root.is_dir():
        return []
    package_dirs: list[Path] = []
    for entry in root.iterdir():
        if entry.name.startswith("@") and entry.is_dir():
            package_dirs.extend(p for p in entry.iterdir() if p.is_dir())
        elif entry.is_dir():
            package_dirs.append(entry)
    result: list[Software] = []
    for package_dir in sorted(package_dirs, key=lambda p: p.name.casefold()):
        manifest = package_dir / "package.json"
        if not manifest.is_file():
            continue
        try:
            data = json.loads(manifest.read_text(errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        name = data.get("name") or package_dir.name
        deps = sorted((data.get("dependencies") or {}).keys()) if isinstance(data.get("dependencies"), dict) else []
        sw = _software_location(
            manager, name, str(data.get("version", "")), title=title,
            summary=str(data.get("description", "")), deps=deps,
            location=str(package_dir), group="Node.js",
        )
        result.append(sw)
    return result


def collect_npm(home: Path) -> list[Software]:
    if not which("npm"):
        return []
    q = run(["npm", "root", "-g"], timeout=10)
    root = Path(q.stdout.strip()) if q.returncode == 0 and q.stdout.strip() else home / ".npm-global/lib/node_modules"
    return _node_modules_packages(root, "npm", "Node.js · npm global")


def collect_yarn(home: Path) -> list[Software]:
    if not which("yarn"):
        return []
    q = run(["yarn", "global", "dir"], timeout=10)
    root = Path(q.stdout.strip()) if q.returncode == 0 and q.stdout.strip() else home / ".config/yarn/global"
    modules = root / "node_modules" if (root / "node_modules").is_dir() else root
    return _node_modules_packages(modules, "yarn", "Node.js · Yarn global")


def collect_pnpm(home: Path) -> list[Software]:
    if not which("pnpm"):
        return []
    q = run(["pnpm", "root", "-g"], timeout=10)
    root = Path(q.stdout.strip()) if q.returncode == 0 and q.stdout.strip() else home / ".local/share/pnpm/global/5/node_modules"
    return _node_modules_packages(root, "pnpm", "Node.js · pnpm global")

def collect_cargo(home: Path) -> list[Software]:
    if not which("cargo"):
        return []
    q = run(["cargo", "install", "--list"], timeout=40)
    if q.returncode != 0:
        return []
    cargo_home = Path(os.environ.get("CARGO_HOME", home / ".cargo"))
    result: list[Software] = []
    for line in q.stdout.splitlines():
        match = re.match(r"^(\S+) v(\S+):$", line.strip())
        if not match:
            continue
        name, version = match.groups()
        sw = _software_location("cargo", name, version, title="Rust · cargo install", group="Rust")
        sw.artifacts.append(Artifact("Cargo 安装目录", str(cargo_home / "bin" / name)))
        result.append(sw)
    return result


def collect_gem(home: Path) -> list[Software]:
    if not which("gem"):
        return []
    q = run(["gem", "list", "--local"], timeout=40)
    if q.returncode != 0:
        return []
    result: list[Software] = []
    for line in q.stdout.splitlines():
        match = re.match(r"^(\S+) \(([^)]+)\)", line.strip())
        if not match:
            continue
        name, versions = match.groups()
        result.append(_software_location("gem", name, versions.split(",")[0].strip(), title="Ruby · gem", group="Ruby"))
    return result


def collect_go(home: Path) -> list[Software]:
    if not which("go"):
        return []
    q = run(["go", "env", "GOPATH"], timeout=20)
    if q.returncode != 0:
        return []
    gopath = Path(q.stdout.strip().splitlines()[0]) if q.stdout.strip() else home / "go"
    bin_dir = gopath / "bin"
    if not bin_dir.is_dir():
        return []
    result: list[Software] = []
    for p in sorted(bin_dir.iterdir()):
        if not (p.is_file() or p.is_symlink()) or not os.access(p, os.X_OK):
            continue
        sw = _software_location("go", p.name, "", title="Go · go install", group="Go")
        sw.artifacts.append(Artifact("Go 二进制", str(p)))
        result.append(sw)
    return result


def collect_conda(home: Path) -> list[Software]:
    conda = which("conda") or which("mamba")
    if not conda:
        return []
    q = run([conda, "list", "--json"], timeout=60)
    if q.returncode != 0 or not q.stdout.strip():
        return []
    try:
        packages = json.loads(q.stdout)
    except json.JSONDecodeError:
        return []
    info = run([conda, "info", "--json"], timeout=30)
    env_prefix = ""
    try:
        info_data = json.loads(info.stdout) if info.returncode == 0 else {}
        env_prefix = str(info_data.get("active_prefix") or info_data.get("root_prefix") or "")
    except json.JSONDecodeError:
        pass
    result: list[Software] = []
    for item in packages:
        name = item.get("name", "")
        if not name:
            continue
        sw = _software_location(
            "conda", name, item.get("version", ""), title="Conda · 当前环境",
            summary=f"{item.get('channel', '')} {item.get('build', '')}".strip(),
            location=env_prefix, group="Conda",
        )
        sw.metadata.update({"build": item.get("build", ""), "channel": item.get("channel", "")})
        result.append(sw)
    return result


def collect_composer(home: Path) -> list[Software]:
    if not which("composer"):
        return []
    q = run(["composer", "global", "show", "--format=json"], timeout=40)
    if q.returncode != 0:
        return []
    try:
        data = json.loads(q.stdout)
    except json.JSONDecodeError:
        return []
    home_q = run(["composer", "global", "config", "home"], timeout=15)
    location = home_q.stdout.strip() if home_q.returncode == 0 else str(home / ".composer")
    result: list[Software] = []
    for item in data.get("installed", []):
        name = item.get("name", "")
        if name:
            result.append(_software_location(
                "composer", name, item.get("version", ""), title="PHP · Composer global",
                summary=item.get("description", ""), location=location, group="PHP/Composer",
            ))
    return result


def collect_luarocks(home: Path) -> list[Software]:
    if not which("luarocks"):
        return []
    q = run(["luarocks", "list", "--porcelain"], timeout=40)
    if q.returncode != 0:
        return []
    result: list[Software] = []
    for line in q.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            result.append(_software_location(
                "luarocks", parts[0], parts[1], title="Lua · LuaRocks",
                location=parts[2] if len(parts) > 2 else "", group="Lua/LuaRocks",
            ))
    return result


def collect_flatpak(home: Path) -> list[Software]:
    if not which("flatpak"):
        return []
    q = run(["flatpak", "list", "--app", "--columns=application,name,version,origin,installation"], timeout=30)
    if q.returncode != 0:
        return []
    result: list[Software] = []
    for line in q.stdout.splitlines():
        parts = [p.strip() for p in line.split("\t")]
        if len(parts) < 2 or not parts[0]:
            continue
        app_id, name = parts[0], parts[1]
        version = parts[2] if len(parts) > 2 else ""
        origin = parts[3] if len(parts) > 3 else ""
        installation = parts[4] if len(parts) > 4 else ""
        var_path = home / ".var/app" / app_id
        sw = Software(
            id=f"flatpak:{app_id}", name=name or app_id, method="flatpak",
            method_title="Flatpak", version=version, summary=app_id,
            package_name=app_id, package_group=origin or "flatpak",
            aliases=sorted(candidate_aliases(name, app_id)),
            metadata={"manager": "flatpak", "application_id": app_id, "installation": installation},
        )
        if var_path.exists():
            sw.data_artifacts.append(Artifact("Flatpak 应用数据", str(var_path)))
        result.append(sw)
    return result


def collect_snap(home: Path) -> list[Software]:
    if not which("snap"):
        return []
    q = run(["snap", "list", "--unicode=never"], timeout=30)
    if q.returncode != 0 or not q.stdout.strip():
        return []
    result: list[Software] = []
    for line in q.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 2:
            continue
        name, version = parts[0], parts[1]
        sw = Software(
            id=f"snap:{name}", name=name, method="snap", method_title="Snap",
            version=version, summary="Snap package", package_name=name, package_group="snap",
            aliases=sorted(candidate_aliases(name)),
            metadata={"manager": "snap", "revision": parts[-1] if len(parts) > 2 else ""},
        )
        snap_path = home / "snap" / name
        if snap_path.exists():
            sw.data_artifacts.append(Artifact("Snap 用户数据", str(snap_path)))
        result.append(sw)
    return result


def find_appimages(home: Path) -> list[str]:
    paths: set[str] = set()
    # One bounded whole-home scan is faster than several recursive rglob()
    # walks through large source/build trees. Depth 8 covers normal portable
    # install locations while avoiding pathological project directories.
    q = run([
        "find", str(home), "-xdev", "-maxdepth", "8", "-type", "f", "-iname", "*.AppImage",
        "-not", "-path", "*/.cache/*", "-not", "-path", "*/.Trash/*",
        "-not", "-path", "*/.local/share/Trash/*",
        "-not", "-path", "*/node_modules/*", "-not", "-path", "*/.git/*",
    ], timeout=25)
    for line in q.stdout.splitlines():
        if line.strip():
            paths.add(os.path.realpath(line.strip()))
    if Path("/opt").is_dir():
        q = run(["find", "/opt", "-xdev", "-maxdepth", "6", "-type", "f", "-iname", "*.AppImage"], timeout=10)
        for line in q.stdout.splitlines():
            if line.strip():
                paths.add(os.path.realpath(line.strip()))
    return sorted(paths, key=lambda x: x.casefold())

def collect_local_bin(home: Path) -> list[dict[str, str]]:
    root = home / ".local/bin"
    result: list[dict[str, str]] = []
    if not root.is_dir():
        return result
    for p in root.iterdir():
        if p.is_symlink():
            try:
                target = os.path.realpath(p)
            except OSError:
                continue
            result.append({"launcher": str(p), "target": target})
        elif p.is_file() and os.access(p, os.X_OK):
            result.append({"launcher": str(p), "target": str(p)})
    return result


def appimage_name(path: str, desktop_names: dict[str, str]) -> str:
    real = os.path.realpath(path)
    if real in desktop_names:
        desktop_name = desktop_names[real]
        if re.match(r"(?i)^WPS (Writer|PDF|Presentation|Spreadsheets)$", desktop_name):
            return "WPS Office"
        return desktop_name
    base = Path(path).name
    base = re.sub(r"(?i)\.appimage$", "", base)
    base = re.sub(r"[-_ ]v?\d+(?:\.\d+){1,4}.*$", "", base)
    return base or Path(path).stem


def collect_appimages(home: Path, desktop: list[dict[str, str]], local_bins: list[dict[str, str]]) -> list[Software]:
    appimages = find_appimages(home)
    desktop_names: dict[str, str] = {}
    desktop_refs: dict[str, list[str]] = {}
    for entry in desktop:
        ex = exec_path(entry.get("Exec", ""))
        if ex and ex.lower().endswith(".appimage"):
            real = os.path.realpath(ex)
            desktop_names.setdefault(real, entry.get("Name", Path(real).stem))
            desktop_refs.setdefault(real, []).append(entry["path"])
    local_refs: dict[str, list[str]] = {}
    for item in local_bins:
        target = item["target"]
        if target.lower().endswith(".appimage"):
            real = os.path.realpath(target)
            local_refs.setdefault(real, []).append(item["launcher"])

    # A desktop entry may launch a shell wrapper which starts an AppImage. If
    # the wrapper mentions an AppImage basename, treat that file as registered.
    script_refs: dict[str, list[str]] = {}
    for entry in desktop:
        if not entry["path"].startswith(str(home / ".local/share/applications")):
            continue
        ex = exec_path(entry.get("Exec", ""))
        if not ex or ex.lower().endswith(".appimage") or not os.path.isfile(ex) or is_elf(ex):
            continue
        try:
            if os.path.getsize(ex) > 2_000_000:
                continue
            text = Path(ex).read_bytes()[:500_000].decode(errors="replace")
        except OSError:
            continue
        for token in re.findall(r"[^\s\"']+\.AppImage", text, flags=re.IGNORECASE):
            script_refs.setdefault(Path(token).name.casefold(), []).append(entry["path"])

    result: list[Software] = []
    for path in appimages:
        real = os.path.realpath(path)
        name = appimage_name(real, desktop_names)
        refs = desktop_refs.get(real, []) + local_refs.get(real, [])
        refs += script_refs.get(Path(real).name.casefold(), [])
        refs = list(dict.fromkeys(refs))
        trash = "/Trash/" in real or "/.Trash/" in real
        status = "residual" if trash else ("installed" if refs else "unregistered")
        version_match = re.search(r"(?i)(?:^|[-_ ])v?(\d+(?:\.\d+){1,4})", Path(real).name)
        aliases = candidate_aliases(name, Path(real).name)
        sw = Software(
            id=f"appimage:{real}", name=name, method="appimage",
            method_title="AppImage", version=version_match.group(1) if version_match else "",
            status=status,
            summary="已由桌面入口或命令链接登记" if status == "installed" else (
                "文件位于回收站" if trash else "存在 AppImage 文件，但没有发现启动入口"
            ),
            aliases=sorted(aliases),
            metadata={"appimage_path": real, "registered": status == "installed", "trash": trash},
        )
        sw.artifacts.append(Artifact("AppImage 本体", real, size=path_size(real), note="可执行文件" if os.access(real, os.X_OK) else "不可执行"))
        for ref in refs:
            sw.artifacts.append(Artifact("启动入口", ref, note="桌面入口" if ref.endswith(".desktop") else "命令链接"))
        result.append(sw)
    return result


def application_ini_info(root: Path) -> dict[str, str]:
    for candidate in (root / "application.ini", root / "app/application.ini"):
        if not candidate.is_file():
            continue
        info: dict[str, str] = {}
        section = ""
        for raw in _read_small_text(candidate, 20_000).splitlines():
            line = raw.strip()
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1]
            elif "=" in line and not line.startswith(("#", ";")):
                key, value = line.split("=", 1)
                info[f"{section}.{key}" if section else key] = value.strip()
        return info
    return {}


def manual_update_metadata(root: Path, app_info: dict[str, str]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    update_url = app_info.get("AppUpdate.URL", "")
    updates_dir = root / "updates"
    electron_update = next((p for p in (root / "resources/app-update.yml", root / "app-update.yml", root / "resources/app-update.yaml") if p.is_file()), None)
    mar_files = sorted(glob.glob(str(root / "**/*.mar"), recursive=True))[:20]
    if update_url or updates_dir.is_dir() or mar_files:
        metadata["self_updating"] = True
        metadata["update_system"] = "Mozilla MAR / 应用内完整更新包"
        if updates_dir.is_dir():
            metadata["update_directory"] = str(updates_dir)
        if mar_files:
            metadata["mar_files"] = mar_files
    elif electron_update:
        metadata["self_updating"] = True
        metadata["update_system"] = "Electron autoUpdater"
        metadata["update_config"] = str(electron_update)
    return metadata


def collect_source_manual(home: Path, desktop: list[dict[str, str]], local_bins: list[dict[str, str]]) -> list[Software]:
    groups: dict[str, dict[str, Any]] = {}

    def add(
        key_path: str,
        name: str,
        *,
        artifact: Optional[Artifact] = None,
        alias_source: str = "",
        status: str = "installed",
        summary: str = "",
    ) -> None:
        if not key_path:
            return
        key = os.path.realpath(key_path)
        if key not in groups:
            groups[key] = {
                "name": name, "artifacts": [], "aliases": set(),
                "status": status, "summary": summary,
            }
        group = groups[key]
        if status == "unregistered" and group.get("status") == "installed" and not group["artifacts"]:
            group["status"] = status
        if artifact:
            group["artifacts"].append(artifact)
        # Never use full path components as aliases: /usr and /bin would match
        # unrelated user data. Only the application name and target basename do.
        group["aliases"].update(candidate_aliases(name, Path(key_path).name, alias_source))
        if not group.get("summary") and summary:
            group["summary"] = summary

    def root_for(path: str) -> Optional[str]:
        real = os.path.realpath(path)
        home_real = os.path.realpath(home)
        candidates = [
            (home_real + "/.application/", 1),
            (home_real + "/.local/opt/", 1),
            (home_real + "/.local/apps/", 1),
            (home_real + "/.local/src/", 1),
            (home_real + "/opt/", 1),
            (home_real + "/Qt", 1),
            ("/opt/", 1),
            ("/usr/local/src/", 1),
        ]
        for prefix, depth in candidates:
            if real.startswith(prefix):
                rel = real[len(prefix):].split(os.sep)
                if not rel or not rel[0]:
                    return None
                return prefix + os.sep.join(rel[:max(1, min(depth, len(rel)))])
        if real.startswith(home_real + "/") and "/bin/" in real:
            # Only project binaries are useful. A regular script directly in
            # ~/.local/bin belongs to the aggregate user CLI group below.
            root = real.split("/bin/", 1)[0]
            if root in {home_real, home_real + "/.local"}:
                return None
            return root
        if real.startswith(home_real + "/") and os.path.isfile(real):
            parent = str(Path(real).parent)
            if parent in {home_real, home_real + "/.local/bin"}:
                return None
            return parent
        return None

    # 1) ~/.local/bin symlinks and regular user scripts.
    regular_user_cli: list[dict[str, str]] = []
    for item in local_bins:
        target = item["target"]
        if Path(item["launcher"]).is_symlink():
            if target.lower().endswith(".appimage"):
                continue
            root = root_for(target)
            if not root:
                continue
            name = Path(root).name
            add(
                root, name,
                artifact=Artifact("命令链接", item["launcher"], note=f"-> {target}"),
                alias_source=Path(target).name,
            )
        else:
            regular_user_cli.append(item)

    if regular_user_cli:
        key = str(home / ".local/bin")
        aliases: set[str] = set()
        group = groups.setdefault(key, {
            "name": "用户级 CLI 工具", "artifacts": [], "aliases": set(),
            "status": "installed",
            "summary": "~/.local/bin 中直接安装的命令（常见于 pip、脚本或上游安装包）",
        })
        for item in regular_user_cli:
            stem = Path(item["launcher"]).name
            group["artifacts"].append(Artifact("用户命令", item["launcher"]))
            aliases.update(candidate_aliases(stem))
            if stem.startswith("jupyter"):
                aliases.update(candidate_aliases("jupyter"))
        group["aliases"].update(aliases)

    # 2) User desktop entries with an absolute executable path.
    for entry in desktop:
        if not entry["path"].startswith(str(home / ".local/share/applications")):
            continue
        ex = exec_path(entry.get("Exec", ""))
        if not ex:
            try:
                token = shlex.split(entry.get("Exec", ""))[0]
            except (ValueError, IndexError):
                continue
            if "/" not in token:
                continue
            ex = os.path.expanduser(token)
        if ex.lower().endswith(".appimage"):
            continue
        root = root_for(ex)
        if not root:
            continue
        name = entry.get("Name") or Path(root).name
        add(
            root, name,
            artifact=Artifact("桌面入口", entry["path"], note=f"Exec={entry.get('Exec', '')}"),
            alias_source=Path(ex).name,
            summary=entry.get("Comment", ""),
        )

    # 3) /opt and /usr/local: find non-package-manager programs.
    package_owned: set[str] = set()
    if which("pacman"):
        probe: list[str] = []
        if Path("/opt").is_dir():
            probe.extend(str(p) for p in Path("/opt").iterdir() if p.is_dir())
        if Path("/usr/local/bin").is_dir():
            probe.extend(str(p) for p in Path("/usr/local/bin").iterdir() if p.is_file() or p.is_symlink())
        if probe:
            q = run(["pacman", "-Qo", *probe], timeout=20)
            for line in q.stdout.splitlines():
                if " is owned by " in line:
                    package_owned.add(line.split(" is owned by ", 1)[0])

    if Path("/opt").is_dir():
        for p in Path("/opt").iterdir():
            if not p.is_dir() or str(p) in package_owned:
                continue
            add(
                str(p), p.name,
                artifact=Artifact("安装目录", str(p), note="非包管理器目录"),
                alias_source=p.name,
            )

    local_usr_bin_regular: list[Path] = []
    if Path("/usr/local/bin").is_dir():
        for p in Path("/usr/local/bin").iterdir():
            if str(p) in package_owned or not (p.is_file() or p.is_symlink()):
                continue
            if p.is_symlink():
                target = os.path.realpath(p)
                root = root_for(target)
                if root:
                    add(
                        root, Path(root).name,
                        artifact=Artifact("命令入口", str(p), note=f"-> {target}"),
                        alias_source=Path(target).name,
                    )
                else:
                    local_usr_bin_regular.append(p)
            elif os.access(p, os.X_OK):
                local_usr_bin_regular.append(p)
    if local_usr_bin_regular:
        key = "/usr/local/bin"
        group = groups.setdefault(key, {
            "name": "本地编译工具", "artifacts": [], "aliases": set(),
            "status": "installed",
            "summary": "/usr/local/bin 中未登记到 pacman 的可执行文件",
        })
        for p in local_usr_bin_regular:
            group["artifacts"].append(Artifact("本地命令", str(p)))
            group["aliases"].update(candidate_aliases(p.name))

    # 4) Top-level source/build/manual application directories without a launcher entry.
    app_roots = [
        (home / ".application", "源码 / 解压目录"),
        (home / ".local/opt", "手动安装目录"),
        (home / "opt", "手动安装目录"),
    ]
    for application_root, role in app_roots:
        if not application_root.is_dir():
            continue
        known_keys = list(groups)
        for p in application_root.iterdir():
            if not p.is_dir() or p.name == "software_manager":
                continue
            if any(os.path.realpath(p) == k or os.path.realpath(p).startswith(k + os.sep) or k.startswith(os.path.realpath(p) + os.sep) for k in known_keys):
                continue
            # AppImage files inside a source tree are already represented in
            # the AppImage page; don't duplicate the containing directory.
            try:
                if any(child.is_file() and child.name.lower().endswith(".appimage") for child in p.rglob("*")):
                    continue
            except OSError:
                pass
            add(
                str(p), p.name,
                artifact=Artifact(role, str(p), note="未发现桌面入口或命令链接"),
                alias_source=p.name,
                status="unregistered",
                summary="存在源码或手动安装目录，但没有发现启动入口",
            )

    result: list[Software] = []
    for key, group in groups.items():
        if not group["artifacts"] or not os.path.exists(key):
            continue
        name = group["name"]
        root_path = Path(key)
        app_info = application_ini_info(root_path)
        version = app_info.get("App.Version", "")
        if not version:
            for marker in (root_path / "version", root_path / "VERSION", root_path / "package.json"):
                if marker.is_file():
                    try:
                        text = marker.read_text(errors="replace")[:2000]
                        m = re.search(r"(?im)(?:version[\"']?\s*[:=]\s*[\"']?|^v?)(\d+(?:\.\d+){1,4})", text)
                        if m:
                            version = m.group(1)
                            break
                    except OSError:
                        pass
        metadata: dict[str, Any] = {
            "install_root": key,
            "explicit": group.get("status", "installed") == "installed",
            "application_id": app_info.get("App.ID", ""),
            "vendor": app_info.get("App.Vendor", ""),
        }
        metadata.update(manual_update_metadata(root_path, app_info))
        sw = Software(
            id=f"manual:{key}", name=app_info.get("App.Name", name), method="source",
            method_title="源码 / 手动安装 / 自更新应用", version=version,
            status=group.get("status", "installed"),
            summary=group.get("summary") or key,
            aliases=sorted(group["aliases"]),
            metadata=metadata,
        )
        # The install root is the real program body for a source/manual app.
        # Aggregate tool directories are containers and must not be removed as
        # one unit.
        if key not in {str(home / ".local/bin"), "/usr/local/bin"}:
            sw.artifacts.append(Artifact("安装根目录", key))
        update_directory = metadata.get("update_directory")
        if update_directory and os.path.isdir(update_directory):
            sw.artifacts.append(Artifact("自更新缓存目录", update_directory, size=path_size(update_directory)))
        seen = set()
        for artifact in group["artifacts"]:
            token = (artifact.role, artifact.path)
            if token not in seen:
                sw.artifacts.append(artifact)
                seen.add(token)
        result.append(sw)
    return result

def collect_userdata_roots(home: Path) -> list[tuple[str, Path]]:
    roots = [
        ("用户配置", home / ".config"),
        ("用户数据", home / ".local/share"),
        ("缓存", home / ".cache"),
        ("用户状态", home / ".local/state"),
        ("Flatpak 数据", home / ".var/app"),
        ("Snap 数据", home / "snap"),
    ]
    result: list[tuple[str, Path]] = []
    for role, root in roots:
        if root.is_dir():
            try:
                for child in root.iterdir():
                    if child.name in {"software_manager", "Trash"}:
                        continue
                    result.append((role, child))
            except OSError:
                continue
    return result


def enrich_package_aliases_from_desktops(inventory: Inventory, desktop: list[dict[str, str]]) -> None:
    """Map desktop names back to the package that owns the .desktop file.

    This lets package names such as rpi-imager claim user data named
    "Raspberry Pi". It is intentionally one pacman -Qo batch, not one call per
    desktop file.
    """
    if not which("pacman"):
        return
    system_desktops = [e["path"] for e in desktop if e["path"].startswith("/usr/share/applications/")]
    if not system_desktops:
        return
    q = run(["pacman", "-Qo", *system_desktops], timeout=30)
    owners: dict[str, str] = {}
    for line in q.stdout.splitlines():
        match = re.match(r"^(.+) is owned by (\S+) ", line)
        if match:
            owners[match.group(1)] = match.group(2)
    by_package = {s.package_name: s for s in inventory.all_software() if s.metadata.get("manager") == "pacman"}
    for entry in desktop:
        package = owners.get(entry["path"])
        if not package or package not in by_package:
            continue
        name = entry.get("Name", "")
        ex = exec_path(entry.get("Exec", ""))
        aliases = candidate_aliases(name, Path(ex).name if ex else "")
        by_package[package].aliases = sorted(set(by_package[package].aliases) | aliases)

    # Distribution-neutral fallback: match a desktop Exec basename to a package
    # name. This helps apt/dnf/yum/zypper systems without hard-coding each
    # distribution's ownership query format.
    for entry in desktop:
        ex = exec_path(entry.get("Exec", ""))
        ex_name = norm(Path(ex).stem) if ex else ""
        if len(ex_name) < 4:
            continue
        aliases = candidate_aliases(entry.get("Name", ""), Path(ex).name if ex else "")
        for sw in inventory.all_software():
            package_name = norm(sw.package_name or sw.name)
            if len(package_name) < 4:
                continue
            if package_name == ex_name or package_name in ex_name or ex_name in package_name:
                sw.aliases = sorted(set(sw.aliases) | aliases)


def attach_userdata(inventory: Inventory, home: Path) -> None:
    roots = collect_userdata_roots(home)
    softwares = inventory.all_software()
    # Prefer the visible application over a dependency/font helper that only
    # happens to share a keyword (e.g. ttf-wps-fonts vs WPS Office).
    method_priority = {
        "appimage": 0, "source": 1, "flatpak": 1, "snap": 1, "brew": 1,
        "pacman": 2, "apt": 2, "dnf": 2, "yum": 2, "zypper": 2,
        "apk": 2, "emerge": 2, "xbps": 2, "nix": 2,
        "pip": 3, "pipx": 3, "npm": 3, "yarn": 3, "pnpm": 3,
        "cargo": 3, "gem": 3, "go": 3, "conda": 3,
        "composer": 3, "luarocks": 3,
    }
    softwares = sorted(
        softwares,
        key=lambda s: (
            method_priority.get(s.method, 5),
            0 if s.metadata.get("explicit") else 1,
            s.name.casefold(),
        ),
    )
    claimed: set[str] = {
        artifact.path
        for software in softwares
        for artifact in (*software.artifacts, *software.data_artifacts, *software.cache_artifacts)
    }
    for sw in softwares:
        if sw.is_plugin:
            # Plugin scanners already associate exact data directories.
            # Broad alias matching would let generic words such as "project"
            # claim unrelated QtProject data.
            continue
        aliases = set(sw.aliases) | candidate_aliases(sw.name)
        for role, path in roots:
            if str(path) in claimed:
                continue
            if alias_matches(path.name, aliases):
                artifact = Artifact(role, str(path), size=path_size(str(path)))
                if role == "缓存":
                    sw.cache_artifacts.append(artifact)
                else:
                    sw.data_artifacts.append(artifact)
                claimed.add(str(path))

    # Anything app-looking that remains is a likely orphan.
    ignored: list[str] = []
    orphan_groups: dict[str, dict[str, Any]] = {}
    for role, path in roots:
        if str(path) in claimed:
            continue
        n = norm(path.name)
        if not n or path.name in GENERIC_USERDATA or n in {norm(x) for x in GENERIC_USERDATA}:
            ignored.append(str(path))
            continue
        if path.name.startswith("ksycoca5_"):
            ignored.append(str(path))
            continue
        if any(path.name.startswith(prefix) for prefix in GENERIC_PREFIXES):
            # Desktop-ID-looking dirs are still useful if they contain app data;
            # don't discard them, but don't label framework caches as apps.
            if role in {"缓存", "用户状态"} and "." in path.name:
                ignored.append(str(path))
                continue
        key = n
        group = orphan_groups.setdefault(key, {"name": path.name, "paths": [], "roles": set()})
        group["paths"].append((role, str(path)))
        group["roles"].add(role)

    # Add AppImage files already sitting in Trash as residual entries.
    for sw in softwares:
        if sw.method == "appimage" and sw.is_residual:
            key = "appimage:" + norm(sw.name)
            group = orphan_groups.setdefault(key, {"name": sw.name, "paths": [], "roles": set()})
            for a in sw.artifacts:
                group["paths"].append((a.role, a.path))
                group["roles"].add(a.role)

    residual_softwares: list[Software] = []
    for key, group in orphan_groups.items():
        sw = Software(
            id=f"residual:{key}", name=group["name"], method="residual",
            method_title="已卸载 / 未知 · 用户数据和缓存残留",
            status="residual",
            summary="未发现与该目录对应的已安装包、AppImage、桌面入口或手动安装程序",
            aliases=sorted(candidate_aliases(group["name"])),
        )
        seen = set()
        for role, path in group["paths"]:
            if path in seen or not os.path.lexists(path):
                continue
            seen.add(path)
            a = Artifact(role, path, size=path_size(path))
            if "缓存" in role:
                sw.cache_artifacts.append(a)
            else:
                sw.data_artifacts.append(a)
        if sw.data_artifacts or sw.cache_artifacts:
            sw.warnings.append("这是启发式识别结果。删除前请展开路径并确认不再需要。")
            residual_softwares.append(sw)
    residual_softwares.sort(key=lambda s: s.total_known_size, reverse=True)
    if residual_softwares:
        inventory.methods.append(Method(
            id="residual", title="已卸载 / 未知 · 残留数据",
            description="软件本体没有找到，但发现了用户数据或缓存。红色=疑似残留。",
            softwares=residual_softwares, residual=True,
        ))
    inventory.ignored_userdata = sorted(ignored)


def read_os_release() -> dict[str, str]:
    result: dict[str, str] = {}
    try:
        for line in Path("/etc/os-release").read_text(errors="replace").splitlines():
            if "=" not in line or line.lstrip().startswith("#"):
                continue
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip().strip('"')
    except OSError:
        pass
    return result


def distro_family(os_release: dict[str, str]) -> set[str]:
    raw = " ".join([
        os_release.get("ID", ""),
        os_release.get("ID_LIKE", ""),
        os_release.get("NAME", ""),
    ]).casefold()
    return {token for token in re.split(r"[\s,_-]+", raw) if token}


def scan_inventory(progress=None) -> Inventory:
    started = time.monotonic()
    home = Path.home()
    if progress:
        progress("读取系统信息")
    os_release = read_os_release()
    os_name = os_release.get("PRETTY_NAME", "Linux")
    family = distro_family(os_release)

    methods: list[Method] = []
    if progress:
        progress("检测系统包管理器")

    available: dict[str, Any] = {}
    if which("pacman"):
        available["pacman"] = lambda: collect_pacman(home)
    if which("dpkg-query"):
        available["apt"] = lambda: collect_apt(home)
    if which("rpm") and which("dnf"):
        available["dnf"] = lambda: collect_rpm(home, "dnf")
    elif which("rpm") and which("yum"):
        available["yum"] = lambda: collect_rpm(home, "yum")
    elif which("rpm") and which("zypper"):
        available["zypper"] = lambda: collect_rpm(home, "zypper")
    if which("apk"):
        available["apk"] = lambda: collect_apk(home)
    if which("emerge") or Path("/var/db/pkg").is_dir():
        available["emerge"] = lambda: collect_emerge(home)
    if which("xbps-query"):
        available["xbps"] = lambda: collect_xbps(home)
    if which("nix-env"):
        available["nix"] = lambda: collect_nix(home)

    # Distro-native manager first. The remaining detected managers still appear
    # later so mixed systems (for example Debian + Homebrew) are represented.
    preferred: list[str] = []
    checks = [
        ({"debian", "ubuntu", "linuxmint", "pop", "elementary", "kali"}, "apt"),
        ({"fedora", "rhel", "centos", "rocky", "almalinux", "ol", "amzn"}, "dnf"),
        ({"rhel", "centos", "oracle", "scientific", "amzn"}, "yum"),
        ({"arch", "manjaro", "endeavouros", "artix"}, "pacman"),
        ({"opensuse", "suse", "sles", "sled"}, "zypper"),
        ({"alpine"}, "apk"),
        ({"gentoo", "funtoo"}, "emerge"),
        ({"void"}, "xbps"),
        ({"nixos"}, "nix"),
    ]
    for names, manager in checks:
        if family & names and manager in available and manager not in preferred:
            preferred.append(manager)
    for manager in ["apt", "dnf", "yum", "zypper", "pacman", "apk", "emerge", "xbps", "nix"]:
        if manager in available and manager not in preferred:
            # dnf supersedes yum when both are installed.
            if manager == "yum" and "dnf" in available:
                continue
            preferred.append(manager)

    metadata_titles = {
        "apt": ("系统包管理器 · apt/dpkg", "Ubuntu/Debian 软件包、依赖与手动安装标记"),
        "dnf": ("系统包管理器 · dnf/rpm", "Fedora/RHEL/CentOS 及兼容发行版软件包"),
        "yum": ("系统包管理器 · yum/rpm", "CentOS/RHEL 传统 yum 软件包"),
        "zypper": ("系统包管理器 · zypper/rpm", "openSUSE/SLES 软件包"),
        "pacman": ("系统包管理器 · pacman", "Arch/Manjaro 官方仓库、AUR 和依赖包"),
        "apk": ("系统包管理器 · apk", "Alpine Linux 软件包"),
        "emerge": ("系统包管理器 · Portage/emerge", "Gentoo world 集合与依赖"),
        "xbps": ("系统包管理器 · XBPS", "Void Linux 软件包"),
        "nix": ("Nix profile", "Nix 用户 profile 与 store 路径"),
    }
    for manager in preferred:
        if progress:
            progress(f"扫描 {manager}")
        packages = available[manager]()
        if packages:
            title, description = metadata_titles[manager]
            methods.append(Method(manager, title, description, packages))

    if progress:
        progress("扫描 Flatpak / Snap / Homebrew")
    flatpak = collect_flatpak(home)
    snap = collect_snap(home)
    brew = collect_brew(home)
    if flatpak:
        methods.append(Method("flatpak", "Flatpak", "沙箱应用及 ~/.var/app 数据", flatpak))
    if snap:
        methods.append(Method("snap", "Snap", "Snap 应用及 ~/snap 数据", snap))
    if brew:
        methods.append(Method("brew", "Homebrew / Linuxbrew", "brew formula、Cellar 位置与依赖", brew))

    if progress:
        progress("读取桌面入口和命令链接")
    desktop = desktop_entries(home)
    local_bins = collect_local_bin(home)
    if progress:
        progress("扫描 AppImage")
    appimages = collect_appimages(home, desktop, local_bins)
    if appimages:
        methods.append(Method("appimage", "AppImage", "便携程序、启动入口与回收站文件", appimages))

    if progress:
        progress("扫描编辑器插件与扩展")
    vim_plugins = collect_vim_plugins(home)
    vscode_extensions = collect_vscode_extensions(home)
    editor_plugins = vim_plugins + vscode_extensions
    if editor_plugins:
        methods.append(Method(
            "editor-plugins", "编辑器插件 · Vim / Neovim / VS Code",
            "插件本体、配置、用户数据、子扩展和宿主应用关系",
            editor_plugins,
        ))

    if progress:
        progress("扫描浏览器扩展与 Flash/NPAPI 插件")
    browser_plugins = collect_browser_extensions(home) + collect_flash_plugins(home)
    if browser_plugins:
        methods.append(Method(
            "browser-plugins", "浏览器扩展 / 插件",
            "Chrome/Chromium/Firefox 扩展及 Flash/NPAPI 历史插件",
            browser_plugins,
        ))

    if progress:
        progress("扫描源码 / 手动安装")
    manual = collect_source_manual(home, desktop, local_bins)
    if manual:
        methods.append(Method("source", "源码 / 手动安装", "由 ~/.local/bin 或桌面入口发现的非包管理器程序", manual))

    if progress:
        progress("扫描语言级包管理器")
    pip = collect_pip_user(home)
    pipx = collect_pipx(home)
    npm = collect_npm(home)
    yarn = collect_yarn(home)
    pnpm = collect_pnpm(home)
    cargo = collect_cargo(home)
    gems = collect_gem(home)
    go_packages = collect_go(home)
    conda = collect_conda(home)
    composer = collect_composer(home)
    luarocks = collect_luarocks(home)
    if pip or pipx:
        methods.append(Method("python", "Python · pip / pipx", "用户级 Python 包和隔离应用", pip + pipx))
    node = npm + yarn + pnpm
    if node:
        methods.append(Method("node", "Node.js · npm / yarn / pnpm", "Node.js 全局包", node))
    if cargo:
        methods.append(Method("rust", "Rust · cargo", "cargo install 安装的程序", cargo))
    if gems:
        methods.append(Method("ruby", "Ruby · gem", "Ruby 本地 gem", gems))
    if go_packages:
        methods.append(Method("go", "Go · go install", "GOPATH/bin 中的 Go 程序", go_packages))
    if conda:
        methods.append(Method("conda", "Conda · 当前环境", "Conda 当前环境中的包", conda))
    other_lang = composer + luarocks
    if other_lang:
        methods.append(Method("other-lang", "其他语言包管理器", "PHP Composer、Lua LuaRocks", other_lang))

    remove_source_plugin_overlaps(methods)

    inventory = Inventory(
        generated_at=dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        hostname=os.uname().nodename,
        os_name=os_name,
        home=str(home),
        methods=methods,
    )
    if progress:
        progress("关联桌面名称与软件包")
    enrich_package_aliases_from_desktops(inventory, desktop)
    if progress:
        progress("匹配用户数据与缓存")
    attach_userdata(inventory, home)
    inventory.scan_seconds = time.monotonic() - started
    return inventory


# ---------------------------------------------------------------------------
# Detail and export
# ---------------------------------------------------------------------------

RPM_MANAGERS = {"dnf", "yum", "zypper"}


def package_dependencies(sw: Software) -> list[str]:
    manager = sw.metadata.get("manager")
    name = sw.package_name
    if not name:
        return []
    if manager in RPM_MANAGERS:
        q = run(["rpm", "-qR", name], timeout=20)
        if q.returncode == 0:
            return [line.strip() for line in q.stdout.splitlines() if line.strip() and not line.startswith("rpmlib(")]
    elif manager == "apt":
        q = run(["dpkg-query", "-W", "-f=${Depends}", name], timeout=20)
        if q.returncode == 0:
            value = q.stdout.replace("\n", " ")
            deps: list[str] = []
            for item in value.split(","):
                item = item.strip()
                if item:
                    deps.append(re.split(r"\s*\|\s*", item)[0].strip())
            return deps
    elif manager == "apk":
        q = run(["apk", "info", "-R", name], timeout=20)
        if q.returncode == 0:
            return [line.strip() for line in q.stdout.splitlines() if line.strip() and line.strip() != name]
    elif manager == "xbps":
        q = run(["xbps-query", "-x", name], timeout=20)
        if q.returncode == 0:
            return [line.strip() for line in q.stdout.splitlines() if line.strip()]
    elif manager == "emerge":
        atom = sw.metadata.get("atom", name)
        category, _, pkg = atom.partition("/")
        version = sw.version
        metadata_dir = Path("/var/db/pkg") / category / f"{pkg}-{version}"
        for filename in ("DEPEND", "RDEPEND"):
            try:
                text = (metadata_dir / filename).read_text(errors="replace")
                if text.strip():
                    return text.split()
            except OSError:
                pass
    elif manager in {"pip", "pipx"}:
        try:
            dist = importlib_metadata.distribution(name)
            return [req.split(";", 1)[0].strip() for req in (dist.requires or [])]
        except importlib_metadata.PackageNotFoundError:
            pass
    elif manager == "gem":
        q = run(["gem", "dependency", name, "--local"], timeout=20)
        if q.returncode == 0:
            return [line.strip() for line in q.stdout.splitlines() if "(" in line and not line.startswith("Gem")]
    return []


def canonical_package_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).casefold()


def reverse_dependencies(sw: Software) -> list[str]:
    manager = sw.metadata.get("manager")
    name = sw.package_name
    if not name:
        return []
    if sw.required_by:
        return list(dict.fromkeys(sw.required_by))
    if manager == "apt":
        q = run(["apt-cache", "rdepends", "--installed", name], timeout=20)
        if q.returncode == 0:
            result: list[str] = []
            for line in q.stdout.splitlines()[1:]:
                item = line.strip().lstrip("|").strip()
                if item and item != name:
                    result.append(item)
            return list(dict.fromkeys(result))
    elif manager in RPM_MANAGERS:
        q = run(["rpm", "-q", "--whatrequires", name, "--qf", "%{NAME}\\n"], timeout=20)
        if q.returncode == 0:
            return list(dict.fromkeys(line.strip() for line in q.stdout.splitlines() if line.strip() and line.strip() != name))
    elif manager == "apk":
        q = run(["apk", "info", "-r", name], timeout=20)
        if q.returncode == 0:
            return list(dict.fromkeys(line.strip() for line in q.stdout.splitlines() if line.strip() and line.strip() != name))
    elif manager == "xbps":
        q = run(["xbps-query", "-X", name], timeout=20)
        if q.returncode == 0:
            return list(dict.fromkeys(line.strip() for line in q.stdout.splitlines() if line.strip() and line.strip() != name))
    elif manager == "emerge":
        atom = sw.metadata.get("atom", name)
        q = run(["equery", "depends", atom], timeout=20)
        if q.returncode == 0:
            result = []
            for line in q.stdout.splitlines():
                match = re.match(r"^\s*([^\s]+/[^\s]+)", line)
                if match and match.group(1) != atom:
                    result.append(match.group(1).split("-")[0])
            return list(dict.fromkeys(result))
    elif manager == "brew":
        q = run(["brew", "uses", "--installed", name], timeout=20)
        if q.returncode == 0:
            return [line.strip() for line in q.stdout.splitlines() if line.strip()]
    elif manager in {"pip", "pipx"}:
        target = canonical_package_name(name)
        result = []
        for dist in importlib_metadata.distributions():
            try:
                dist_name = dist.metadata.get("Name", "")
            except Exception:
                continue
            for requirement in dist.requires or []:
                req_name = re.split(r"[<>=!~;\[\s]", requirement, 1)[0]
                if canonical_package_name(req_name) == target and canonical_package_name(dist_name) != target:
                    result.append(dist_name)
                    break
        return sorted(set(result), key=str.casefold)
    elif manager in {"npm", "yarn", "pnpm"}:
        location = next((a.path for a in sw.artifacts if a.role == "安装位置"), "")
        if location:
            package_dir = Path(location)
            modules = package_dir.parent.parent if package_dir.parent.name.startswith("@") else package_dir.parent
            result = []
            for candidate in _node_modules_packages(modules, manager, sw.method_title):
                if name in candidate.dependencies:
                    result.append(candidate.name)
            return result
    return []


def plugin_reverse_dependencies(sw: Software, inventory: Inventory) -> list[str]:
    if not sw.is_plugin or not sw.package_name:
        return []
    target = canonical_package_name(sw.package_name)
    target_name = canonical_package_name(sw.name)
    result: list[str] = []
    for other in inventory.all_software():
        if other.id == sw.id or not other.is_plugin:
            continue
        for dependency in other.dependencies:
            dep = canonical_package_name(dependency)
            if dep == target or dep == target_name:
                result.append(other.name)
                break
    return sorted(set(result), key=str.casefold)


def software_description(sw: Software) -> str:
    text = (sw.summary or "").strip()
    if text and text != sw.name and not text.startswith("/home/"):
        return text
    if sw.metadata.get("manager") == "gem":
        q = run(["gem", "specification", sw.package_name, "summary", "--local"], timeout=20)
        if q.returncode == 0:
            value = q.stdout.strip()
            if value:
                return value
    if sw.method == "appimage":
        return "AppImage 便携应用。包内通常自带运行库，具体用途以内置桌面说明和程序界面为准。"
    if sw.method == "source":
        return "从源码或手动安装目录发现的程序；包管理器没有提供简介。"
    if sw.method == "go":
        return "通过 go install 安装的二进制程序；本地没有统一的包描述字段。"
    return "本地包管理器没有提供该软件的详细简介。"


def software_kind(sw: Software) -> str:
    if sw.is_residual:
        return "残留数据（软件本体未发现）"
    if sw.status == "plugin_host_missing":
        return "插件残留（宿主应用未安装）"
    if sw.status == "plugin_body_missing":
        return "插件数据（插件本体缺失）"
    if sw.status == "plugin_not_installed":
        return "配置中声明但尚未安装的插件"
    if sw.is_plugin:
        plugin_type = sw.metadata.get("plugin_type", "插件 / 扩展")
        hosts = sw.metadata.get("host_apps") or [sw.metadata.get("host_app", "")]
        host_text = "、".join(str(h) for h in hosts if h)
        return f"{plugin_type}（宿主：{host_text}）" if host_text else str(plugin_type)
    if sw.status == "unregistered":
        return "只有程序文件 / 源码目录，未发现启动入口"
    explicit = sw.metadata.get("explicit")
    if explicit is False:
        return "依赖项 / 库或运行时组件"
    if explicit is True:
        if sw.method in {"appimage", "source", "go"}:
            return "用户安装的应用或命令行程序"
        return "用户主动安装的软件"
    if reverse_dependencies(sw):
        return "已安装软件（也被其他软件依赖）"
    return "已安装软件（安装原因未明确记录）"


def install_reason_explanation(sw: Software) -> str:
    if sw.is_residual:
        return "没有找到对应的软件本体；这里展示的是仍留在用户目录中的配置、数据或缓存。"
    if sw.status == "plugin_host_missing":
        return "插件本体或数据仍然存在，但它依赖的宿主应用没有检测到。它可能属于已经卸载或改名后的编辑器/浏览器。"
    if sw.status == "plugin_body_missing":
        return "插件配置或用户数据仍然存在，但没有找到插件本体目录。它不是普通独立软件残留，不能简单按应用数据删除。"
    if sw.status == "plugin_not_installed":
        return "编辑器配置中声明了这个插件，但插件管理器目录中没有找到本体，可能是配置保留了但插件尚未安装。"
    if sw.is_plugin:
        hosts = sw.metadata.get("host_apps") or [sw.metadata.get("host_app", "宿主应用")]
        return f"这是安装在 {'、'.join(str(h) for h in hosts if h)} 中的扩展/插件，不是独立桌面软件。"
    if sw.status == "unregistered":
        return "程序文件或源码目录存在，但没有找到指向它的 .desktop 桌面入口或 ~/.local/bin 命令链接。它可能是构建目录、未完成安装的手动程序，或需要直接运行的可执行文件。"
    if sw.install_reason:
        explicit = sw.metadata.get("explicit")
        if explicit is False:
            return "这不是用户主动选择的软件，而是安装其他软件时由包管理器自动带入的依赖。"
        if explicit is True:
            return "这是用户或管理员主动安装的软件，而不是仅作为其他包的依赖被带入。"
        return sw.install_reason
    explicit = sw.metadata.get("explicit")
    if explicit is False:
        return "包管理器将其标记为依赖项：它不是用户主动安装的最终软件，而是其他软件运行时需要的库或组件。"
    if explicit is True:
        return "由用户或管理员主动安装。"
    return "包管理器没有提供明确的“主动安装 / 依赖”标记。"


def package_files(sw: Software) -> list[str]:
    manager = sw.metadata.get("manager")
    name = sw.package_name
    if not name:
        return [a.path for a in sw.artifacts if os.path.exists(a.path)]
    if manager == "pacman":
        q = run(["pacman", "-Qlq", name], timeout=20)
    elif manager in RPM_MANAGERS:
        q = run(["rpm", "-ql", name], timeout=20)
    elif manager == "apt":
        q = run(["dpkg-query", "-L", name], timeout=20)
    elif manager == "apk":
        q = run(["apk", "info", "-L", name], timeout=20)
    elif manager == "xbps":
        q = run(["xbps-query", "-f", name], timeout=20)
    elif manager == "emerge":
        q = run(["qlist", "-e", name], timeout=20)
    elif manager == "nix":
        store = sw.metadata.get("store_path", "")
        q = run(["nix-store", "-qR", store], timeout=20) if store else None
    elif manager == "brew":
        q = run(["brew", "--prefix", name], timeout=15)
        if q.returncode == 0:
            root = Path(q.stdout.strip())
            if root.is_dir():
                return [str(p) for p in root.rglob("*") if not p.is_dir()]
        return []
    else:
        return [a.path for a in sw.artifacts if os.path.exists(a.path)]
    if q is not None and q.returncode == 0:
        return [line for line in q.stdout.splitlines() if line.strip()]
    return []


def package_cache(sw: Software) -> list[str]:
    manager = sw.metadata.get("manager")
    name = sw.package_name
    if not name:
        return []
    patterns: list[str] = []
    if manager == "pacman":
        patterns.append(f"/var/cache/pacman/pkg/{glob.escape(name)}-*.pkg.tar.*")
    elif manager in {"dnf", "yum"}:
        patterns.extend([
            f"/var/cache/{manager}/{glob.escape(name)}*",
            f"/var/cache/{manager}/**/{glob.escape(name)}*",
        ])
    elif manager == "zypper":
        patterns.append(f"/var/cache/zypp/packages/**/{glob.escape(name)}*")
    elif manager == "apt":
        patterns.append(f"/var/cache/apt/archives/{glob.escape(name)}*")
    elif manager == "apk":
        patterns.append(f"/var/cache/apk/{glob.escape(name)}*")
    elif manager == "xbps":
        patterns.append(f"/var/cache/xbps/{glob.escape(name)}*")
    elif manager == "brew" and which("brew"):
        q = run(["brew", "--cache", name], timeout=15)
        if q.returncode == 0 and q.stdout.strip():
            patterns.append(q.stdout.strip())
    elif manager in {"pip", "pipx"}:
        patterns.append(str(Path.home() / ".cache" / "pip"))
    elif manager == "cargo":
        patterns.append(str(Path.home() / ".cargo" / "registry" / "cache"))
    elif manager == "gem":
        patterns.append(str(Path.home() / ".gem"))
    found: list[str] = []
    for pattern in patterns:
        found.extend(glob.glob(pattern, recursive=True))
    return sorted({p for p in found if os.path.exists(p)})


def uninstall_plan(sw: Software) -> list[str]:
    manager = sw.metadata.get("manager")
    pkg = shlex.quote(sw.package_name) if sw.package_name else ""
    if sw.method == "vim-plugin":
        plugin_manager = sw.metadata.get("plugin_manager", "")
        if plugin_manager == "vim-plug":
            return ["# 从 Vim 配置中删除对应 Plug 行，然后执行: vim +PlugClean"]
        if plugin_manager == "lazy.nvim":
            return ["# 从 lazy.nvim 配置中删除该插件，然后重启 Neovim 执行 :Lazy clean"]
        return ["# 在 Vim/Neovim 插件管理器或配置中移除该插件"]
    if sw.method == "vscode-extension":
        host = sw.metadata.get("host_app", "")
        command = "codium" if "VSCodium" in host else "code"
        return [f"{command} --uninstall-extension {pkg}"]
    if sw.method == "browser-extension":
        return ["# 在浏览器扩展管理页中移除对应扩展（扩展本体不要直接手删）"]
    if sw.method == "browser-plugin":
        commands = []
        for artifact in sw.artifacts:
            if artifact.path.endswith(".so") or artifact.path.endswith("flashplayer"):
                commands.append(f"rm -f -- {shlex.quote(artifact.path)}")
        return commands or ["# 在浏览器插件/兼容设置中停用或移除"]
    if manager == "pacman":
        return [f"sudo pacman -Rns -- {pkg}"]
    if manager == "dnf":
        return [f"sudo dnf remove {pkg}"]
    if manager == "yum":
        return [f"sudo yum remove {pkg}"]
    if manager == "zypper":
        return [f"sudo zypper remove {pkg}"]
    if manager == "apt":
        return [f"sudo apt remove --purge {pkg}"]
    if manager == "apk":
        return [f"sudo apk del {pkg}"]
    if manager == "emerge":
        return [f"sudo emerge --unmerge {pkg}", "# 之后使用 sudo emerge --depclean 清理不再需要的依赖"]
    if manager == "xbps":
        return [f"sudo xbps-remove -R {pkg}"]
    if manager == "nix":
        return [f"nix-env -e {pkg}"]
    if manager == "brew":
        return [f"brew uninstall {pkg}"]
    if manager == "pip":
        return [f"{shlex.quote(sys.executable)} -m pip uninstall {pkg}"]
    if manager == "pipx":
        return [f"pipx uninstall {pkg}"]
    if manager == "npm":
        return [f"npm uninstall -g {pkg}"]
    if manager == "yarn":
        return [f"yarn global remove {pkg}"]
    if manager == "pnpm":
        return [f"pnpm remove -g {pkg}"]
    if manager == "cargo":
        return [f"cargo uninstall {pkg}"]
    if manager == "gem":
        return [f"gem uninstall {pkg}"]
    if manager == "go":
        return [f"rm -f -- \"$(go env GOPATH)/bin/{sw.name}\""]
    if manager == "conda":
        return [f"conda remove {pkg}"]
    if manager == "composer":
        return [f"composer global remove {pkg}"]
    if manager == "luarocks":
        return [f"luarocks remove {pkg}"]
    if sw.method == "flatpak":
        return [f"flatpak uninstall {pkg}"]
    if sw.method == "snap":
        return [f"sudo snap remove {pkg}"]
    if sw.method == "appimage":
        path = sw.metadata.get("appimage_path", "")
        out = [f"rm -f -- {shlex.quote(path)}"] if path else []
        for artifact in sw.artifacts:
            if artifact.role == "启动入口":
                out.append(f"rm -f -- {shlex.quote(artifact.path)}")
        return out
    if sw.method == "source":
        root = sw.metadata.get("install_root", "")
        return [f"# 手动安装目录，请确认后删除: {root}"] if root else []
    return []

def cleanup_paths(sw: Software) -> list[Artifact]:
    """Paths the built-in cleanup action is allowed to remove.

    Installed package bodies are deliberately NOT removed here; package-manager
    uninstall is shown as a command instead. User data/cache and orphaned
    residuals are removable after explicit confirmation.
    """
    result: list[Artifact] = []
    result.extend(sw.data_artifacts)
    result.extend(sw.cache_artifacts)
    if sw.is_residual or sw.status == "unregistered" or sw.method in {"appimage", "source"}:
        result.extend(a for a in sw.artifacts if "容器目录" not in a.note)
    seen = set()
    clean: list[Artifact] = []
    for a in result:
        if a.path and a.path not in seen and os.path.lexists(a.path):
            seen.add(a.path)
            clean.append(a)
    return clean


def delete_cleanup_paths(sw: Software) -> tuple[list[str], list[str]]:
    """Delete only paths approved by cleanup_paths(); return successes/errors."""
    deleted: list[str] = []
    errors: list[str] = []
    for artifact in cleanup_paths(sw):
        path = Path(artifact.path)
        try:
            if path.is_symlink() or path.is_file():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)
            else:
                continue
            deleted.append(artifact.path)
        except OSError as exc:
            errors.append(f"{artifact.path}: {exc}")
    return deleted, errors


def detail_lines(sw: Software, inventory: Inventory) -> list[tuple[str, str]]:
    lines: list[tuple[str, str]] = []

    def add(text: str = "", style: str = "normal") -> None:
        lines.append((text, style))

    status_style = (
        "danger" if sw.is_residual or sw.status == "plugin_host_missing"
        else "warning" if sw.status in {"unregistered", "plugin_body_missing", "plugin_not_installed"}
        else "success"
    )
    add(f"[{sw.status_label}] {sw.name}" + (f"  {sw.version}" if sw.version else ""), status_style)
    add(f"安装方式：{sw.method_title}")
    if sw.package_name:
        add(f"软件标识：{sw.package_name}")
    if sw.package_group:
        add(f"来源分组：{sw.package_group}")
    if sw.metadata.get("self_updating"):
        add("")
        add("── 安装 / 更新方式 ──", "title")
        add(f"更新方式：{sw.metadata.get('update_system', '应用内自更新')}")
        if sw.metadata.get("application_id"):
            add(f"应用 ID：{sw.metadata['application_id']}")
        if sw.metadata.get("vendor"):
            add(f"发布者：{sw.metadata['vendor']}")
        if sw.metadata.get("update_directory"):
            add(f"更新目录：{sw.metadata['update_directory']}", "dim")
        mar_files = sw.metadata.get("mar_files") or []
        if mar_files:
            add("发现的 MAR 更新包：", "subtitle")
            for mar_file in mar_files:
                add(f"  - {mar_file}")
        if sw.metadata.get("update_config"):
            add(f"更新配置：{sw.metadata['update_config']}", "dim")
    if sw.is_plugin:
        add("")
        add("── 插件关系 ──", "title")
        add(f"插件类型：{sw.metadata.get('plugin_type', '插件 / 扩展')}")
        hosts = sw.metadata.get("host_apps") or [sw.metadata.get("host_app", "")]
        host_text = "、".join(str(h) for h in hosts if h) or "未检测到"
        add(f"宿主应用：{host_text}")
        add(f"宿主状态：{'已安装' if sw.metadata.get('host_installed') else '未检测到'}")
        if sw.metadata.get("plugin_manager"):
            add(f"插件管理器：{sw.metadata['plugin_manager']}")
        if sw.metadata.get("extension_id"):
            add(f"扩展 ID：{sw.metadata['extension_id']}")
        body_installed = sw.metadata.get("body_installed")
        if body_installed is not None:
            add(f"插件本体：{'已找到' if body_installed else '未找到'}", "success" if body_installed else "warning")
        sub_extensions = sw.metadata.get("sub_extensions") or []
        if sub_extensions:
            add("包含的子扩展：")
            for item in sub_extensions:
                add(f"  - {item}")
        extension_pack = sw.metadata.get("extension_pack") or []
        if extension_pack:
            add("扩展包包含：")
            for item in extension_pack:
                add(f"  - {item}")
        if sw.metadata.get("active_status"):
            add(f"运行状态：{sw.metadata['active_status']}", "warning")

    add("")
    add("── 软件介绍 ──", "title")
    add(f"软件类型：{software_kind(sw)}")
    add("用途说明：")
    for part in wrap_display(software_description(sw), 100):
        add("  " + part)
    add("安装原因：")
    for part in wrap_display(install_reason_explanation(sw), 100):
        reason_style = "warning" if sw.is_residual or sw.status == "unregistered" or sw.metadata.get("explicit") is False else "normal"
        add("  " + part, reason_style)
    if sw.warnings:
        add("")
        for warning in sw.warnings:
            for part in wrap_display("⚠ " + warning, 100):
                add(part, "warning")

    add("")
    add("── 安装位置 / 本体文件 ──", "title")
    if sw.artifacts:
        for a in sw.artifacts:
            suffix = f"  [{human_size(a.size)}]" if a.size is not None else ""
            add(f"  {a.role}: {a.path}{suffix}")
            if a.note:
                add(f"      {a.note}", "dim")
    else:
        add("  （没有独立文件；由包管理器数据库管理）", "dim")
    files = package_files(sw)
    if files:
        add("")
        add(f"登记的文件 / 目录（共 {len(files)} 项）", "subtitle")
        # Full list stays available in exported reports; the TUI groups it.
        groups: dict[str, int] = {}
        for path in files:
            p = Path(path)
            if path.startswith("/usr/bin/"):
                key = "/usr/bin"
            elif path.startswith("/usr/lib/"):
                key = "/usr/lib"
            elif path.startswith("/usr/share/"):
                key = "/usr/share"
            elif path.startswith("/etc/"):
                key = "/etc"
            elif path.startswith("/opt/"):
                key = "/opt"
            else:
                key = str(p.parent)
            groups[key] = groups.get(key, 0) + 1
        for key, count in sorted(groups.items(), key=lambda kv: (-kv[1], kv[0]))[:20]:
            add(f"  {key}: {count} 个")
        if len(groups) > 20:
            add(f"  ... 还有 {len(groups) - 20} 个目录组", "dim")
        add("  [提示] 按 e 导出完整文件清单。", "dim")

    add("")
    add("── 依赖关系 ──", "title")
    resolved_dependencies = list(dict.fromkeys(sw.dependencies + package_dependencies(sw)))
    reverse = list(dict.fromkeys(reverse_dependencies(sw) + plugin_reverse_dependencies(sw, inventory)))
    add("它需要哪些软件（正向依赖）：")
    if resolved_dependencies:
        for part in wrap_display("  " + ", ".join(resolved_dependencies), 100):
            add(part)
    else:
        add("  无记录，或由 AppImage / 程序自身携带运行时。", "dim")
    add("哪些已安装软件需要它（反向依赖）：")
    if reverse:
        for part in wrap_display("  " + ", ".join(reverse), 100):
            add(part, "warning")
        add("  ↑ 卸载这个包可能影响以上软件。", "warning")
    else:
        add("  没有发现其他已安装软件依赖它。", "dim")
    if sw.optional_dependencies:
        add("可选依赖：" + ", ".join(sw.optional_dependencies))
    if sw.metadata.get("installed_size"):
        add("包管理器记录大小：" + str(sw.metadata["installed_size"]))
    if sw.metadata.get("installed_size_bytes"):
        add("包管理器记录大小：" + human_size(sw.metadata["installed_size_bytes"]))
    if sw.metadata.get("url"):
        add("上游网址：" + str(sw.metadata["url"]))
    if sw.metadata.get("licenses"):
        add("许可证：" + str(sw.metadata["licenses"]))

    add("")
    add("── 用户数据 ──", "title")
    if sw.data_artifacts:
        for a in sw.data_artifacts:
            size = f"  [{human_size(a.size)}]" if a.size is not None else ""
            add(f"  {a.role}: {a.path}{size}", "warning" if sw.is_residual else "normal")
    else:
        add("  未检测到按名称匹配的用户数据", "dim")

    add("")
    add("── 缓存 / 包文件 ──", "title")
    cache_entries = list(sw.cache_artifacts)
    for path in package_cache(sw):
        archive = Artifact("包管理器缓存", path, size=path_size(path))
        cache_entries.append(archive)
    if cache_entries:
        for a in cache_entries:
            size = f"  [{human_size(a.size)}]" if a.size is not None else ""
            add(f"  {a.role}: {a.path}{size}", "warning" if sw.is_residual else "normal")
    else:
        add("  未检测到缓存", "dim")

    plan = uninstall_plan(sw)
    add("")
    add("── 卸载 / 清理建议 ──", "title")
    if sw.is_residual:
        add("  软件本体未发现。可先按 d 删除下面列出的残留数据和缓存。", "warning")
    elif sw.is_plugin:
        if sw.status == "plugin_body_missing":
            add("  插件本体缺失。d 只清理插件数据，不会自动删除编辑器配置。", "warning")
        elif sw.status == "plugin_not_installed":
            add("  插件尚未安装。d 可清理残留数据；通常应先重新安装或删除配置声明。", "warning")
        else:
            add("  插件应通过宿主应用的插件管理器卸载；d 默认只清理插件数据。", "dim")
    elif sw.method == "appimage":
        add("  AppImage 没有系统卸载器；d 会删除本体、启动入口及用户数据。", "warning")
    elif sw.method == "source":
        add("  源码/手动程序没有统一卸载器；d 会删除安装根目录、入口及用户数据。", "warning")
    else:
        add("  请使用软件包管理器卸载本体；d 只清理用户数据和缓存。", "dim")
    if plan:
        for cmd in plan:
            add("  " + cmd, "command")
    return lines


def software_to_dict(sw: Software, include_full_files: bool = False) -> dict[str, Any]:
    data = asdict(sw)
    data["artifacts"] = [asdict(a) for a in sw.artifacts]
    data["data_artifacts"] = [asdict(a) for a in sw.data_artifacts]
    data["cache_artifacts"] = [asdict(a) for a in sw.cache_artifacts]
    # Querying pacman -Qlq for every package would turn one export into
    # thousands of subprocesses. Full file lists remain available through the
    # detail-page export and --export-details.
    data["package_files"] = package_files(sw) if include_full_files else []
    data["package_cache"] = package_cache(sw) if include_full_files else []
    data["uninstall_plan"] = uninstall_plan(sw)
    return data


def inventory_to_dict(inv: Inventory, include_full_files: bool = False) -> dict[str, Any]:
    return {
        "format_version": 1,
        "app_version": APP_VERSION,
        "generated_at": inv.generated_at,
        "hostname": inv.hostname,
        "os_name": inv.os_name,
        "home": inv.home,
        "scan_seconds": round(inv.scan_seconds, 3),
        "methods": [
            {
                "id": m.id,
                "title": m.title,
                "description": m.description,
                "residual": m.residual,
                "count": len(m.softwares),
                "softwares": [software_to_dict(s, include_full_files=include_full_files) for s in m.softwares],
            }
            for m in inv.methods
        ],
        "ignored_userdata": inv.ignored_userdata,
    }


def export_software(sw: Software, inventory: Inventory) -> Path:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", sw.name).strip("-") or "software"
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = STATE_DIR / f"{safe}-{stamp}.md"
    lines = [f"# {sw.name}", "", f"- 主机：{inventory.hostname}", f"- 导出时间：{dt.datetime.now().astimezone().isoformat(timespec='seconds')}", ""]
    for text, _style in detail_lines(sw, inventory):
        lines.append(text)
    lines.extend(["", "## 完整文件清单", ""])
    files = package_files(sw)
    if files:
        lines.extend(f"- `{p}`" for p in files)
    else:
        lines.append("（无包管理器文件清单）")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def export_inventory(inv: Inventory) -> tuple[Path, Path]:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    json_path = STATE_DIR / f"inventory-{stamp}.json"
    md_path = STATE_DIR / f"inventory-{stamp}.md"
    json_path.write_text(json.dumps(inventory_to_dict(inv), ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Linux 软件清单", "",
        f"- 主机：{inv.hostname}",
        f"- 系统：{inv.os_name}",
        f"- 生成时间：{inv.generated_at}",
        f"- 扫描耗时：{inv.scan_seconds:.1f} 秒",
        "",
    ]
    for method in inv.methods:
        lines.extend([f"## {method.title}（{len(method.softwares)}）", ""])
        for sw in method.softwares:
            marker = "⚠ 疑似残留" if sw.is_residual else sw.status_label
            lines.append(f"- [{marker}] `{sw.name}` {sw.version} — {sw.method}")
            for a in sw.data_artifacts + sw.cache_artifacts:
                lines.append(f"  - {a.role}: `{a.path}`")
        lines.append("")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


# ---------------------------------------------------------------------------
# TUI
# ---------------------------------------------------------------------------

PAIR_NORMAL = 0
PAIR_TITLE = 1
PAIR_SUCCESS = 2
PAIR_WARNING = 3
PAIR_DANGER = 4
PAIR_SELECTED = 5
PAIR_DIM = 6
PAIR_COMMAND = 7
PAIR_HEADER = 8


class TUI:
    def __init__(self, inventory: Inventory, refresh_cb):
        self.inventory = inventory
        self.refresh_cb = refresh_cb
        self.method_index = 0
        self.method_offset = 0
        self.software_index = 0
        self.detail_scroll = 0
        self.state = "methods"
        self.list_filter = "all"
        self.search = ""
        self.scan_note = ""
        self.detail_cache: dict[str, list[tuple[str, str]]] = {}

    def init_colors(self) -> None:
        try:
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(PAIR_TITLE, curses.COLOR_CYAN, -1)
            curses.init_pair(PAIR_SUCCESS, curses.COLOR_GREEN, -1)
            curses.init_pair(PAIR_WARNING, curses.COLOR_YELLOW, -1)
            curses.init_pair(PAIR_DANGER, curses.COLOR_RED, -1)
            curses.init_pair(PAIR_SELECTED, curses.COLOR_BLACK, curses.COLOR_CYAN)
            curses.init_pair(PAIR_DIM, curses.COLOR_WHITE, -1)
            curses.init_pair(PAIR_COMMAND, curses.COLOR_MAGENTA, -1)
            curses.init_pair(PAIR_HEADER, curses.COLOR_BLUE, -1)
        except curses.error:
            pass

    def attr(self, style: str) -> int:
        if style == "title":
            return curses.color_pair(PAIR_TITLE) | curses.A_BOLD
        if style == "subtitle":
            return curses.color_pair(PAIR_HEADER) | curses.A_BOLD
        if style == "success":
            return curses.color_pair(PAIR_SUCCESS) | curses.A_BOLD
        if style == "warning":
            return curses.color_pair(PAIR_WARNING) | curses.A_BOLD
        if style == "danger":
            return curses.color_pair(PAIR_DANGER) | curses.A_BOLD
        if style == "dim":
            return curses.color_pair(PAIR_DIM) | curses.A_DIM
        if style == "command":
            return curses.color_pair(PAIR_COMMAND)
        return curses.color_pair(PAIR_NORMAL)

    def put(self, stdscr, y: int, x: int, text: str, width: int, style: str = "normal", selected: bool = False) -> None:
        max_y, max_x = stdscr.getmaxyx()
        if y < 0 or y >= max_y or x < 0 or x >= max_x:
            return
        width = max(0, min(width, max_x - x))
        text = clip_display(text, width)
        attr = self.attr(style)
        if selected:
            attr = curses.color_pair(PAIR_SELECTED) | curses.A_BOLD
            text = pad_display(text, width)
        try:
            stdscr.addstr(y, x, text, attr)
        except curses.error:
            pass

    def header(self, stdscr, title: str, subtitle: str = "") -> int:
        max_y, max_x = stdscr.getmaxyx()
        width = max(10, max_x)
        self.put(stdscr, 0, 0, " " * min(width, max_x), width, "title")
        self.put(stdscr, 0, 2, f"{APP_NAME}  {APP_VERSION}", max(1, width - 4), "title")
        self.put(stdscr, 1, 2, title, max(1, width - 4), "subtitle")
        if subtitle:
            self.put(stdscr, 1, max(4, max_x - min(display_width(subtitle), 40) - 2), subtitle, 40, "dim")
        self.put(stdscr, 2, 0, "─" * max_x, max_x, "dim")
        return 3

    def footer(self, stdscr, text: str, mode: str = "") -> None:
        max_y, max_x = stdscr.getmaxyx()
        y = max_y - 1
        self.put(stdscr, y - 1, 0, "─" * max_x, max_x, "dim")
        if mode:
            self.put(stdscr, y, 0, f" {mode}", max_x, "warning")
        else:
            self.put(stdscr, y, 0, text, max_x, "dim")

    def draw_methods(self, stdscr) -> None:
        stdscr.erase()
        max_y, max_x = stdscr.getmaxyx()
        counts = self.inventory.counts()
        total = sum(counts.values())
        self.header(stdscr, "选择安装方式", f"{self.inventory.hostname} · {self.inventory.os_name}")
        y = 4
        self.put(stdscr, y, 2, f"共发现 {len(self.inventory.methods)} 类、{total} 个软件条目。↑↓ 选择，数字直选，Enter 进入。", max_x - 4, "dim")
        y += 2
        visible = max(1, max_y - 9)
        if self.method_index < self.method_offset:
            self.method_offset = self.method_index
        elif self.method_index >= self.method_offset + visible:
            self.method_offset = self.method_index - visible + 1
        self.method_offset = max(0, min(self.method_offset, max(0, len(self.inventory.methods) - visible)))
        for idx in range(self.method_offset, min(len(self.inventory.methods), self.method_offset + visible)):
            method = self.inventory.methods[idx]
            selected = idx == self.method_index
            marker = "▶" if selected else " "
            style = "danger" if method.residual else "normal"
            count = len(method.softwares)
            label = f"{marker} {idx + 1:>2}. {method.title}"
            line = pad_display(label, max(20, max_x - 22)) + f"{count:>7} 个"
            self.put(stdscr, y, 2, line, max_x - 4, style, selected=selected)
            y += 1
        if not self.inventory.methods:
            self.put(stdscr, y + 1, 4, "未发现任何软件。", max_x - 8, "warning")
        elif self.inventory.methods:
            selected_method = self.inventory.methods[self.method_index]
            desc = selected_method.description
            if selected_method.residual:
                desc = "红色：软件本体已不存在，但用户数据/缓存仍占空间。" + desc
                style = "danger"
            else:
                style = "dim"
            self.put(stdscr, max_y - 3, 2, desc, max_x - 4, style)
        self.footer(stdscr, "↑↓ 选择  Enter 进入  1-9 直选  r 重新扫描  q 退出")

    @staticmethod
    def method_supports_filters(method: Method) -> bool:
        return any(isinstance(s.metadata.get("explicit"), bool) for s in method.softwares)

    @staticmethod
    def method_supports_foreign(method: Method) -> bool:
        return any(s.metadata.get("foreign") for s in method.softwares)

    def filter_order(self, method: Method) -> list[str]:
        order = ["all"]
        if method.id in {"editor-plugins", "browser-plugins"}:
            order.extend(["installed_plugins", "missing_plugins", "host_missing"])
        elif self.method_supports_filters(method):
            order.extend(["explicit", "deps"])
        if self.method_supports_foreign(method):
            order.append("foreign")
        return order


    def filtered_software(self, method: Method) -> list[Software]:
        items = method.softwares
        if self.list_filter == "explicit":
            items = [s for s in items if s.metadata.get("explicit") is True]
        elif self.list_filter == "deps":
            items = [s for s in items if s.metadata.get("explicit") is False]
        elif self.list_filter == "foreign":
            items = [s for s in items if s.metadata.get("foreign")]
        elif self.list_filter == "installed_plugins":
            items = [s for s in items if s.status == "installed"]
        elif self.list_filter == "missing_plugins":
            items = [s for s in items if s.status in {"plugin_body_missing", "plugin_not_installed", "unregistered"}]
        elif self.list_filter == "host_missing":
            items = [s for s in items if s.status == "plugin_host_missing"]
        if self.search:
            q = self.search.casefold()
            items = [s for s in items if q in s.name.casefold() or q in s.summary.casefold() or q in s.package_name.casefold()]
        return items

    def current_method(self) -> Method:
        return self.inventory.methods[self.method_index]

    def current_software(self) -> Optional[Software]:
        items = self.filtered_software(self.current_method())
        if not items:
            return None
        self.software_index = min(max(0, self.software_index), len(items) - 1)
        return items[self.software_index]

    def draw_list(self, stdscr) -> None:
        stdscr.erase()
        max_y, max_x = stdscr.getmaxyx()
        method = self.current_method()
        items = self.filtered_software(method)
        title_style = "danger" if method.residual else "subtitle"
        filters = {
            "all": "全部", "explicit": "显式安装", "deps": "依赖包", "foreign": "第三方/本地",
            "installed_plugins": "已安装插件", "missing_plugins": "插件本体缺失/未安装",
            "host_missing": "宿主已卸载",
        }
        self.header(stdscr, f"{method.title} · {filters.get(self.list_filter, '全部')}" + (f" · 搜索 {self.search}" if self.search else ""), f"{len(items)} / {len(method.softwares)} 个")
        legend = "● 已安装   ○ 无启动入口   ● 红色=残留   ｜ 主动=用户主动安装，依赖=随其他软件带入"
        self.put(stdscr, 3, 2, legend, max_x - 4, "dim")
        start_y = 4
        page = max(1, max_y - start_y - 3)
        if items:
            self.software_index = min(max(0, self.software_index), len(items) - 1)
            offset = max(0, self.software_index - page + 1)
            offset = min(offset, self.software_index)
            # Keep a stable viewport when moving up.
            offset = max(0, min(offset, len(items) - page))
            for row, idx in enumerate(range(offset, min(len(items), offset + page))):
                sw = items[idx]
                selected = idx == self.software_index
                if sw.is_residual:
                    style = "danger"
                    status = "● 残留"
                elif sw.status == "plugin_host_missing":
                    style = "danger"
                    status = "● 宿主缺失"
                elif sw.status == "plugin_body_missing":
                    style = "warning"
                    status = "○ 插件缺失"
                elif sw.status == "plugin_not_installed":
                    style = "warning"
                    status = "○ 仅声明"
                elif sw.status == "unregistered":
                    style = "warning"
                    status = "○ 无入口"
                else:
                    style = "normal"
                    status = "● 已安装"
                reason = ""
                explicit = sw.metadata.get("explicit")
                if explicit is True:
                    reason = "主动"
                elif explicit is False:
                    reason = "依赖"
                elif sw.metadata.get("foreign"):
                    reason = "第三方"
                elif sw.is_plugin:
                    reason = "插件"
                label = f" {status:<6} {sw.name}"
                version = sw.version[:22]
                right = f"{reason:<4} {version:>22}"
                line = pad_display(label, max(16, max_x - 32)) + right
                self.put(stdscr, start_y + row, 1, line, max_x - 2, style, selected=selected)
        else:
            self.put(stdscr, start_y + 2, 4, "没有匹配项。", max_x - 8, "warning")
        if method.residual:
            hint = "红色条目表示没有发现对应软件本体；Enter 查看路径"
        elif self.method_supports_filters(method) or self.method_supports_foreign(method) or method.id in {"editor-plugins", "browser-plugins"}:
            hint = "↑↓/PgUp/PgDn 移动  Enter 详情  / 搜索  f 筛选  e 导出清单  Esc 返回"
        else:
            hint = "↑↓/PgUp/PgDn 移动  Enter 详情  / 搜索  e 导出清单  Esc 返回"
        self.footer(stdscr, hint)

    def draw_detail(self, stdscr) -> None:
        stdscr.erase()
        max_y, max_x = stdscr.getmaxyx()
        sw = self.current_software()
        if not sw:
            self.footer(stdscr, "没有可显示的项目，Esc 返回")
            return
        self.header(stdscr, f"软件详情 · {sw.name}", sw.status_label)
        if sw.id not in self.detail_cache:
            self.detail_cache[sw.id] = detail_lines(sw, self.inventory)
        raw = self.detail_cache[sw.id]
        content: list[tuple[str, str]] = []
        for text, style in raw:
            wrapped = wrap_display(text, max(20, max_x - 5))
            for part in wrapped:
                content.append((part, style))
        page = max(1, max_y - 6)
        max_scroll = max(0, len(content) - page)
        self.detail_scroll = min(max(0, self.detail_scroll), max_scroll)
        for row in range(min(page, len(content) - self.detail_scroll)):
            text, style = content[self.detail_scroll + row]
            self.put(stdscr, 4 + row, 2, text, max_x - 4, style)
        extra = ""
        if sw.is_residual:
            extra = "  d 删除残留数据"
        elif cleanup_paths(sw):
            if sw.is_plugin:
                extra = "  d 清理插件数据"
            elif sw.method in {"appimage", "source"}:
                extra = "  d 卸载并清理"
            else:
                extra = "  d 清理用户数据/缓存"
        self.footer(stdscr, f"↑↓/PgUp/PgDn 滚动  e 导出详情{extra}  Esc 返回  q 退出")

    def confirm(self, stdscr, question: str) -> bool:
        max_y, max_x = stdscr.getmaxyx()
        self.put(stdscr, max_y - 2, 1, " " * max(1, max_x - 2), max_x - 2, "warning")
        self.put(stdscr, max_y - 2, 2, question + " [y/N] ", max_x - 4, "warning")
        curses.echo()
        try:
            answer = stdscr.getstr(max_y - 2, min(max_x - 2, 2 + display_width(question) + 7), 8)
        finally:
            curses.noecho()
        return answer.decode(errors="replace").strip().lower() in {"y", "yes"}

    def perform_cleanup(self, stdscr, sw: Software) -> None:
        paths = cleanup_paths(sw)
        if not paths:
            self.flash(stdscr, "没有可安全删除的路径。", "warning")
            return
        total = sum(a.size or 0 for a in paths)
        question = f"删除 {len(paths)} 个路径（约 {human_size(total)}）？"
        if not self.confirm(stdscr, question):
            self.flash(stdscr, "已取消。", "dim")
            return
        deleted, errors = delete_cleanup_paths(sw)
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with DELETE_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "time": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                "software": sw.name,
                "deleted": deleted,
                "errors": errors,
            }, ensure_ascii=False) + "\n")
        # Remove now-missing artifacts from the in-memory model.
        for container in (sw.artifacts, sw.data_artifacts, sw.cache_artifacts):
            container[:] = [a for a in container if os.path.lexists(a.path)]
        self.detail_cache.pop(sw.id, None)
        message = f"已删除 {len(deleted)} 个路径。日志：{DELETE_LOG}"
        if errors:
            message += f"  失败 {len(errors)} 个，见日志。"
        self.flash(stdscr, message, "success" if not errors else "warning")

    def flash(self, stdscr, text: str, style: str = "normal") -> None:
        max_y, max_x = stdscr.getmaxyx()
        self.put(stdscr, max_y - 2, 1, " " * max(1, max_x - 2), max_x - 2, "normal")
        self.put(stdscr, max_y - 2, 2, text, max_x - 4, style)
        stdscr.refresh()
        curses.napms(900)

    def ask_search(self, stdscr) -> None:
        max_y, max_x = stdscr.getmaxyx()
        curses.echo()
        curses.curs_set(1)
        try:
            self.put(stdscr, max_y - 2, 1, " " * max(1, max_x - 2), max_x - 2, "normal")
            self.put(stdscr, max_y - 2, 2, "搜索: ", max_x - 4, "warning")
            value = stdscr.getstr(max_y - 2, 9, max(1, max_x - 12))
            self.search = value.decode(errors="replace").strip()
            self.software_index = 0
        finally:
            curses.noecho()
            curses.curs_set(0)

    def loop(self, stdscr) -> None:
        curses.curs_set(0)
        stdscr.keypad(True)
        curses.noecho()
        curses.cbreak()
        self.init_colors()
        while True:
            if self.state == "methods":
                self.draw_methods(stdscr)
            elif self.state == "list":
                self.draw_list(stdscr)
            else:
                self.draw_detail(stdscr)
            stdscr.refresh()
            ch = stdscr.getch()
            if ch == -1:
                continue
            if self.state == "methods":
                if ch in (ord("q"), 27):
                    return
                if ch in (curses.KEY_UP, ord("k")):
                    self.method_index = (self.method_index - 1) % max(1, len(self.inventory.methods))
                elif ch in (curses.KEY_DOWN, ord("j")):
                    self.method_index = (self.method_index + 1) % max(1, len(self.inventory.methods))
                elif ch in (10, 13, curses.KEY_ENTER) and self.inventory.methods:
                    self.state = "list"
                    self.software_index = 0
                    self.list_filter = "all"
                    self.search = ""
                elif ord("1") <= ch <= ord("9"):
                    idx = ch - ord("1")
                    if idx < len(self.inventory.methods):
                        self.method_index = idx
                        self.state = "list"
                        self.software_index = 0
                        self.list_filter = "all"
                        self.search = ""
                elif ch == ord("r"):
                    self.inventory = self.refresh_cb()
                    self.detail_cache.clear()
                    self.method_index = 0
                    self.software_index = 0
            elif self.state == "list":
                method = self.current_method()
                items = self.filtered_software(method)
                page = max(1, stdscr.getmaxyx()[0] - 7)
                if ch in (27, ord("q")):
                    self.state = "methods"
                elif ch in (curses.KEY_UP, ord("k")):
                    self.software_index = max(0, self.software_index - 1)
                elif ch in (curses.KEY_DOWN, ord("j")):
                    self.software_index = min(max(0, len(items) - 1), self.software_index + 1)
                elif ch == curses.KEY_PPAGE:
                    self.software_index = max(0, self.software_index - page)
                elif ch == curses.KEY_NPAGE:
                    self.software_index = min(max(0, len(items) - 1), self.software_index + page)
                elif ch == curses.KEY_HOME:
                    self.software_index = 0
                elif ch == curses.KEY_END:
                    self.software_index = max(0, len(items) - 1)
                elif ch in (10, 13, curses.KEY_ENTER) and items:
                    self.state = "detail"
                    self.detail_scroll = 0
                elif ch == ord("/"):
                    self.ask_search(stdscr)
                elif ch == ord("f"):
                    order = self.filter_order(method)
                    if len(order) > 1:
                        self.list_filter = order[(order.index(self.list_filter) + 1) % len(order)] if self.list_filter in order else "all"
                        self.software_index = 0
                elif ch == ord("e"):
                    json_path, md_path = export_inventory(self.inventory)
                    self.flash(stdscr, f"已导出：{md_path}", "success")
                elif ch == ord("r"):
                    self.inventory = self.refresh_cb()
                    self.detail_cache.clear()
                    self.method_index = min(self.method_index, len(self.inventory.methods) - 1)
                    self.software_index = 0
            else:
                sw = self.current_software()
                if not sw:
                    self.state = "list"
                    continue
                page = max(1, stdscr.getmaxyx()[0] - 7)
                if ch in (27,):
                    self.state = "list"
                elif ch == ord("q"):
                    return
                elif ch in (curses.KEY_UP, ord("k")):
                    self.detail_scroll = max(0, self.detail_scroll - 1)
                elif ch in (curses.KEY_DOWN, ord("j")):
                    self.detail_scroll += 1
                elif ch == curses.KEY_PPAGE:
                    self.detail_scroll = max(0, self.detail_scroll - page)
                elif ch == curses.KEY_NPAGE:
                    self.detail_scroll += page
                elif ch == curses.KEY_HOME:
                    self.detail_scroll = 0
                elif ch == curses.KEY_END:
                    self.detail_scroll = 10**9
                elif ch == ord("e"):
                    path = export_software(sw, self.inventory)
                    self.flash(stdscr, f"已导出：{path}", "success")
                elif ch == ord("d"):
                    self.perform_cleanup(stdscr, sw)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def print_progress(message: str) -> None:
    if sys.stderr.isatty() or sys.stdout.isatty():
        print(f"\r\033[2K  {message}…", end="", file=sys.stderr, flush=True)


def summary_text(inv: Inventory) -> str:
    lines = [
        f"{APP_NAME} {APP_VERSION}",
        f"主机: {inv.hostname}",
        f"系统: {inv.os_name}",
        f"时间: {inv.generated_at}",
        f"耗时: {inv.scan_seconds:.2f}s",
        "",
    ]
    for m in inv.methods:
        lines.append(f"{m.title}: {len(m.softwares)}")
    lines.append(f"忽略的共享配置目录: {len(inv.ignored_userdata)}")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Linux terminal software inventory and cleanup assistant")
    parser.add_argument("--json", action="store_true", help="print complete inventory as JSON")
    parser.add_argument("--summary", action="store_true", help="print a short scan summary")
    parser.add_argument("--export", metavar="DIR", help="write inventory JSON and Markdown to DIR")
    parser.add_argument("--self-test", action="store_true", help="run scan and print consistency checks")
    parser.add_argument("--version", action="version", version=f"%(prog)s {APP_VERSION}")
    args = parser.parse_args(argv)

    if args.json or args.summary or args.export or args.self_test:
        inv = scan_inventory(print_progress)
        if args.json:
            print(json.dumps(inventory_to_dict(inv), ensure_ascii=False, indent=2))
        elif args.summary:
            print(summary_text(inv))
        elif args.self_test:
            print(summary_text(inv))
            total = len(inv.all_software())
            assert total > 0, "inventory is empty"
            assert any(s.package_name for s in inv.all_software()), "no package manager entries"
            residual = [s for s in inv.all_software() if s.is_residual]
            print(f"CHECK ok: {total} entries, {len(residual)} residual groups")
        elif args.export:
            target = Path(args.export).expanduser()
            target.mkdir(parents=True, exist_ok=True)
            stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            jp = target / f"software-inventory-{stamp}.json"
            mp = target / f"software-inventory-{stamp}.md"
            jp.write_text(json.dumps(inventory_to_dict(inv), ensure_ascii=False, indent=2), encoding="utf-8")
            md = [f"# Linux 软件清单\n", f"- 主机：{inv.hostname}", f"- 系统：{inv.os_name}", f"- 时间：{inv.generated_at}", ""]
            for m in inv.methods:
                md.append(f"## {m.title}（{len(m.softwares)}）\n")
                for s in m.softwares:
                    md.append(f"- {'⚠ 残留' if s.is_residual else s.status_label}: `{s.name}` {s.version}")
                    for a in s.data_artifacts + s.cache_artifacts:
                        md.append(f"  - {a.role}: `{a.path}`")
                md.append("")
            mp.write_text("\n".join(md) + "\n", encoding="utf-8")
            print(f"JSON: {jp}\nMarkdown: {mp}")
        return 0

    # Ensure UTF-8 handling even when the remote shell locale is POSIX.
    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error:
        try:
            locale.setlocale(locale.LC_ALL, "C.UTF-8")
        except locale.Error:
            pass
    os.environ.setdefault("ESCDELAY", "25")

    inventory = scan_inventory(print_progress)
    if sys.stderr.isatty() or sys.stdout.isatty():
        print("\r\033[2K", end="", file=sys.stderr)
    try:
        curses.wrapper(lambda scr: TUI(inventory, lambda: scan_inventory(print_progress)).loop(scr))
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
