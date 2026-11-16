#!/usr/bin/env python3
"""
dedupe_and_organize.py
Find duplicate files (by content), keep the newest, optionally organize, and cache hashes.

SAFE BY DEFAULT: Dry-run unless --apply is given.
"""
import argparse
import csv
import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from time import localtime, strftime
from typing import Dict, List, Tuple, Optional

CHUNK_SIZE = 1024 * 1024
CACHE_VERSION = 1

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None

try:
    from send2trash import send2trash
except ImportError:
    send2trash = None

class Palette:
    def __init__(self, enable: bool):
        if enable:
            self.dim = "\033[2m"
            self.bold = "\033[1m"
            self.green = "\033[92m"
            self.yellow = "\033[93m"
            self.red = "\033[91m"
            self.blue = "\033[94m"
            self.reset = "\033[0m"
        else:
            self.dim = self.bold = self.green = self.yellow = self.red = self.blue = self.reset = ""

def want_color(no_color_flag: bool) -> bool:
    if no_color_flag:
        return False
    return sys.stdout.isatty()

@dataclass
class FileInfo:
    path: Path
    size: int
    mtime: float
    sha256: Optional[str] = None

def iter_files(roots: List[Path], min_size: int = 1, follow_symlinks: bool = False) -> List[FileInfo]:
    files: List[FileInfo] = []
    for root in roots:
        root = root.expanduser().resolve()
        if not root.exists():
            continue
        for dirpath, _, filenames in os.walk(root, followlinks=follow_symlinks):
            for name in filenames:
                p = Path(dirpath) / name
                try:
                    st = p.stat()
                except Exception:
                    continue
                if p.is_file() and st.st_size >= min_size:
                    files.append(FileInfo(path=p, size=st.st_size, mtime=st.st_mtime))
    return files

def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(CHUNK_SIZE):
            h.update(chunk)
    return h.hexdigest()

def _hash_fileinfo(fi: FileInfo) -> Tuple[Path, Optional[str], Optional[str]]:
    try:
        return fi.path, hash_file(fi.path), None
    except Exception as e:
        return fi.path, None, str(e)

def load_cache(cache_path: Optional[Path]) -> Dict[str, dict]:
    if not cache_path or not cache_path.exists():
        return {}
    try:
        with cache_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("version") == CACHE_VERSION:
            return data.get("files", {})
    except Exception:
        pass
    return {}

def save_cache(cache_path: Optional[Path], files_map: Dict[str, dict]):
    if not cache_path:
        return
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache_path.with_suffix(cache_path.suffix + ".tmp")
    data = {"version": CACHE_VERSION, "files": files_map}
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    tmp.replace(cache_path)

def preload_hashes_from_cache(files: List[FileInfo], cache: Dict[str, dict], refresh: bool) -> List[FileInfo]:
    to_hash = []
    for fi in files:
        key = str(fi.path)
        if not refresh and key in cache:
            entry = cache[key]
            if entry.get("size") == fi.size and abs(entry.get("mtime", -1) - fi.mtime) < 1e-6:
                fi.sha256 = entry.get("sha256")
        if fi.sha256 is None:
            to_hash.append(fi)
    return to_hash

def update_cache_from_files(files: List[FileInfo]) -> Dict[str, dict]:
    return {str(fi.path): {"size": fi.size, "mtime": fi.mtime, "sha256": fi.sha256} for fi in files if fi.sha256}

def compute_hashes_threaded(files_to_hash: List[FileInfo], workers: int = 0):
    if not files_to_hash:
        return
    if workers <= 0:
        workers = max(2, os.cpu_count() or 2)
    if tqdm:
        with ThreadPoolExecutor(max_workers=workers) as ex, tqdm(total=len(files_to_hash), unit="file", desc="Hashing") as bar:
            futures = {ex.submit(_hash_fileinfo, fi): fi for fi in files_to_hash}
            for fut in as_completed(futures):
                fi = futures[fut]
                path, h, err = fut.result()
                if h:
                    fi.sha256 = h
                else:
                    print(f"Failed to hash {path}: {err}", file=sys.stderr)
                bar.update(1)
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(_hash_fileinfo, fi): fi for fi in files_to_hash}
            for fut in as_completed(futures):
                fi = futures[fut]
                path, h, err = fut.result()
                if h:
                    fi.sha256 = h
                else:
                    print(f"Failed to hash {path}: {err}", file=sys.stderr)

def group_by_hash(files: List[FileInfo]) -> Dict[str, List[FileInfo]]:
    by_hash: Dict[str, List[FileInfo]] = {}
    for fi in files:
        if fi.sha256:
            by_hash.setdefault(fi.sha256, []).append(fi)
    return by_hash

def choose_kept(files: List[FileInfo], preferred_roots: Optional[List[Path]] = None) -> Tuple[FileInfo, List[FileInfo]]:
    candidates = files
    for root in preferred_roots or []:
        matches = [f for f in files if root in f.path.parents]
        if matches:
            candidates = matches
            break
    kept = max(candidates, key=lambda x: x.mtime)
    dups = [f for f in files if f.path != kept.path]
    return kept, dups

def safe_move(src: Path, dst: Path, dry_run: bool):
    if dry_run:
        print(f"[DRY] MOVE {src} -> {dst}")
        return dst
    dst.parent.mkdir(parents=True, exist_ok=True)
    final = dst
    i = 1
    while final.exists():
        final = dst.with_name(f"{dst.stem}__dup{i}{dst.suffix}")
        i += 1
    shutil.move(str(src), str(final))
    return final

def safe_link(duplicate: Path, target: Path, link_type: str, dry_run: bool,
              keep_existing_links: bool = True) -> bool:
    try:
        entry = duplicate.lstat()
        is_symlink = stat.S_ISLNK(entry.st_mode)
        if keep_existing_links and (is_symlink or entry.st_nlink > 1):
            return True
        if dry_run:
            print(f"[DRY] LINK ({link_type}) {duplicate} -> {target}")
            return True
        target = target.resolve(strict=True)
        if link_type == "hard" and not is_symlink and duplicate.samefile(target):
            return True
        if link_type == "symlink" and is_symlink and duplicate.resolve() == target:
            return True
        # Create the link first; a failed creation or replacement leaves the original intact.
        with tempfile.TemporaryDirectory(prefix=".dedupe-link-", dir=str(duplicate.parent)) as temp_dir:
            link = Path(temp_dir) / duplicate.name
            if link_type == "hard":
                os.link(str(target), str(link))
            else:
                os.symlink(str(target), str(link))
            os.replace(str(link), str(duplicate))
        return True
    except OSError as e:
        print(f"Failed to link {duplicate} -> {target} ({link_type}): {e}", file=sys.stderr)
        return False

def safe_delete(p: Path, dry_run: bool):
    if dry_run:
        print(f"[DRY] DELETE {p}")
        return
    try:
        p.unlink()
    except Exception as e:
        print(f"Failed to delete {p}: {e}", file=sys.stderr)

def safe_trash(p: Path, dry_run: bool):
    if dry_run:
        print(f"[DRY] TRASH {p}")
        return
    if send2trash is None:
        raise RuntimeError("send2trash not installed; pip install send2trash")
    send2trash(str(p))

def organize_kept(kept: FileInfo, mode: str, base_dir: Path, dry_run: bool):
    if mode == "yyyymm":
        t = localtime(kept.mtime)
        sub = Path(strftime("%Y", t)) / strftime("%m", t)
        dst = base_dir / sub / kept.path.name
    elif mode == "ext":
        ext = kept.path.suffix.lower().lstrip(".") or "noext"
        dst = base_dir / "_by_ext" / ext / kept.path.name
    else:
        return None
    return safe_move(kept.path, dst, dry_run)

def make_report(groups: Dict[str, List[FileInfo]], preferred_roots: Optional[List[Path]] = None):
    out = []
    for h, files in groups.items():
        kept, dups = choose_kept(files, preferred_roots)
        out.append({
            "hash": h,
            "kept": str(kept.path),
            "kept_mtime": kept.mtime,
            "duplicates": [str(d.path) for d in dups],
        })
    return {"groups": out}

def write_reports(report: Dict, csv_path: Optional[Path], json_path: Optional[Path]):
    if json_path:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        with json_path.open("w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
    if csv_path:
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["hash", "kept", "kept_mtime", "duplicate"])
            for grp in report["groups"]:
                if not grp["duplicates"]:
                    w.writerow([grp["hash"], grp["kept"], grp["kept_mtime"], ""])
                else:
                    for d in grp["duplicates"]:
                        w.writerow([grp["hash"], grp["kept"], grp["kept_mtime"], d])

def parse_args():
    p = argparse.ArgumentParser(description="Find duplicate files and optionally organize")
    p.add_argument("paths", nargs="+", help="Folders to scan")
    p.add_argument("--min-size", type=int, default=1)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--duplicates-dir", default="Duplicates")
    p.add_argument("--duplicate-action", "--duplicates-action", "--duplication-action",
                   choices=["move", "delete", "trash", "link"], default="move")
    p.add_argument("--link-type", choices=["hard", "symlink"], default="hard",
                   help="Link type for the link action (default: hard); retain a copy in the first matching paths folder")
    links = p.add_mutually_exclusive_group()
    links.add_argument("--keep-existing-links", action="store_true", default=True,
                       help="Leave existing symbolic links and files with multiple hard links untouched in link mode (default)")
    links.add_argument("--no-keep-existing-links", action="store_false", dest="keep_existing_links",
                       help="Allow link mode to replace existing links")
    p.add_argument("--organize", choices=["none", "kept"], default="none")
    p.add_argument("--org-mode", choices=["yyyymm", "ext"], default="yyyymm")
    p.add_argument("--organize-root", default="Organized")
    p.add_argument("--report-csv", default=None)
    p.add_argument("--report-json", default=None)
    p.add_argument("--workers", type=int, default=0)
    p.add_argument("--cache-file", default=".dedupe_cache.json")
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--refresh-cache", action="store_true")
    p.add_argument("--no-color", action="store_true")
    return p.parse_args()

def main():
    args = parse_args()
    p = Palette(want_color(args.no_color))
    roots = [Path(r).expanduser().resolve() for r in args.paths]
    preferred_roots = roots if args.duplicate_action == "link" else None
    files = iter_files(roots, min_size=args.min_size)
    cache_path = None if args.no_cache else Path(args.cache_file).expanduser()
    cache = load_cache(cache_path)
    to_hash = preload_hashes_from_cache(files, cache, refresh=args.refresh_cache)
    compute_hashes_threaded(to_hash, workers=args.workers)
    if not args.no_cache:
        save_cache(cache_path, update_cache_from_files(files))
    groups = group_by_hash(files)
    dup_groups = {h: lst for h, lst in groups.items() if len(lst) > 1}
    duplicates_dir = Path(args.duplicates_dir).expanduser()
    organize_root = Path(args.organize_root).expanduser()
    link_failed = False
    for h, lst in dup_groups.items():
        kept, dups = choose_kept(lst, preferred_roots)
        print(f"Hash: {h[:12]} KEEP: {kept.path}")
        link_target = kept.path
        if args.duplicate_action == "link" and args.organize == "kept":
            link_target = organize_kept(kept, mode=args.org_mode, base_dir=organize_root, dry_run=not args.apply)
            link_target = link_target.resolve()
        for d in dups:
            if args.duplicate_action == "move":
                rel_parent = d.path.parent.name
                dst = duplicates_dir / rel_parent / d.path.name
                safe_move(d.path, dst, dry_run=not args.apply)
            elif args.duplicate_action == "delete":
                safe_delete(d.path, dry_run=not args.apply)
            elif args.duplicate_action == "trash":
                safe_trash(d.path, dry_run=not args.apply)
            elif args.duplicate_action == "link":
                if not safe_link(d.path, link_target, args.link_type, dry_run=not args.apply,
                                 keep_existing_links=args.keep_existing_links):
                    link_failed = True
        if args.organize == "kept" and args.duplicate_action != "link":
            organize_kept(kept, mode=args.org_mode, base_dir=organize_root, dry_run=not args.apply)
    write_reports(make_report(groups, preferred_roots), Path(args.report_csv) if args.report_csv else None, Path(args.report_json) if args.report_json else None)
    return 1 if link_failed else 0

if __name__ == "__main__":
    sys.exit(main())

