#!/usr/bin/env python3
"""
Диагностика проблемы с пропаданием OCR и params_hash в post_enrichment.

Проверяет:
1. Наличие OCR в data JSONB
2. Наличие params_hash
3. Последние записи для анализа
4. Сравнение старых и новых записей
"""

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    import asyncpg
    import click
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
except ImportError:
    print("⚠️  Установите зависимости: pip install asyncpg click rich")
    sys.exit(1)

console = Console()


def get_db_url():
    """Получение URL БД из переменных окружения."""
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return database_url.replace("postgresql+asyncpg://", "postgresql://")
    
    db_host = os.getenv("DB_HOST", "supabase-db")
    db_port = os.getenv("DB_PORT", "5432")
    db_user = os.getenv("POSTGRES_USER", "postgres")
    db_password = os.getenv("POSTGRES_PASSWORD", "")
    db_name = os.getenv("POSTGRES_DB", "postgres")
    
    if not db_password:
        console.print("[red]❌ DATABASE_URL или POSTGRES_PASSWORD не установлен[/red]")
        sys.exit(1)
    
    return f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"


async def check_ocr_and_params_hash(db_url: str, limit: int = 50):
    """Проверка OCR и params_hash в post_enrichment."""
    conn = await asyncpg.connect(db_url)
    
    try:
        # Общая статистика
        stats = await conn.fetchrow("""
            SELECT 
                COUNT(*) as total_vision,
                COUNT(CASE WHEN data->'ocr' IS NOT NULL THEN 1 END) as has_ocr_field,
                COUNT(CASE WHEN data->'ocr'->>'text' IS NOT NULL 
                           AND data->'ocr'->>'text' != '' THEN 1 END) as has_ocr_text,
                COUNT(CASE WHEN params_hash IS NOT NULL THEN 1 END) as has_params_hash,
                COUNT(CASE WHEN data->'ocr'->>'text' IS NOT NULL 
                           AND params_hash IS NOT NULL THEN 1 END) as has_both,
                COUNT(CASE WHEN data->'ocr'->>'text' IS NULL 
                           AND params_hash IS NULL THEN 1 END) as missing_both,
                MAX(updated_at) as latest_update
            FROM post_enrichment
            WHERE kind = 'vision'
        """)
        
        console.print("\n[bold blue]Статистика Vision Enrichment[/bold blue]\n")
        
        table = Table(title="Общая статистика")
        table.add_column("Метрика", style="cyan")
        table.add_column("Значение", style="green")
        
        table.add_row("Всего записей Vision", str(stats['total_vision']))
        table.add_row("С полем ocr в data", f"{stats['has_ocr_field']} ({stats['has_ocr_field']/stats['total_vision']*100:.1f}%)" if stats['total_vision'] > 0 else "0")
        table.add_row("С OCR текстом", f"{stats['has_ocr_text']} ({stats['has_ocr_text']/stats['total_vision']*100:.1f}%)" if stats['total_vision'] > 0 else "0")
        table.add_row("С params_hash", f"{stats['has_params_hash']} ({stats['has_params_hash']/stats['total_vision']*100:.1f}%)" if stats['total_vision'] > 0 else "0")
        table.add_row("С OCR и params_hash", str(stats['has_both']))
        table.add_row("Без OCR и params_hash", str(stats['missing_both']))
        table.add_row("Последнее обновление", stats['latest_update'].isoformat() if stats['latest_update'] else "N/A")
        
        console.print(table)
        
        # Последние записи
        recent = await conn.fetch(f"""
            SELECT 
                post_id,
                provider,
                params_hash,
                data->'ocr' as ocr_data,
                data->'ocr'->>'text' as ocr_text,
                data->'ocr'->>'engine' as ocr_engine,
                updated_at,
                created_at
            FROM post_enrichment
            WHERE kind = 'vision'
            ORDER BY updated_at DESC
            LIMIT $1
        """, limit)
        
        console.print(f"\n[bold blue]Последние {len(recent)} записей[/bold blue]\n")
        
        issues_table = Table()
        issues_table.add_column("Post ID", style="cyan")
        issues_table.add_column("Provider", style="green")
        issues_table.add_column("params_hash", style="yellow")
        issues_table.add_column("OCR", style="yellow")
        issues_table.add_column("OCR Text Length", style="yellow")
        issues_table.add_column("Updated", style="blue")
        
        issues_count = 0
        for row in recent:
            has_params_hash = bool(row['params_hash'])
            has_ocr = bool(row['ocr_text'])
            ocr_length = len(row['ocr_text']) if row['ocr_text'] else 0
            
            is_issue = not has_params_hash or not has_ocr
            
            if is_issue:
                issues_count += 1
            
            issues_table.add_row(
                str(row['post_id'])[:8] + "...",
                row['provider'] or "N/A",
                "✓" if has_params_hash else "✗",
                "✓" if has_ocr else "✗",
                str(ocr_length),
                row['updated_at'].isoformat() if row['updated_at'] else "N/A"
            )
        
        console.print(issues_table)
        
        if issues_count > 0:
            console.print(f"\n[yellow]⚠️  Найдено {issues_count} записей с проблемами (нет OCR или params_hash)[/yellow]")
        
        # Записи за последние 24 часа
        yesterday = datetime.now() - timedelta(days=1)
        recent_24h = await conn.fetchrow("""
            SELECT 
                COUNT(*) as total,
                COUNT(CASE WHEN data->'ocr'->>'text' IS NOT NULL THEN 1 END) as with_ocr,
                COUNT(CASE WHEN params_hash IS NOT NULL THEN 1 END) as with_params_hash
            FROM post_enrichment
            WHERE kind = 'vision'
              AND updated_at >= $1
        """, yesterday)
        
        if recent_24h['total'] > 0:
            console.print(f"\n[bold blue]Записи за последние 24 часа[/bold blue]\n")
            recent_table = Table()
            recent_table.add_column("Метрика", style="cyan")
            recent_table.add_column("Значение", style="green")
            recent_table.add_row("Всего", str(recent_24h['total']))
            recent_table.add_row("С OCR", f"{recent_24h['with_ocr']} ({recent_24h['with_ocr']/recent_24h['total']*100:.1f}%)")
            recent_table.add_row("С params_hash", f"{recent_24h['with_params_hash']} ({recent_24h['with_params_hash']/recent_24h['total']*100:.1f}%)")
            console.print(recent_table)
        
        # Примеры проблемных записей
        problematic = await conn.fetch("""
            SELECT 
                post_id,
                provider,
                params_hash,
                data->'ocr' as ocr_data,
                data->'ocr'->>'text' as ocr_text,
                updated_at
            FROM post_enrichment
            WHERE kind = 'vision'
              AND (params_hash IS NULL OR data->'ocr'->>'text' IS NULL)
            ORDER BY updated_at DESC
            LIMIT 10
        """)
        
        if problematic:
            console.print(f"\n[bold red]Примеры проблемных записей (нет OCR или params_hash)[/bold red]\n")
            prob_table = Table()
            prob_table.add_column("Post ID", style="cyan")
            prob_table.add_column("Provider", style="green")
            prob_table.add_column("params_hash", style="yellow")
            prob_table.add_column("OCR в data", style="yellow")
            prob_table.add_column("Updated", style="blue")
            
            for row in problematic:
                prob_table.add_row(
                    str(row['post_id'])[:8] + "...",
                    row['provider'] or "N/A",
                    "✗" if not row['params_hash'] else "✓",
                    "✗" if not row['ocr_text'] else "✓",
                    row['updated_at'].isoformat() if row['updated_at'] else "N/A"
                )
            
            console.print(prob_table)
        
    finally:
        await conn.close()


@click.command()
@click.option('--db-url', envvar='DATABASE_URL', help='Database URL')
@click.option('--limit', default=50, help='Лимит записей для анализа')
def main(db_url: Optional[str], limit: int):
    """Диагностика проблемы с пропаданием OCR и params_hash."""
    
    if not db_url:
        db_url = get_db_url()
    
    console.print(f"[bold blue]Проверка OCR и params_hash в post_enrichment[/bold blue]\n")
    
    asyncio.run(check_ocr_and_params_hash(db_url, limit))


if __name__ == '__main__':
    main()

