"""Check/sync marketplace hashes against Git-staged plugin bytes, not checkout EOLs."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def sync_checksums(root: Path, *, write: bool = False) -> list[str]:
    index_path = root / "marketplace" / "plugins-index.json"
    data = json.loads(index_path.read_text(encoding="utf-8"))
    mismatches = []
    for entry in data["plugins"]:
        filename = entry["file"]
        # External publishers own these bytes; never fetch or rewrite their hashes.
        if filename.startswith(("https://", "http://")):
            continue
        path = PurePosixPath(filename)
        if (not filename or path.is_absolute() or ".." in path.parts
                or "\\" in filename or ":" in filename or path.suffix != ".py"):
            raise ValueError(f"Invalid marketplace file: {filename!r}")
        repo_path = f"marketplace/{path.as_posix()}"
        result = subprocess.run(
            ["git", "show", f":{repo_path}"], cwd=root,
            capture_output=True, check=False,
        )
        if result.returncode:
            raise ValueError(f"Cannot read staged {repo_path}; git add the plugin first")
        digest = hashlib.sha256(result.stdout).hexdigest()
        if entry.get("sha256") != digest:
            mismatches.append(entry["id"])
            entry["sha256"] = digest
    # Only write after every file was validated; a missing file leaves the index intact.
    if write and mismatches:
        index_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8", newline="\n",
        )
    return mismatches


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--write", action="store_true", help="Update hashes in the working index JSON")
    modes.add_argument("--check", action="store_true", help="Check only (the default)")
    args = parser.parse_args()
    try:
        mismatches = sync_checksums(ROOT, write=args.write)
    except (ValueError, KeyError, OSError) as exc:
        parser.exit(2, f"{exc}\n")
    if mismatches:
        print(("Updated: " if args.write else "Checksum mismatch: ") + ", ".join(mismatches))
        if not args.write:
            print("Stage plugin changes, run this script with --write, then stage plugins-index.json.")
            return 1
    else:
        print("All repository-hosted marketplace checksums match Git-staged bytes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
