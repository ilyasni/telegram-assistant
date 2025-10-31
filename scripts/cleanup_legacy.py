#!/usr/bin/env python3
"""
Автоматическая очистка legacy кода по меткам @deprecated remove_by=.

[C7-ID: CODE-CLEANUP-019] Context7 best practice: автоматическое удаление deprecated кода
"""

import re
import sys
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import argparse

REPO_ROOT = Path(__file__).parent.parent
LEGACY_DIR = REPO_ROOT / "legacy"


def parse_deprecation_metadata(file_path: Path) -> Optional[Dict[str, str]]:
    """
    Парсит метаданные из заголовка файла:
    @deprecated since=YYYY-MM-DD remove_by=YYYY-MM-DD
    """
    try:
        content = file_path.read_text(encoding="utf-8")
        # Ищем метку @deprecated в первых 50 строках
        lines = content.split("\n")[:50]
        header = "\n".join(lines)
        
        match = re.search(
            r"@deprecated\s+since=(\d{4}-\d{2}-\d{2})\s+remove_by=(\d{4}-\d{2}-\d{2})",
            header,
        )
        
        if match:
            since = match.group(1)
            remove_by = match.group(2)
            
            # Ищем описание
            reason_match = re.search(r"Reason:\s*(.+?)(?:\n|Replacement:)", header, re.DOTALL)
            reason = reason_match.group(1).strip() if reason_match else "Not specified"
            
            replacement_match = re.search(r"Replacement:\s*(.+?)(?:\n|$)", header, re.DOTALL)
            replacement = replacement_match.group(1).strip() if replacement_match else None
            
            return {
                "since": since,
                "remove_by": remove_by,
                "reason": reason,
                "replacement": replacement,
            }
    except Exception as e:
        print(f"⚠️  Error parsing {file_path}: {e}")
    
    return None


def find_legacy_files() -> List[tuple[Path, Dict[str, str]]]:
    """Найти все файлы в legacy/ с метками @deprecated."""
    legacy_files = []
    
    for py_file in LEGACY_DIR.rglob("*.py"):
        metadata = parse_deprecation_metadata(py_file)
        if metadata:
            legacy_files.append((py_file, metadata))
    
    return legacy_files


def check_removal_candidates(dry_run: bool = True) -> List[tuple[Path, Dict[str, str]]]:
    """
    Проверить файлы, которые можно удалить.
    
    Returns список файлов, у которых remove_by <= today + 3 days
    """
    today = datetime.now().date()
    warning_threshold = today + timedelta(days=3)
    
    legacy_files = find_legacy_files()
    candidates = []
    
    for file_path, metadata in legacy_files:
        try:
            remove_by = datetime.strptime(metadata["remove_by"], "%Y-%m-%d").date()
            
            if remove_by <= today:
                candidates.append((file_path, metadata, "READY"))
            elif remove_by <= warning_threshold:
                candidates.append((file_path, metadata, "WARNING"))
        except ValueError:
            print(f"⚠️  Invalid date format in {file_path}: {metadata['remove_by']}")
    
    return candidates


def main():
    parser = argparse.ArgumentParser(description="Cleanup legacy code based on @deprecated markers")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Dry run (don't delete files, just report)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force deletion (disable dry-run)",
    )
    
    args = parser.parse_args()
    dry_run = args.dry_run and not args.force
    
    if not LEGACY_DIR.exists():
        print("ℹ️  No legacy/ directory found")
        return 0
    
    print("=" * 60)
    print("🧹 Legacy Code Cleanup")
    print("=" * 60)
    print()
    
    if dry_run:
        print("🔍 DRY RUN MODE (use --force to actually delete)")
        print()
    
    candidates = check_removal_candidates(dry_run=dry_run)
    
    if not candidates:
        print("✅ No legacy files ready for removal")
        return 0
    
    ready = [c for c in candidates if c[2] == "READY"]
    warnings = [c for c in candidates if c[2] == "WARNING"]
    
    if ready:
        print("🔴 READY FOR REMOVAL:")
        print()
        for file_path, metadata, _ in ready:
            rel_path = file_path.relative_to(REPO_ROOT)
            print(f"  - {rel_path}")
            print(f"    Deprecated: {metadata['since']}")
            print(f"    Remove by: {metadata['remove_by']}")
            print(f"    Reason: {metadata['reason']}")
            if metadata.get("replacement"):
                print(f"    Replacement: {metadata['replacement']}")
            print()
            
            if not dry_run:
                try:
                    file_path.unlink()
                    print(f"    ✓ Deleted")
                except Exception as e:
                    print(f"    ❌ Error deleting: {e}")
            print()
    
    if warnings:
        print("🟡 WARNING: Removal in 3 days or less:")
        print()
        for file_path, metadata, _ in warnings:
            rel_path = file_path.relative_to(REPO_ROOT)
            remove_by = datetime.strptime(metadata["remove_by"], "%Y-%m-%d").date()
            days_left = (remove_by - datetime.now().date()).days
            print(f"  - {rel_path} (removes in {days_left} days)")
        print()
    
    if not dry_run and ready:
        print("=" * 60)
        print(f"✅ Cleaned up {len(ready)} legacy file(s)")
        print("=" * 60)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())

