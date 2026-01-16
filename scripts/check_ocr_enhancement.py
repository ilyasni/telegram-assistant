#!/usr/bin/env python3
"""
Проверка OCR Enhancement полей в post_enrichment.
Проверяет наличие text_enhanced, corrections, entities и других enhancement полей.

Использование:
    python scripts/check_ocr_enhancement.py --post-id <uuid>
    python scripts/check_ocr_enhancement.py --post-id <uuid1> --post-id <uuid2>
"""

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Any, Optional

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


async def check_ocr_enhancement(
    db_url: str,
    post_ids: List[str]
) -> Dict[str, Any]:
    """
    Проверка OCR Enhancement полей для указанных post_id.
    """
    conn = await asyncpg.connect(db_url)
    
    try:
        results = []
        
        for post_id in post_ids:
            row = await conn.fetchrow("""
                SELECT 
                    post_id,
                    kind,
                    provider,
                    status,
                    data,
                    created_at,
                    updated_at
                FROM post_enrichment
                WHERE post_id = $1 AND kind = 'vision'
                ORDER BY updated_at DESC
                LIMIT 1
            """, post_id)
            
            if not row:
                results.append({
                    "post_id": post_id,
                    "status": "not_found",
                    "message": f"Post {post_id} не найден или не имеет Vision обогащения"
                })
                continue
            
            data = row['data']
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except json.JSONDecodeError:
                    data = None
            
            ocr_data = None
            if data and isinstance(data, dict):
                ocr_data = data.get('ocr')
            
            enhancement_info = {
                "post_id": str(row['post_id']),
                "provider": row['provider'],
                "status_db": row['status'],
                "updated_at": row['updated_at'].isoformat() if row['updated_at'] else None,
                "has_ocr": bool(ocr_data and isinstance(ocr_data, dict) and ocr_data.get('text')),
                "ocr_engine": ocr_data.get('engine') if isinstance(ocr_data, dict) else None,
                "ocr_confidence": ocr_data.get('confidence') if isinstance(ocr_data, dict) else None,
                "has_text_enhanced": bool(ocr_data.get('text_enhanced') if isinstance(ocr_data, dict) else False),
                "has_corrections": bool(ocr_data.get('corrections') if isinstance(ocr_data, dict) else False),
                "has_entities": bool(ocr_data.get('entities') if isinstance(ocr_data, dict) else False),
                "has_enhanced_at": bool(ocr_data.get('enhanced_at') if isinstance(ocr_data, dict) else False),
                "has_enhancement_version": bool(ocr_data.get('enhancement_version') if isinstance(ocr_data, dict) else False),
                "text_original": ocr_data.get('text')[:200] if isinstance(ocr_data, dict) and ocr_data.get('text') else None,
                "text_enhanced": ocr_data.get('text_enhanced')[:200] if isinstance(ocr_data, dict) and ocr_data.get('text_enhanced') else None,
                "corrections_count": len(ocr_data.get('corrections', [])) if isinstance(ocr_data, dict) and ocr_data.get('corrections') else 0,
                "entities_count": len(ocr_data.get('entities', [])) if isinstance(ocr_data, dict) and ocr_data.get('entities') else 0,
                "enhanced_at": ocr_data.get('enhanced_at') if isinstance(ocr_data, dict) else None,
                "enhancement_version": ocr_data.get('enhancement_version') if isinstance(ocr_data, dict) else None,
                "text_confidence": ocr_data.get('text_confidence') if isinstance(ocr_data, dict) else None,
                "full_ocr_data": ocr_data
            }
            
            results.append(enhancement_info)
        
        return {
            "status": "success",
            "results": results
        }
    
    finally:
        await conn.close()


@click.command()
@click.option('--db-url', envvar='DATABASE_URL', help='Database URL')
@click.option('--post-id', multiple=True, help='Post ID для проверки (можно указать несколько)')
def main(db_url: str, post_id: tuple):
    """Проверка OCR Enhancement полей в post_enrichment."""
    
    if not db_url:
        console.print("[red]❌ DATABASE_URL не установлен[/red]")
        sys.exit(1)
    
    post_ids = list(post_id) if post_id else []
    
    if not post_ids:
        console.print("[red]❌ Укажите хотя бы один --post-id[/red]")
        sys.exit(1)
    
    console.print(f"[bold blue]Проверка OCR Enhancement для {len(post_ids)} постов[/bold blue]\n")
    
    result = asyncio.run(check_ocr_enhancement(db_url, post_ids))
    
    if result.get('status') == 'success':
        for item in result.get('results', []):
            if item.get('status') == 'not_found':
                console.print(f"[yellow]⚠️  {item.get('message')}[/yellow]\n")
                continue
            
            console.print(f"[bold]Post ID:[/bold] [cyan]{item['post_id']}[/cyan]")
            console.print(f"[bold]Provider:[/bold] {item['provider']}")
            console.print(f"[bold]Status:[/bold] {item['status_db']}")
            console.print(f"[bold]Updated At:[/bold] {item['updated_at']}\n")
            
            # Таблица с основными полями
            table = Table(title=f"OCR Enhancement Check - {item['post_id'][:8]}...")
            table.add_column("Field", style="cyan")
            table.add_column("Value", style="green")
            
            table.add_row("Has OCR", "✓" if item['has_ocr'] else "✗")
            table.add_row("OCR Engine", item['ocr_engine'] or 'N/A')
            table.add_row("OCR Confidence", str(item['ocr_confidence']) if item['ocr_confidence'] is not None else 'N/A')
            table.add_row("", "")
            table.add_row("[bold]Enhancement Fields[/bold]", "")
            table.add_row("Has text_enhanced", "✓" if item['has_text_enhanced'] else "✗")
            table.add_row("Has corrections", "✓" if item['has_corrections'] else "✗")
            table.add_row("Has entities", "✓" if item['has_entities'] else "✗")
            table.add_row("Has enhanced_at", "✓" if item['has_enhanced_at'] else "✗")
            table.add_row("Has enhancement_version", "✓" if item['has_enhancement_version'] else "✗")
            table.add_row("", "")
            table.add_row("Corrections Count", str(item['corrections_count']))
            table.add_row("Entities Count", str(item['entities_count']))
            table.add_row("Text Confidence", str(item['text_confidence']) if item['text_confidence'] is not None else 'N/A')
            table.add_row("Enhanced At", item['enhanced_at'] or 'N/A')
            table.add_row("Enhancement Version", item['enhancement_version'] or 'N/A')
            
            console.print(table)
            
            # Оригинальный текст
            if item['text_original']:
                console.print(f"\n[bold]OCR Text (original):[/bold]")
                console.print(Panel(item['text_original'], title="Original OCR Text"))
            
            # Улучшенный текст
            if item['text_enhanced']:
                console.print(f"\n[bold]OCR Text (enhanced):[/bold]")
                console.print(Panel(item['text_enhanced'], title="Enhanced OCR Text"))
            elif item['has_ocr']:
                console.print(f"\n[yellow]⚠️  OCR есть, но text_enhanced отсутствует - enhancement не применён![/yellow]")
            
            # Исправления
            if item['corrections_count'] > 0 and item['full_ocr_data']:
                corrections = item['full_ocr_data'].get('corrections', [])
                console.print(f"\n[bold]Corrections ({item['corrections_count']}):[/bold]")
                for i, corr in enumerate(corrections[:5], 1):  # Показываем первые 5
                    console.print(f"  {i}. {corr.get('original', 'N/A')} → {corr.get('corrected', 'N/A')} (confidence: {corr.get('confidence', 'N/A')})")
                if len(corrections) > 5:
                    console.print(f"  ... и ещё {len(corrections) - 5}")
            
            # Сущности
            if item['entities_count'] > 0 and item['full_ocr_data']:
                entities = item['full_ocr_data'].get('entities', [])
                console.print(f"\n[bold]Entities ({item['entities_count']}):[/bold]")
                for i, entity in enumerate(entities[:5], 1):  # Показываем первые 5
                    console.print(f"  {i}. {entity.get('text', 'N/A')} ({entity.get('type', 'N/A')}) - confidence: {entity.get('confidence', 'N/A')}")
                if len(entities) > 5:
                    console.print(f"  ... и ещё {len(entities) - 5}")
            
            console.print("\n" + "="*60 + "\n")
    
    else:
        console.print(f"[red]❌ Ошибка: {result.get('status')}[/red]")
        sys.exit(1)


if __name__ == '__main__':
    main()


