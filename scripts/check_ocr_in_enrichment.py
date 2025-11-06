#!/usr/bin/env python3
"""
Проверка OCR в post_enrichment на реальных данных.
Context7 best practice: валидация данных через SQL запросы и анализ статистики.

Использование:
    python scripts/check_ocr_in_enrichment.py --limit 10
    python scripts/check_ocr_in_enrichment.py --post-id <uuid>
"""

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone

# Добавляем пути
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    import asyncpg
    import click
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.syntax import Syntax
except ImportError:
    print("⚠️  Установите зависимости: pip install asyncpg click rich")
    sys.exit(1)

console = Console()


async def check_ocr_in_enrichment(
    db_url: str,
    post_id: Optional[str] = None,
    limit: int = 20
) -> Dict[str, Any]:
    """
    Context7: Проверка OCR в post_enrichment таблице.
    
    Проверяет:
    1. Наличие OCR в data->'ocr'->>'text'
    2. Наличие OCR в legacy поле ocr_text
    3. Соответствие между новым и legacy форматами
    4. Статистику по провайдерам
    """
    conn = await asyncpg.connect(db_url)
    
    try:
        if post_id:
            # Проверка конкретного поста
            row = await conn.fetchrow("""
                SELECT 
                    post_id,
                    kind,
                    provider,
                    status,
                    data,
                    ocr_text as legacy_ocr_text,
                    ocr_present,
                    created_at,
                    updated_at
                FROM post_enrichment
                WHERE post_id = $1 AND kind = 'vision'
                ORDER BY updated_at DESC
                LIMIT 1
            """, post_id)
            
            if not row:
                return {
                    "status": "not_found",
                    "message": f"Post {post_id} не найден или не имеет Vision обогащения"
                }
            
            return await _analyze_single_row(row)
        else:
            # Статистика по всем записям
            rows = await conn.fetch("""
                SELECT 
                    post_id,
                    kind,
                    provider,
                    status,
                    data,
                    ocr_text as legacy_ocr_text,
                    ocr_present,
                    created_at,
                    updated_at
                FROM post_enrichment
                WHERE kind = 'vision'
                ORDER BY updated_at DESC
                LIMIT $1
            """, limit)
            
            return await _analyze_multiple_rows(rows)
    
    finally:
        await conn.close()


async def _analyze_single_row(row) -> Dict[str, Any]:
    """Анализ одной записи."""
    data = row['data']
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            data = None
    
    # Извлечение OCR из нового формата
    ocr_data = None
    ocr_text_new = None
    ocr_engine = None
    ocr_confidence = None
    
    if data and isinstance(data, dict):
        ocr_data = data.get('ocr')
        if isinstance(ocr_data, dict):
            ocr_text_new = ocr_data.get('text')
            ocr_engine = ocr_data.get('engine')
            ocr_confidence = ocr_data.get('confidence')
        elif isinstance(ocr_data, str):
            ocr_text_new = ocr_data
    
    # Legacy формат
    ocr_text_legacy = row.get('legacy_ocr_text')
    
    # Сравнение
    ocr_match = (
        ocr_text_new and ocr_text_legacy and 
        ocr_text_new.strip() == ocr_text_legacy.strip()
    )
    
    has_ocr_new = bool(ocr_text_new)
    has_ocr_legacy = bool(ocr_text_legacy)
    has_ocr_data = bool(ocr_data)
    
    return {
        "status": "found",
        "post_id": str(row['post_id']),
        "provider": row['provider'],
        "status_db": row['status'],
        "updated_at": row['updated_at'].isoformat() if row['updated_at'] else None,
        "ocr": {
            "has_new_format": has_ocr_new,
            "has_legacy_format": has_ocr_legacy,
            "has_ocr_data": has_ocr_data,
            "text_new": ocr_text_new[:200] if ocr_text_new else None,
            "text_legacy": ocr_text_legacy[:200] if ocr_text_legacy else None,
            "engine": ocr_engine,
            "confidence": ocr_confidence,
            "match": ocr_match,
            "length_new": len(ocr_text_new) if ocr_text_new else 0,
            "length_legacy": len(ocr_text_legacy) if ocr_text_legacy else 0
        },
        "data_keys": list(data.keys()) if data and isinstance(data, dict) else [],
        "full_data": data
    }


async def _analyze_multiple_rows(rows: List) -> Dict[str, Any]:
    """Анализ нескольких записей."""
    total = len(rows)
    has_ocr_new = 0
    has_ocr_legacy = 0
    has_ocr_both = 0
    has_ocr_none = 0
    providers = {}
    ocr_engines = {}
    issues = []
    
    for row in rows:
        data = row['data']
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                data = None
        
        # OCR анализ
        ocr_data = None
        ocr_text_new = None
        ocr_engine = None
        
        if data and isinstance(data, dict):
            ocr_data = data.get('ocr')
            if isinstance(ocr_data, dict):
                ocr_text_new = ocr_data.get('text')
                ocr_engine = ocr_data.get('engine')
            elif isinstance(ocr_data, str):
                ocr_text_new = ocr_data
        
        ocr_text_legacy = row.get('legacy_ocr_text')
        
        # Подсчёт
        has_new = bool(ocr_text_new)
        has_legacy = bool(ocr_text_legacy)
        
        if has_new:
            has_ocr_new += 1
        if has_legacy:
            has_ocr_legacy += 1
        if has_new and has_legacy:
            has_ocr_both += 1
        if not has_new and not has_legacy:
            has_ocr_none += 1
        
        # Провайдеры
        provider = row['provider'] or 'unknown'
        providers[provider] = providers.get(provider, 0) + 1
        
        # OCR engines
        if ocr_engine:
            ocr_engines[ocr_engine] = ocr_engines.get(ocr_engine, 0) + 1
        
        # Проблемы
        if has_new and not has_legacy:
            issues.append({
                "post_id": str(row['post_id']),
                "issue": "OCR в новом формате, но нет в legacy",
                "provider": provider
            })
        elif has_legacy and not has_new:
            issues.append({
                "post_id": str(row['post_id']),
                "issue": "OCR в legacy формате, но нет в новом",
                "provider": provider
            })
    
    return {
        "status": "summary",
        "total": total,
        "statistics": {
            "has_ocr_new_format": has_ocr_new,
            "has_ocr_legacy_format": has_ocr_legacy,
            "has_ocr_both": has_ocr_both,
            "has_ocr_none": has_ocr_none,
            "ocr_coverage_new": round(has_ocr_new / total * 100, 2) if total > 0 else 0,
            "ocr_coverage_legacy": round(has_ocr_legacy / total * 100, 2) if total > 0 else 0
        },
        "providers": providers,
        "ocr_engines": ocr_engines,
        "issues": issues[:10],  # Первые 10 проблем
        "sample_rows": [
            {
                "post_id": str(row['post_id']),
                "provider": row['provider'],
                "has_ocr_new": bool(data.get('ocr') if isinstance(data, dict) else False) if data else False,
                "has_ocr_legacy": bool(row.get('legacy_ocr_text'))
            }
            for row in rows[:5]
        ]
    }


@click.command()
@click.option('--db-url', envvar='DATABASE_URL', help='Database URL')
@click.option('--post-id', help='Проверить конкретный post_id')
@click.option('--limit', default=20, help='Лимит записей для анализа (по умолчанию 20)')
def main(db_url: str, post_id: Optional[str], limit: int):
    """Проверка OCR в post_enrichment на реальных данных."""
    
    if not db_url:
        console.print("[red]❌ DATABASE_URL не установлен[/red]")
        sys.exit(1)
    
    console.print(f"[bold blue]Проверка OCR в post_enrichment[/bold blue]\n")
    
    if post_id:
        console.print(f"Проверка поста: [cyan]{post_id}[/cyan]\n")
    else:
        console.print(f"Анализ последних [cyan]{limit}[/cyan] записей\n")
    
    result = asyncio.run(check_ocr_in_enrichment(db_url, post_id, limit))
    
    if result.get('status') == 'not_found':
        console.print(f"[yellow]⚠️  {result.get('message')}[/yellow]")
        sys.exit(0)
    elif result.get('status') == 'found':
        # Одна запись
        console.print(f"[green]✓ Найдена запись[/green]\n")
        
        table = Table(title="Vision Enrichment OCR Check")
        table.add_column("Field", style="cyan")
        table.add_column("Value", style="green")
        
        ocr_info = result.get('ocr', {})
        table.add_row("Post ID", result.get('post_id', 'N/A'))
        table.add_row("Provider", result.get('provider', 'N/A'))
        table.add_row("Status", result.get('status_db', 'N/A'))
        table.add_row("Updated At", result.get('updated_at', 'N/A'))
        table.add_row("", "")
        table.add_row("[bold]OCR Status[/bold]", "")
        table.add_row("Has OCR (new format)", "✓" if ocr_info.get('has_new_format') else "✗")
        table.add_row("Has OCR (legacy format)", "✓" if ocr_info.get('has_legacy_format') else "✗")
        table.add_row("OCR Engine", ocr_info.get('engine') or 'N/A')
        table.add_row("OCR Confidence", str(ocr_info.get('confidence')) if ocr_info.get('confidence') is not None else 'N/A')
        table.add_row("OCR Text Length (new)", str(ocr_info.get('length_new', 0)))
        table.add_row("OCR Text Length (legacy)", str(ocr_info.get('length_legacy', 0)))
        table.add_row("Formats Match", "✓" if ocr_info.get('match') else "✗")
        
        console.print(table)
        
        if ocr_info.get('text_new'):
            console.print(f"\n[bold]OCR Text (new format):[/bold]")
            console.print(Panel(ocr_info['text_new'][:500], title="OCR Text"))
        
        if ocr_info.get('text_legacy'):
            console.print(f"\n[bold]OCR Text (legacy format):[/bold]")
            console.print(Panel(ocr_info['text_legacy'][:500], title="OCR Text (Legacy)"))
        
        if not ocr_info.get('has_new_format') and not ocr_info.get('has_legacy_format'):
            console.print("\n[red]⚠️  OCR отсутствует в обоих форматах![/red]")
        
        if result.get('data_keys'):
            console.print(f"\n[bold]Data keys:[/bold] {', '.join(result['data_keys'])}")
    
    elif result.get('status') == 'summary':
        # Статистика
        stats = result.get('statistics', {})
        
        console.print("[bold]Статистика OCR:[/bold]\n")
        
        table = Table(title="OCR Statistics")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="green")
        
        table.add_row("Total Records", str(result.get('total', 0)))
        table.add_row("Has OCR (new format)", f"{stats.get('has_ocr_new_format', 0)} ({stats.get('ocr_coverage_new', 0)}%)")
        table.add_row("Has OCR (legacy format)", f"{stats.get('has_ocr_legacy_format', 0)} ({stats.get('ocr_coverage_legacy', 0)}%)")
        table.add_row("Has OCR (both formats)", str(stats.get('has_ocr_both', 0)))
        table.add_row("No OCR", str(stats.get('has_ocr_none', 0)))
        
        console.print(table)
        
        if result.get('providers'):
            console.print("\n[bold]Провайдеры:[/bold]")
            provider_table = Table()
            provider_table.add_column("Provider", style="cyan")
            provider_table.add_column("Count", style="green")
            for provider, count in result['providers'].items():
                provider_table.add_row(provider, str(count))
            console.print(provider_table)
        
        if result.get('ocr_engines'):
            console.print("\n[bold]OCR Engines:[/bold]")
            engine_table = Table()
            engine_table.add_column("Engine", style="cyan")
            engine_table.add_column("Count", style="green")
            for engine, count in result['ocr_engines'].items():
                engine_table.add_row(engine, str(count))
            console.print(engine_table)
        
        if result.get('issues'):
            console.print(f"\n[bold]Проблемы (первые {len(result['issues'])}):[/bold]")
            issues_table = Table()
            issues_table.add_column("Post ID", style="cyan")
            issues_table.add_column("Issue", style="yellow")
            issues_table.add_column("Provider", style="green")
            for issue in result['issues']:
                issues_table.add_row(
                    issue['post_id'][:8] + "...",
                    issue['issue'],
                    issue['provider']
                )
            console.print(issues_table)
        
        if stats.get('ocr_coverage_new', 0) < 50:
            console.print("\n[yellow]⚠️  Покрытие OCR в новом формате менее 50%![/yellow]")
    
    else:
        console.print(f"[red]❌ Неизвестный статус: {result.get('status')}[/red]")
        sys.exit(1)


if __name__ == '__main__':
    main()

