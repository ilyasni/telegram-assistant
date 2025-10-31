#!/usr/bin/env python3
"""
Автоматическая инвентаризация мёртвого кода, дубликатов и неиспользуемых импортов.

[C7-ID: CODE-CLEANUP-011] Context7 best practice: автоматический сбор кандидатов на удаление
"""

import json
import subprocess
import csv
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any
import sys

REPO_ROOT = Path(__file__).parent.parent
REPORTS_DIR = REPO_ROOT / "docs" / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)


def run_command(cmd: List[str], cwd: Path = None) -> tuple[str, int]:
    """Выполнить команду и вернуть вывод + код возврата."""
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd or REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout + result.stderr, result.returncode
    except Exception as e:
        return str(e), 1


def run_vulture() -> List[Dict[str, Any]]:
    """Запустить Vulture для поиска мёртвого кода."""
    print("🔍 Running Vulture...")
    output, code = run_command([
        "vulture",
        ".",
        "--min-confidence", "80",
        "--exclude", "tests/",
        "--exclude", "legacy/",
        "--exclude", "migrations/",
        "--exclude", "**/__pycache__/",
    ])
    
    candidates = []
    for line in output.strip().split("\n"):
        if not line or ":" not in line:
            continue
        try:
            parts = line.split(":", 2)
            if len(parts) >= 3:
                file_path, line_num, description = parts[0], parts[1], parts[2]
                candidates.append({
                    "file": file_path,
                    "line": line_num,
                    "description": description.strip(),
                    "priority": "MEDIUM",
                })
        except Exception:
            continue
    
    # Сохранить в CSV
    csv_path = REPORTS_DIR / "dead_code_vulture.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        if candidates:
            writer = csv.DictWriter(f, fieldnames=["file", "line", "description", "priority"])
            writer.writeheader()
            writer.writerows(candidates)
        else:
            f.write("file,line,description,priority\n")
    
    print(f"  ✓ Found {len(candidates)} dead code candidates → {csv_path}")
    return candidates


def run_ruff_unused_imports() -> List[Dict[str, Any]]:
    """Запустить Ruff для поиска неиспользуемых импортов."""
    print("🔍 Running Ruff (unused imports)...")
    output, code = run_command([
        "ruff", "check",
        "--select", "F401,F841,F403",
        "--output-format", "json",
        ".",
    ])
    
    candidates = []
    try:
        if output.strip():
            issues = json.loads(output)
            for issue in issues:
                candidates.append({
                    "file": issue.get("filename", ""),
                    "line": issue.get("location", {}).get("row", ""),
                    "code": issue.get("code", ""),
                    "message": issue.get("message", ""),
                    "priority": "LOW",
                })
    except json.JSONDecodeError:
        # Fallback to text parsing
        for line in output.strip().split("\n"):
            if "F401" in line or "F841" in line or "F403" in line:
                candidates.append({
                    "file": line.split(":")[0] if ":" in line else "",
                    "line": "",
                    "code": "",
                    "message": line,
                    "priority": "LOW",
                })
    
    # Сохранить в CSV
    csv_path = REPORTS_DIR / "unused_imports.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        if candidates:
            writer = csv.DictWriter(f, fieldnames=["file", "line", "code", "message", "priority"])
            writer.writeheader()
            writer.writerows(candidates)
        else:
            f.write("file,line,code,message,priority\n")
    
    print(f"  ✓ Found {len(candidates)} unused import issues → {csv_path}")
    return candidates


def run_jscpd() -> Dict[str, Any]:
    """Запустить JSCPD для поиска дубликатов кода."""
    print("🔍 Running JSCPD (duplicate detection)...")
    output, code = run_command([
        "jscpd",
        "--min-tokens", "80",  # Повышенный порог для снижения шума
        "--threshold", "1%",
        "--languages", "python,javascript",
        "--format", "json",
        "--reporters", "json",
        "--output", str(REPORTS_DIR / "jscpd_report"),
        ".",
    ])
    
    json_path = REPORTS_DIR / "jscpd_report" / "jscpd-report.json"
    duplicates = []
    
    if json_path.exists():
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                clones = data.get("duplicates", [])
                for clone in clones:
                    duplicates.append({
                        "file1": clone.get("firstFile", {}).get("name", ""),
                        "file2": clone.get("secondFile", {}).get("name", ""),
                        "lines": clone.get("lines", ""),
                        "tokens": clone.get("tokens", ""),
                        "priority": "HIGH" if clone.get("tokens", 0) > 100 else "MEDIUM",
                    })
        except Exception as e:
            print(f"  ⚠️  Error parsing JSCPD JSON: {e}")
    
    # Сохранить упрощённый JSON
    simplified_path = REPORTS_DIR / "duplicates.json"
    with open(simplified_path, "w", encoding="utf-8") as f:
        json.dump(duplicates, f, indent=2, ensure_ascii=False)
    
    print(f"  ✓ Found {len(duplicates)} duplicate code blocks → {simplified_path}")
    return {"duplicates": duplicates, "count": len(duplicates)}


def generate_summary_report(
    vulture_results: List[Dict],
    ruff_results: List[Dict],
    jscpd_results: Dict,
) -> None:
    """Сгенерировать сводный отчёт."""
    report_path = REPORTS_DIR / "cleanup_candidates.md"
    
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("# Cleanup Candidates Report\n\n")
        f.write(f"**Generated:** {datetime.now().isoformat()}\n\n")
        
        # HIGH PRIORITY: Точные дубликаты
        f.write("## 🔴 HIGH PRIORITY: Exact Duplicates\n\n")
        high_priority = [d for d in jscpd_results.get("duplicates", []) if d.get("priority") == "HIGH"]
        if high_priority:
            for dup in high_priority[:10]:  # Top 10
                f.write(f"- **{dup['file1']}** ↔ **{dup['file2']}** ({dup.get('tokens', '?')} tokens)\n")
        else:
            f.write("No high-priority duplicates found.\n")
        f.write("\n")
        
        # MEDIUM PRIORITY: Мёртвый код
        f.write("## 🟡 MEDIUM PRIORITY: Dead Code\n\n")
        medium_priority = [v for v in vulture_results if v.get("priority") == "MEDIUM"][:20]
        if medium_priority:
            for item in medium_priority:
                f.write(f"- `{item['file']}:{item['line']}` - {item['description']}\n")
        else:
            f.write("No medium-priority dead code found.\n")
        f.write("\n")
        
        # LOW PRIORITY: Неиспользуемые импорты
        f.write("## 🟢 LOW PRIORITY: Unused Imports\n\n")
        f.write(f"Total unused imports: {len(ruff_results)}\n")
        f.write("(See `unused_imports.csv` for details)\n\n")
        
        # REQUIRES REVIEW: Deprecated код
        f.write("## ⚠️  REQUIRES REVIEW: Deprecated Code\n\n")
        deprecated_files = list(REPO_ROOT.glob("**/*deprecated*.py"))
        deprecated_files.extend(REPO_ROOT.glob("legacy/**/*.py"))
        if deprecated_files:
            for file in deprecated_files:
                f.write(f"- `{file.relative_to(REPO_ROOT)}`\n")
        else:
            f.write("No deprecated files found.\n")
        f.write("\n")
        
        # Статистика
        f.write("## 📊 Statistics\n\n")
        f.write(f"- Dead code candidates: {len(vulture_results)}\n")
        f.write(f"- Unused imports: {len(ruff_results)}\n")
        f.write(f"- Code duplicates: {jscpd_results.get('count', 0)}\n")
    
    print(f"  ✓ Summary report → {report_path}")


def main():
    """Главная функция."""
    print("=" * 60)
    print("🔍 Dead Code Inventory")
    print("=" * 60)
    print()
    
    # Проверка инструментов
    tools = {
        "vulture": "vulture",
        "ruff": "ruff",
        "jscpd": "jscpd",
    }
    
    missing_tools = []
    for name, cmd in tools.items():
        _, code = run_command(["which", cmd] if sys.platform != "win32" else ["where", cmd])
        if code != 0:
            missing_tools.append(name)
    
    if missing_tools:
        print(f"❌ Missing tools: {', '.join(missing_tools)}")
        print("   Install with: pip install vulture ruff jscpd")
        return 1
    
    # Запуск анализа
    vulture_results = run_vulture()
    ruff_results = run_ruff_unused_imports()
    jscpd_results = run_jscpd()
    
    # Генерация отчёта
    generate_summary_report(vulture_results, ruff_results, jscpd_results)
    
    print()
    print("=" * 60)
    print("✅ Inventory complete!")
    print(f"   Reports saved to: {REPORTS_DIR}")
    print("=" * 60)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())

