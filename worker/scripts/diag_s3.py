#!/usr/bin/env python3
"""
Diagnostic CLI для S3 Storage компонентов.
[C7-ID: DIAG-S3-001]

Использование:
    python diag_s3.py quota --tenant-id <uuid>
    python diag_s3.py usage
    python diag_s3.py list --prefix media/ --limit 10
    python diag_s3.py check-key --key media/tenant/file.jpg
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Any, Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

try:
    import click
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.progress import Progress, SpinnerColumn, TextColumn
except ImportError:
    print("⚠️  Установите зависимости: pip install click rich")
    sys.exit(1)

console = Console()


# ============================================================================
# DIAGNOSTIC FUNCTIONS
# ============================================================================

async def get_s3_service():
    """Инициализация S3 сервиса."""
    try:
        from api.services.s3_storage import S3StorageService
        
        service = S3StorageService(
            endpoint_url=os.getenv('S3_ENDPOINT_URL', 'https://s3.cloud.ru'),
            access_key_id=os.getenv('S3_ACCESS_KEY_ID', ''),
            secret_access_key=os.getenv('S3_SECRET_ACCESS_KEY', ''),
            bucket_name=os.getenv('S3_BUCKET_NAME', 'test-467940'),
            region=os.getenv('S3_REGION', 'ru-central-1'),
            use_compression=os.getenv('S3_USE_COMPRESSION', 'true').lower() == 'true',
            compression_level=int(os.getenv('S3_COMPRESSION_LEVEL', '6'))
        )
        return service
    except Exception as e:
        console.print(f"[red]❌ Ошибка инициализации S3: {str(e)}[/red]")
        return None


async def check_quota(tenant_id: str) -> Dict[str, Any]:
    """Проверка S3 квот."""
    try:
        from worker.services.storage_quota import StorageQuotaService
        
        s3_service = await get_s3_service()
        if not s3_service:
            return {"status": "error", "error": "S3 service недоступен"}
        
        quota_service = StorageQuotaService(s3_service=s3_service)
        
        usage = await quota_service.get_bucket_usage()
        
        return {
            "status": "ok",
            "usage": usage,
            "limits": quota_service.limits
        }
        
    except Exception as e:
        return {
            "status": "error",
            "error": str(e)
        }


async def list_objects(prefix: str, limit: int = 100) -> Dict[str, Any]:
    """Список объектов в S3."""
    try:
        s3_service = await get_s3_service()
        if not s3_service:
            return {"status": "error", "error": "S3 service недоступен"}
        
        # List objects
        objects = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: list(s3_service.s3_client.list_objects_v2(
                Bucket=s3_service.bucket_name,
                Prefix=prefix,
                MaxKeys=limit
            ).get('Contents', []))
        )
        
        total_size = sum(obj['Size'] for obj in objects)
        
        return {
            "status": "ok",
            "count": len(objects),
            "total_size_bytes": total_size,
            "total_size_gb": total_size / (1024 ** 3),
            "objects": [
                {
                    "key": obj['Key'],
                    "size_bytes": obj['Size'],
                    "size_mb": obj['Size'] / (1024 ** 2),
                    "last_modified": obj['LastModified'].isoformat() if obj.get('LastModified') else None
                }
                for obj in objects[:limit]
            ]
        }
        
    except Exception as e:
        return {
            "status": "error",
            "error": str(e)
        }


async def check_object(s3_key: str) -> Dict[str, Any]:
    """Проверка конкретного объекта."""
    try:
        s3_service = await get_s3_service()
        if not s3_service:
            return {"status": "error", "error": "S3 service недоступен"}
        
        # Head object
        try:
            metadata = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: s3_service.s3_client.head_object(
                    Bucket=s3_service.bucket_name,
                    Key=s3_key
                )
            )
            
            return {
                "status": "found",
                "key": s3_key,
                "size_bytes": metadata.get('ContentLength', 0),
                "size_mb": metadata.get('ContentLength', 0) / (1024 ** 2),
                "content_type": metadata.get('ContentType'),
                "content_encoding": metadata.get('ContentEncoding'),
                "last_modified": metadata.get('LastModified').isoformat() if metadata.get('LastModified') else None,
                "etag": metadata.get('ETag', '').strip('"')
            }
        except s3_service.s3_client.exceptions.NoSuchKey:
            return {
                "status": "not_found",
                "key": s3_key
            }
        
    except Exception as e:
        return {
            "status": "error",
            "error": str(e)
        }


async def get_bucket_usage() -> Dict[str, Any]:
    """Общее использование bucket."""
    try:
        s3_service = await get_s3_service()
        if not s3_service:
            return {"status": "error", "error": "S3 service недоступен"}
        
        # Подсчёт по префиксам
        prefixes = {
            "media": "media/",
            "vision": "vision/",
            "crawl": "crawl/"
        }
        
        usage_by_type = {}
        total_size = 0
        
        for type_name, prefix in prefixes.items():
            try:
                objects = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda p=prefix: list(s3_service.s3_client.list_objects_v2(
                        Bucket=s3_service.bucket_name,
                        Prefix=p
                    ).get('Contents', []))
                )
                
                type_size = sum(obj['Size'] for obj in objects)
                usage_by_type[type_name] = {
                    "count": len(objects),
                    "size_bytes": type_size,
                    "size_gb": type_size / (1024 ** 3)
                }
                total_size += type_size
            except Exception as e:
                usage_by_type[type_name] = {
                    "error": str(e)
                }
        
        return {
            "status": "ok",
            "total_bytes": total_size,
            "total_gb": total_size / (1024 ** 3),
            "by_type": usage_by_type
        }
        
    except Exception as e:
        return {
            "status": "error",
            "error": str(e)
        }


# ============================================================================
# CLI COMMANDS
# ============================================================================

@click.group()
@click.pass_context
def cli(ctx):
    """Диагностика S3 Storage компонентов."""
    ctx.ensure_object(dict)


@cli.command()
@click.option('--tenant-id', envvar='S3_DEFAULT_TENANT_ID', help='Tenant ID')
@click.pass_context
def quota(ctx, tenant_id: Optional[str]):
    """Проверка S3 квот и использования."""
    console.print("[bold blue]Проверка S3 Quota[/bold blue]\n")
    
    if not tenant_id:
        tenant_id = os.getenv('S3_DEFAULT_TENANT_ID', '877193ef-be80-4977-aaeb-8009c3d772ee')
    
    result = asyncio.run(check_quota(tenant_id))
    
    if result.get('status') == 'error':
        console.print(f"[red]❌ Ошибка: {result.get('error')}[/red]")
        sys.exit(1)
    
    usage = result.get('usage', {})
    limits = result.get('limits', {})
    
    # Общая статистика
    table = Table(title="Bucket Usage")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")
    
    total_gb = usage.get('total_gb', 0)
    limit_gb = limits.get('total_gb', 15)
    usage_percent = usage.get('usage_percent', 0)
    
    table.add_row("Total Usage (GB)", f"{total_gb:.2f} / {limit_gb:.1f}")
    table.add_row("Usage %", f"{usage_percent:.1f}%")
    table.add_row("Emergency Threshold", f"{limits.get('emergency_threshold_gb', 14):.1f} GB")
    
    # Индикатор заполнения
    if usage_percent >= 90:
        color = "red"
        emoji = "🔴"
    elif usage_percent >= 75:
        color = "yellow"
        emoji = "🟡"
    else:
        color = "green"
        emoji = "🟢"
    
    table.add_row("Status", f"[{color}]{emoji} {usage_percent:.1f}%[/{color}]")
    
    console.print(table)
    
    # По типам контента
    if usage.get('by_type'):
        console.print("\n[bold]По типам контента:[/bold]")
        table2 = Table()
        table2.add_column("Type", style="cyan")
        table2.add_column("Usage (GB)", style="green")
        table2.add_column("Limit (GB)", style="yellow")
        
        by_type = usage['by_type']
        quotas = limits.get('quotas_by_type', {})
        
        for type_name in ['media', 'vision', 'crawl']:
            type_usage = by_type.get(type_name, 0)
            type_limit = quotas.get(type_name, {}).get('max_gb', 0)
            table2.add_row(
                type_name.capitalize(),
                f"{type_usage:.2f}",
                f"{type_limit:.1f}"
            )
        
        console.print(table2)


@cli.command()
@click.pass_context
def usage(ctx):
    """Общее использование bucket."""
    console.print("[bold blue]Bucket Usage Statistics[/bold blue]\n")
    
    with console.status("[bold green]Подсчёт использования..."):
        result = asyncio.run(get_bucket_usage())
    
    if result.get('status') == 'error':
        console.print(f"[red]❌ Ошибка: {result.get('error')}[/red]")
        sys.exit(1)
    
    # Общая статистика
    console.print(f"[bold]Total: {result.get('total_gb', 0):.2f} GB[/bold]\n")
    
    # По типам
    table = Table(title="Usage by Type")
    table.add_column("Type", style="cyan")
    table.add_column("Objects", style="green", justify="right")
    table.add_column("Size (GB)", style="yellow", justify="right")
    
    for type_name, data in result.get('by_type', {}).items():
        if 'error' in data:
            table.add_row(type_name.capitalize(), "Error", data['error'])
        else:
            table.add_row(
                type_name.capitalize(),
                str(data.get('count', 0)),
                f"{data.get('size_gb', 0):.2f}"
            )
    
    console.print(table)


@cli.command()
@click.option('--prefix', default='', help='Префикс для поиска')
@click.option('--limit', default=20, help='Лимит объектов')
@click.pass_context
def list(ctx, prefix: str, limit: int):
    """Список объектов в S3."""
    console.print(f"[bold blue]Список объектов: {prefix or '(корень)'}[/bold blue]\n")
    
    result = asyncio.run(list_objects(prefix, limit))
    
    if result.get('status') == 'error':
        console.print(f"[red]❌ Ошибка: {result.get('error')}[/red]")
        sys.exit(1)
    
    console.print(f"[dim]Найдено: {result.get('count', 0)} объектов ({result.get('total_size_gb', 0):.2f} GB)[/dim]\n")
    
    if result.get('objects'):
        table = Table()
        table.add_column("Key", style="cyan")
        table.add_column("Size (MB)", style="green", justify="right")
        table.add_column("Last Modified", style="yellow")
        
        for obj in result['objects']:
            table.add_row(
                obj['key'],
                f"{obj['size_mb']:.2f}",
                obj['last_modified'] or 'N/A'
            )
        
        console.print(table)
    else:
        console.print("[yellow]⚠️  Объекты не найдены[/yellow]")


@cli.command()
@click.option('--key', required=True, help='S3 ключ объекта')
@click.pass_context
def check_key(ctx, key: str):
    """Проверка конкретного объекта."""
    console.print(f"[bold blue]Проверка объекта: {key}[/bold blue]\n")
    
    result = asyncio.run(check_object(key))
    
    if result.get('status') == 'error':
        console.print(f"[red]❌ Ошибка: {result.get('error')}[/red]")
        sys.exit(1)
    elif result.get('status') == 'not_found':
        console.print(f"[yellow]⚠️  Объект не найден[/yellow]")
        sys.exit(0)
    
    # Выводим метаданные
    table = Table(title="Object Metadata")
    table.add_column("Field", style="cyan")
    table.add_column("Value", style="green")
    
    table.add_row("Key", result.get('key', 'N/A'))
    table.add_row("Size (MB)", f"{result.get('size_mb', 0):.2f}")
    table.add_row("Content Type", result.get('content_type', 'N/A'))
    table.add_row("Content Encoding", result.get('content_encoding', 'N/A'))
    table.add_row("Last Modified", result.get('last_modified', 'N/A'))
    table.add_row("ETag", result.get('etag', 'N/A'))
    
    console.print(table)


if __name__ == '__main__':
    cli()

