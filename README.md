# Knowledge Base Conventions

A self-contained, PARA-centered Obsidian governance kit: research, filing rules, lifecycle guidance and a deterministic, conflict-aware documentation sync. Complete copies live in the vault **and** in a public installable agent skill.

## Read the rules

- [Governance index](vault/00_meta/README.md)
- [Conventions](vault/00_meta/Knowledge%20Base%20Conventions.md)
- [Research and decisions](vault/00_meta/Knowledge%20Base%20Research%20and%20Decisions.md)
- [PARA lifecycle](vault/00_meta/Knowledge%20Base%20Lifecycle.md)
- [Sync, updates and recovery](vault/00_meta/Knowledge%20Base%20Sync.md)

Vault copies use Obsidian wikilinks; this index provides normal GitHub navigation. Five public governance notes are managed. The generated sync-status note and optional local-conventions note are local only.

## Why duplicate the documentation?

Because offline, self-contained copies are useful. The problem is independent, untraceable authoring—not duplication itself.

```text
knowledge-base-conventions (canonical, reviewed docs)
  ├── immutable revision + hashes -> agent-skills/personal-vault-para
  │                                  └── GitHub-installed skill + offline references
  └── read-only, validated Markdown pull -> personal vault / 00_meta
```

The skill bundle is a declared release snapshot. The vault can track a newer source revision; agents read current vault governance and report snapshot limitations. No runtime git submodule is needed, and no scheduled update executes newly downloaded code.

## Install the public skill

```bash
hermes skills tap add fabzter/agent-skills
hermes skills install fabzter/agent-skills/personal-vault-para --yes
```

Use `--force` when intentionally reinstalling an already installed copy, then verify installed contents. The skill is agent-neutral Markdown; Hermes is the deployment used here. Its source is [fabzter/agent-skills](https://github.com/fabzter/agent-skills/tree/main/skills/personal-vault-para).

## Configure the local mirror

Requires Python 3.10+ and a running, responsive Obsidian installation with the official CLI. No third-party Python libraries, public API token or LLM is required.

1. Read [config.example.json](config.example.json).
2. Create a **private** `~/.config/knowledge-base-sync/config.json` with your exact vault name, expected vault path, official CLI path, state directory and approved note paths. Do not commit it.
3. Run a dry-run, then apply:

```bash
python3 sync_engine.py --dry-run
python3 sync_engine.py
```

The installed skill's `scripts/sync_vault.py` is the same reviewed executable and accepts the same arguments. For Hermes no-agent cron, copy the reviewed `scripts/cron_entry.py` wrapper into the profile's `scripts` directory: entry points outside that sandbox are refused. The wrapper runs the installed executable with a bounded process lifetime. Scheduling is opt-in; the sync uses POSIX locking on macOS/Linux.

## Safety properties

- Resolve one immutable upstream commit; verify the entire manifest and SHA-256 payload before writes.
- Destination authority comes from the **local** fixed allowlist, not the remote manifest.
- Explicit vault-path identity check; official CLI writes and full readback verification.
- Local changes/deletions and unknown pre-existing content cause conflicts, never silent overwrites.
- Exclusive lock, private backups/journal and per-note recovery checkpoints.
- No deletion, automatic organization, vault-data upload, account switching or filesystem write fallback.
- No-op is silent; failure is nonzero with persistent local diagnostics.
- Private account settings and local conventions are never exported.

This is not a multi-file database transaction: interrupted progress is recoverable and accurately recorded. It also cannot prove a new scheduled check succeeded merely because the last successful status note exists.

## Development and release

```bash
python3 -m unittest discover -s tests -v
python3 tools/bundle.py manifest --check
```

After changing a canonical note, regenerate `manifest.json`, test, review and commit. Export a public skill release only from a clean committed checkout:

```bash
python3 tools/bundle.py manifest
# Review, test and commit the canonical change first.
python3 tools/bundle.py export --output /path/to/agent-skills/skills/personal-vault-para
python3 tools/bundle.py verify --bundle /path/to/agent-skills/skills/personal-vault-para --remote
```

The exporter ships real references and reviewed scripts plus `references/upstream-lock.json`. Commit/push that generated bundle to the skill repository, then reinstall it from GitHub. CI checks manifests, public-reference integrity and the pinned skill bundle.

## PARA: complete, not complicated

This system already has Projects, Areas, Resources and Archives. Extra support folders are implementation choices. A more mature practice adds clear project outcomes, Area standards, sustainable reviews, reusable learning and deliberate archiving—not a deeper hierarchy. The lifecycle guide describes those conventions; nothing here automatically migrates existing notes.

## License and source material

Original code and documentation are MIT licensed. Linked third-party articles remain their authors' work; this repository records short findings and implementation decisions, not copies of those articles. Community preferences are identified as such, not presented as universal consensus.
