"""Read-only check of rebuilt static assets against the Git index.

Run after `cd web && npm run build`. Unlike `git diff --exit-code` alone,
this also catches newly generated files (even ignored files) and symlinks.
The index is intentional: developers can stage the matching bundle before
committing, while CI compares against the clean checkout of that commit.
"""
import argparse
import os
import subprocess
from pathlib import Path


STATIC = Path("worktogether/static")


def git_paths(root, *args):
    result = subprocess.run(["git", *args, "--", str(STATIC)], cwd=root,
                            capture_output=True, check=True, timeout=30)
    return {os.fsdecode(path) for path in result.stdout.split(b"\0") if path}


def check_static(root):
    """Return diagnostic strings. Do not stage, build, delete, or edit files."""
    root = Path(root).resolve()
    indexed = git_paths(root, "ls-files", "--cached", "-z")
    if str(STATIC / "index.html") not in indexed:
        return ["Missing indexed static/index.html; build and stage the full static bundle."]
    target = root / STATIC
    if (root / "worktogether").is_symlink() or target.is_symlink() or not target.is_dir():
        return ["Static output must be a real directory, not a symlink or missing path."]
    issues = []
    actual = set()
    def cannot_walk(error):
        raise error

    for directory, dirs, files in os.walk(target, followlinks=False, onerror=cannot_walk):
        for name in list(dirs):
            path = Path(directory) / name
            if path.is_symlink():
                issues.append("Symlink directory: " + str(path.relative_to(root)))
                dirs.remove(name)
        for name in files:
            path = Path(directory) / name
            rel = str(path.relative_to(root))
            actual.add(rel)
            if path.is_symlink() or not path.is_file():
                issues.append("Non-regular asset: " + rel)
    issues.extend("Unstaged/new asset: " + path for path in sorted(actual - indexed))
    issues.extend("Missing asset: " + path for path in sorted(indexed - actual))
    changed = git_paths(root, "diff", "--name-only", "--no-ext-diff", "-z")
    issues.extend("Asset differs from index: " + path for path in sorted(changed - (indexed - actual)))
    return issues


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    try:
        issues = check_static(args.root)
    except (OSError, subprocess.SubprocessError):
        print("Cannot inspect the Git index/static bundle; check the repository and Git installation.")
        return 2
    if issues:
        print("Static bundle is out of sync:")
        for issue in issues:
            print("- " + issue)
        print("Rebuild with `cd web && npm run build`, review, then stage worktogether/static/.")
        return 1
    print("Static bundle matches the Git index (including newly generated files).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
