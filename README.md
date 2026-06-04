# Codex Switch Tool

`codex-switch` is a small WSL/Linux command-line tool for switching Codex auth and
config profiles without putting private files in the repository.

## What it manages

The tool works with exactly two files per profile:

- `auth.json`
- `config.toml`

It keeps the active pair in a configurable active directory and stores reusable
profiles under a separate runtime root.

Default locations:

- Active directory: `~/.codex/`
- Runtime root: `~/.codex-switch/`

The runtime root contains:

- `profiles/` for imported profiles
- `backups/` for snapshots of the active files before switching

## Repository layout

- `bin/codex-switch.py` - the main CLI
- `bin/codex-switch` - a small shell launcher for Linux/WSL
- `.gitignore` - excludes caches and runtime data

## Installation

One-command install from the repository root:

```bash
bash install.sh
```

That creates a symlink in `~/.local/bin/codex-switch` and makes the launcher
executable.

You can also run the Python script directly:

```bash
python3 bin/codex-switch.py --help
```

If you want to install manually, the script does the same thing as:

```bash
chmod +x bin/codex-switch
mkdir -p ~/.local/bin
ln -sf "$PWD/bin/codex-switch" ~/.local/bin/codex-switch
```

## Commands

```bash
codex-switch
codex-switch list
codex-switch status
codex-switch import
codex-switch import personal work
codex-switch switch personal
```

### `list`

Shows the active directory, the runtime root, the source root, and every discovered
profile.

### `status`

Shows which profile is active, if it can be matched to one of the stored profiles,
and whether each profile is ready.

Running `codex-switch` without arguments opens a direct profile picker so you can
choose a profile and switch immediately.

If standard input is not interactive, the command falls back to `codex-switch status`.

In the picker, use Up/Down arrows or `j`/`k`, then press Enter. You can also type
the profile number if arrow keys are not convenient.

The first option is `Do nothing / Quit`, which exits without changing anything.

### `import`

Copies `auth.json` and `config.toml` from one or more source profiles into the local
profile store.

If no profile names are provided, all discovered profiles are imported.

### `switch`

Backs up the current active Codex files, then copies the selected profile into the
active directory.

Use `--no-backup` if you do not want a backup copy.

## Profile discovery

By default, the tool discovers profiles from `~/.codex-switch/sources/`.

You can override that location with `--source-root`.

Each subfolder becomes a profile automatically if it contains both files:

```text
source-root/
  personal/
    auth.json
    config.toml
  work/
    auth.json
    config.toml
```

Then run:

```bash
codex-switch list
codex-switch import personal work
codex-switch switch personal
```

## Options

- `--active-dir` changes where the active `auth.json` and `config.toml` are written.
- `--runtime-root` changes where imported profiles and backups are stored.
- `--source-root` changes where profile subfolders are discovered.
