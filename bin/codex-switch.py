#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

try:
    import curses
except ImportError:
    curses = None


DEFAULT_RUNTIME_ROOT = Path.home() / ".codex-switch"
DEFAULT_ACTIVE_DIR = Path.home() / ".codex"
DEFAULT_SOURCE_ROOT = DEFAULT_RUNTIME_ROOT / "profiles"
AUTH_FILE = "auth.json"
CONFIG_CANDIDATES = ("config.json", "config.toml")


def find_config_file(directory: Path) -> Path | None:
    for name in CONFIG_CANDIDATES:
        p = directory / name
        if p.is_file():
            return p
    return None


def extract_base_url(path: Path) -> str | None:
    try:
        if path.name.endswith(".json"):
            data = json.loads(path.read_text())
            return data.get("base_url")
        # TOML-like: handle lines like `base_url = "..."` or `base_url = '...'`
        text = path.read_text()
        # Accept optionally-escaped quotes (e.g. \"https://...\") and capture inner value
        m = re.search(r"^\s*base_url\s*=\s*(?:\\?[\"'])(.*?)(?:\\?[\"'])", text, flags=re.MULTILINE)
        if m:
            val = m.group(1)
            # Normalize by removing any backslash escapes around quotes inside value
            return val.replace('\\"', '"').replace("\\'", "'")
    except Exception:
        return None
    return None


def update_toml_base_url(path: Path, new_value: str) -> None:
    text = path.read_text()
    # Normalize any existing escaped quotes inside the base_url value, then
    # replace the inner value with the provided new_value and write a clean
    # double-quoted line (no backslash escapes).
    pattern = re.compile(r"(^\s*base_url\s*=\s*)(?:\\?[\"'])(.*?)(?:\\?[\"'])", flags=re.MULTILINE)

    def _normalize_and_replace(m: re.Match) -> str:
        prefix = m.group(1)
        inner = m.group(2)
        # unescape any escaped quotes inside
        inner = inner.replace('\\"', '"').replace("\\'", "'")
        # return new clean quoted line with new_value
        return f"{prefix}\"{new_value}\""

    if pattern.search(text):
        new_text = pattern.sub(_normalize_and_replace, text)
    else:
        new_text = text.rstrip() + "\nbase_url = \"" + new_value + "\"\n"
    path.write_text(new_text)


@dataclass(frozen=True)
class ProfileInfo:
    name: str
    source: Path
    store: Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codex-switch",
        description="Switch Codex auth/config profiles.",
    )
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=DEFAULT_RUNTIME_ROOT,
        help="Root directory for stored profiles and backups.",
    )
    parser.add_argument(
        "--active-dir",
        type=Path,
        default=DEFAULT_ACTIVE_DIR,
        help="Directory that receives the active auth.json and config.toml.",
    )
    parser.add_argument(
        "--source-root",
        type=Path,
        default=DEFAULT_SOURCE_ROOT,
        help="Directory that contains profile subfolders.",
    )

    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("list", help="List known profiles.")
    subparsers.add_parser("status", help="Show active profile and imported profiles.")

    import_parser = subparsers.add_parser(
        "import",
        help="Import one or more profiles into the local profile store.",
    )
    import_parser.add_argument(
        "profiles",
        nargs="*",
        help="Profiles to import. Defaults to all known profiles.",
    )
    import_parser.add_argument(
        "--refresh",
        action="store_true",
        help="Re-import even if the profile already exists in the store.",
    )

    switch_parser = subparsers.add_parser("switch", help="Switch the active files.")
    switch_parser.add_argument("profile", help="Profile name to activate.")
    switch_parser.add_argument(
        "--refresh",
        action="store_true",
        help="Re-import the profile from its source before switching.",
    )
    switch_parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Do not back up the current active files before switching.",
    )

    return parser


def profile_store_dir(runtime_root: Path) -> Path:
    return runtime_root / "profiles"


def backup_root(runtime_root: Path) -> Path:
    return runtime_root / "backups"


def discover_profiles(source_root: Path | None, runtime_root: Path, active_dir: Path) -> dict[str, ProfileInfo]:
    profiles: dict[str, ProfileInfo] = {
        "active": ProfileInfo(name="active", source=active_dir, store=profile_store_dir(runtime_root) / "active"),
    }
    if source_root and source_root.is_dir():
        for source_dir in sorted(path for path in source_root.iterdir() if path.is_dir()):
            profiles[source_dir.name] = ProfileInfo(
                name=source_dir.name,
                source=source_dir,
                store=profile_store_dir(runtime_root) / source_dir.name,
            )
    return profiles


def ensure_parent(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def require_profile_files(directory: Path) -> None:
    missing = []
    if not (directory / AUTH_FILE).is_file():
        missing.append(AUTH_FILE)
    if find_config_file(directory) is None:
        missing.append("config.json|config.toml")
    if missing:
        raise FileNotFoundError(f"Missing {', '.join(missing)} in {directory}")


def copy_profile(source: Path, destination: Path) -> None:
    require_profile_files(source)
    ensure_parent(destination)
    # copy auth.json
    shutil.copy2(source / AUTH_FILE, destination / AUTH_FILE)

    # handle config: prefer JSON merge for config.json, otherwise copy toml
    src_config = find_config_file(source)
    if src_config is None:
        raise FileNotFoundError(f"No config file found in {source}")
    dest_config = find_config_file(destination)

    # Merge base_url when possible; otherwise copy source config file.
    src_base = extract_base_url(src_config)
    if dest_config and dest_config.is_file():
        # If we have a base_url in the source, update destination's base_url
        if src_base is not None:
            if dest_config.name.endswith(".json"):
                try:
                    dest_data = json.loads(dest_config.read_text())
                    dest_data["base_url"] = src_base
                    dest_config.write_text(json.dumps(dest_data, indent=2, ensure_ascii=False))
                except Exception:
                    # fallback to overwrite
                    shutil.copy2(src_config, destination / src_config.name)
            else:
                # dest is toml-like: update or append base_url line
                try:
                    update_toml_base_url(dest_config, src_base)
                except Exception:
                    shutil.copy2(src_config, destination / src_config.name)
        else:
            # no base_url in source: do nothing to destination
            pass
    else:
        # no destination config exists: copy source config as-is
        shutil.copy2(src_config, destination / src_config.name)


def import_profile(profile: ProfileInfo, refresh: bool = False) -> None:
    # If the profile's source and store are the same directory (merged layout),
    # there's nothing to copy.
    try:
        if profile.source.resolve() == profile.store.resolve():
            return
    except Exception:
        pass
    if profile.store.is_dir() and not refresh:
        return
    copy_profile(profile.source, profile.store)


def import_profiles(profiles: Iterable[ProfileInfo], refresh: bool = False) -> None:
    for profile in profiles:
        import_profile(profile, refresh=refresh)


def profile_digest(directory: Path) -> str | None:
    if not directory.is_dir():
        return None
    hasher = hashlib.sha256()
    auth_path = directory / AUTH_FILE
    if not auth_path.is_file():
        return None
    hasher.update(AUTH_FILE.encode("utf-8"))
    hasher.update(b"\0")
    hasher.update(auth_path.read_bytes())
    hasher.update(b"\0")
    config_path = find_config_file(directory)
    if config_path is None or not config_path.is_file():
        return None
    hasher.update(config_path.name.encode("utf-8"))
    hasher.update(b"\0")
    hasher.update(config_path.read_bytes())
    hasher.update(b"\0")
    return hasher.hexdigest()


def active_profile_name(profiles: dict[str, ProfileInfo], active_dir: Path) -> str | None:
    active_hash = profile_digest(active_dir)
    if active_hash is None:
        return None
    for profile in profiles.values():
        if profile_digest(profile.store) == active_hash:
            return profile.name
    return None


def backup_active(active_dir: Path, backup_dir: Path, label: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = backup_dir / f"{timestamp}-{label}"
    ensure_parent(backup_path)
    # Copy auth file
    auth_src = active_dir / AUTH_FILE
    if auth_src.is_file():
        shutil.copy2(auth_src, backup_path / AUTH_FILE)
    # Copy config file if present (support config.json or config.toml)
    cfg_src = find_config_file(active_dir)
    if cfg_src is not None and cfg_src.is_file():
        shutil.copy2(cfg_src, backup_path / cfg_src.name)

    backups = sorted(
        (path for path in backup_dir.iterdir() if path.is_dir()),
        key=lambda path: path.name,
        reverse=True,
    )
    for old_backup in backups[5:]:
        shutil.rmtree(old_backup)
    return backup_path


def list_profiles(profiles: dict[str, ProfileInfo], runtime_root: Path, source_root: Path | None, active_dir: Path) -> int:
    print(f"active-dir: {active_dir}")
    print(f"runtime-root: {runtime_root}")
    print(f"source-root: {source_root if source_root is not None else '(not set)'}")
    for name, profile in profiles.items():
        stored = "yes" if profile.store.is_dir() else "no"
        print(f"{name}: source={profile.source} stored={stored} store={profile.store}")
    return 0


def status(profiles: dict[str, ProfileInfo], runtime_root: Path, active_dir: Path) -> int:
    current = active_profile_name(profiles, active_dir)
    if current:
        print(f"active: {current}")
    else:
        print("active: unknown")
    print(f"active-dir: {active_dir}")
    print(f"runtime-root: {runtime_root}")
    for name, profile in profiles.items():
        print(f"{name}: {'ready' if profile.store.is_dir() else 'missing'} -> {profile.store}")
    return 0


def switch_profile(profile: ProfileInfo, active_dir: Path, backup_dir: Path, refresh: bool = False, no_backup: bool = False) -> int:
    import_profile(profile, refresh=refresh)
    if not profile.store.is_dir():
        raise FileNotFoundError(f"Profile store is missing: {profile.store}")
    if not no_backup:
        backup_path = backup_active(active_dir, backup_dir, profile.name)
        print(f"backup: {backup_path}")
    copy_profile(profile.store, active_dir)
    print(f"active: {profile.name}")
    print(f"active-dir: {active_dir}")
    return 0


def handle_import(profiles: dict[str, ProfileInfo], profile_names: list[str], refresh: bool) -> int:
    if not profile_names:
        profile_names = [name for name in profiles.keys() if name != "active"]
    unknown = [name for name in profile_names if name not in profiles]
    if unknown:
        raise SystemExit(f"Unknown profile(s): {', '.join(unknown)}")
    import_profiles((profiles[name] for name in profile_names), refresh=refresh)
    for name in profile_names:
        print(f"imported: {name}")
    return 0


def prompt_choice(prompt: str, options: list[str]) -> int:
    while True:
        print(prompt)
        for index, option in enumerate(options, start=1):
            print(f"  {index}) {option}")
        raw_choice = input("Select a number: ").strip()
        try:
            choice = int(raw_choice)
        except ValueError:
            print("Please enter a number.")
            continue
        if 1 <= choice <= len(options):
            return choice - 1
        print("Choice out of range.")


def arrow_choice(prompt: str, options: list[str]) -> int:
    if "curses" not in globals() or curses is None:
        return prompt_choice(prompt, options)

    def _draw(screen, selected_index: int) -> None:
        screen.clear()
        screen.addstr(0, 0, prompt)
        screen.addstr(1, 0, "Use Up/Down arrows, Enter to select, q to quit.")
        for index, option in enumerate(options, start=0):
            prefix = "> " if index == selected_index else "  "
            line = f"{prefix}{index + 1}) {option}"
            if index == selected_index:
                screen.attron(curses.A_REVERSE)
                screen.addstr(index + 3, 0, line)
                screen.attroff(curses.A_REVERSE)
            else:
                screen.addstr(index + 3, 0, line)
        screen.refresh()

    def _run(screen) -> int:
        curses.curs_set(0)
        screen.keypad(True)
        selected_index = 0
        while True:
            _draw(screen, selected_index)
            key = screen.getch()
            if key in (curses.KEY_UP, ord("k")):
                selected_index = (selected_index - 1) % len(options)
            elif key in (curses.KEY_DOWN, ord("j")):
                selected_index = (selected_index + 1) % len(options)
            elif key in (curses.KEY_ENTER, 10, 13):
                return selected_index
            elif key in (ord("q"), 27):
                raise KeyboardInterrupt

    try:
        return curses.wrapper(_run)
    except KeyboardInterrupt:
        return 0
    except Exception:
        return prompt_choice(prompt, options)


def interactive_mode(profiles: dict[str, ProfileInfo], runtime_root: Path, active_dir: Path, backup_dir: Path, source_root: Path | None) -> int:
    selectable_profiles = [profile for name, profile in profiles.items() if name != "active"]
    if not selectable_profiles:
        print("No switchable profiles found.")
        return 1

    print()
    print("Codex Switch")
    print(f"active-dir: {active_dir}")
    print(f"runtime-root: {runtime_root}")
    print(f"source-root: {source_root if source_root is not None else '(not set)'}")

    profile_index = arrow_choice(
        "Choose a profile to switch:",
        ["Do nothing / Quit"] + [f"{profile.name}  ({profile.source})" for profile in selectable_profiles],
    )
    if profile_index == 0:
        return 0
    profile_index -= 1
    selected_profile = selectable_profiles[profile_index]
    return switch_profile(selected_profile, active_dir, backup_dir)


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    runtime_root = args.runtime_root
    active_dir = args.active_dir
    source_root = args.source_root
    profiles = discover_profiles(source_root, runtime_root, active_dir)
    backup_dir = backup_root(runtime_root)

    if args.command is None:
        if not sys.stdin.isatty():
            return status(profiles, runtime_root, active_dir)
        return interactive_mode(profiles, runtime_root, active_dir, backup_dir, source_root)

    command = args.command

    if command == "list":
        return list_profiles(profiles, runtime_root, source_root, active_dir)
    if command == "status":
        return status(profiles, runtime_root, active_dir)
    if command == "import":
        return handle_import(profiles, args.profiles, args.refresh)
    if command == "switch":
        if args.profile not in profiles:
            raise SystemExit(f"Unknown profile: {args.profile}")
        return switch_profile(profiles[args.profile], active_dir, backup_dir, refresh=args.refresh, no_backup=args.no_backup)

    raise SystemExit("Unsupported command")


if __name__ == "__main__":
    raise SystemExit(main())