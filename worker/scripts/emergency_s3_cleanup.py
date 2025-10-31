#!/usr/bin/env python3
"""
Emergency S3 Cleanup Script для освобождения места в bucket.
[C7-ID: EMERGENCY-S3-001]

Использование:
    python emergency_s3_cleanup.py --target-gb 10 --dry-run
    python emergency_s3_cleanup.py --cleanup-crawl --cleanup-vision
    python emergency_s3_cleanup.py --lru-media --min-refs 0
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

try:
    import click
    from rich.console import Console
    from rich.table import Table
    from rich.progress import Progress, BarColumn, TextColumn, SpinnerColumn
    from rich.panel import Panel
except ImportError:
    print("⚠️  Установите зависимости: pip install click rich")
    sys.exit(1)

console = Console()


# ============================================================================
# CLEANUP FUNCTIONS
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
            region=os.getenv('S3_REGION', 'ru-central-1')
        )
        return service
    except Exception as e:
        console.print(f"[red]❌ Ошибка инициализации S3: {str(e)}[/red]")
        return None


async def cleanup_by_ttl(
    s3_service,
    prefix: str,
    max_age_days: int,
    dry_run: bool = True
) -> Dict[str, Any]:
    """Очистка объектов старше max_age_days."""
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    
    try:
        # List objects
        objects = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: list(s3_service.s3_client.list_objects_v2(
                Bucket=s3_service.bucket_name,
                Prefix=prefix
            ).get('Contents', []))
        )
        
        to_delete = []
        total_size = 0
        
        for obj in objects:
            last_modified = obj.get('LastModified')
            if last_modified and last_modified.replace(tzinfo=timezone.utc) < cutoff_date:
                to_delete.append({
                    'key': obj['Key'],
                    'size': obj['Size'],
                    'last_modified': last_modified.isoformat()
                })
                total_size += obj['Size']
        
        if not dry_run and to_delete:
            # Удаляем объекты батчами
            delete_keys = [{'Key': obj['key']} for obj in to_delete]
            
            for i in range(0, len(delete_keys), 1000):  # boto3 лимит - 1000 на запрос
                batch = delete_keys[i:i+1000]
                await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda b=batch: s3_service.s3_client.delete_objects(
                        Bucket=s3_service.bucket_name,
                        Delete={'Objects': b}
                    )
                )
        
        return {
            "prefix": prefix,
            "found": len(objects),
            "to_delete": len(to_delete),
            "deleted": len(to_delete) if not dry_run else 0,
            "size_freed_gb": total_size / (1024 ** 3),
            "cutoff_date": cutoff_date.isoformat()
        }
        
    except Exception as e:
        return {
            "status": "error",
            "error": str(e)
        }


async def cleanup_crawl_cache(
    s3_service,
    max_age_days: int = 3,
    dry_run: bool = True
) -> Dict[str, Any]:
    """Очистка crawl кэша старше max_age_days."""
    return await cleanup_by_ttl(
        s3_service,
        prefix="crawl/",
        max_age_days=max_age_days,
        dry_run=dry_run
    )


async def cleanup_vision_cache(
    s3_service,
    max_age_days: int = 7,
    dry_run: bool = True
) -> Dict[str, Any]:
    """Очистка vision кэша старше max_age_days."""
    return await cleanup_by_ttl(
        s3_service,
        prefix="vision/",
        max_age_days=max_age_days,
        dry_run=dry_run
    )


async def cleanup_orphaned_multipart(s3_service, dry_run: bool = True) -> Dict[str, Any]:
    """Очистка незавершённых multipart uploads."""
    try:
        # List multipart uploads
        uploads = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: list(s3_service.s3_client.list_multipart_uploads(
                Bucket=s3_service.bucket_name
            ).get('Uploads', []))
        )
        
        aborted = 0
        
        if not dry_run and uploads:
            for upload in uploads:
                try:
                    await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda u=upload: s3_service.s3_client.abort_multipart_upload(
                            Bucket=s3_service.bucket_name,
                            Key=u['Key'],
                            UploadId=u['UploadId']
                        )
                    )
                    aborted += 1
                except Exception as e:
                    console.print(f"[yellow]⚠️  Не удалось прервать upload {upload['Key']}: {str(e)}[/yellow]")
        
        return {
            "found": len(uploads),
            "aborted": aborted if not dry_run else 0
        }
        
    except Exception as e:
        return {
            "status": "error",
            "error": str(e)
        }


async def get_target_cleanup(
    s3_service,
    target_free_gb: float,
    dry_run: bool = True
) -> Dict[str, Any]:
    """Расчёт необходимой очистки для достижения target_free_gb."""
    try:
        from worker.services.storage_quota import StorageQuotaService
        
        quota_service = StorageQuotaService(s3_service=s3_service)
        usage = await quota_service.get_bucket_usage()
        
        current_gb = usage.get('total_gb', 0)
        limit_gb = quota_service.limits.get('total_gb', 15)
        target_usage_gb = limit_gb - target_free_gb
        need_free_gb = max(0, current_gb - target_usage_gb)
        
        if need_free_gb <= 0:
            return {
                "status": "no_cleanup_needed",
                "current_gb": current_gb,
                "target_gb": target_usage_gb,
                "need_free_gb": 0
            }
        
        # Стратегия очистки
        cleanup_plan = []
        freed_gb = 0.0
        
        # 1. Crawl cache (старше 3 дней)
        crawl_result = await cleanup_crawl_cache(s3_service, max_age_days=3, dry_run=True)
        if crawl_result.get('size_freed_gb', 0) > 0:
            cleanup_plan.append(("crawl_3d", crawl_result))
            freed_gb += crawl_result['size_freed_gb']
        
        if freed_gb < need_free_gb:
            # 2. Vision cache (старше 7 дней)
            vision_result = await cleanup_vision_cache(s3_service, max_age_days=7, dry_run=True)
            if vision_result.get('size_freed_gb', 0) > 0:
                cleanup_plan.append(("vision_7d", vision_result))
                freed_gb += vision_result['size_freed_gb']
        
        # TODO: LRU media cleanup (требует БД интеграцию)
        
        return {
            "status": "plan_ready",
            "current_gb": current_gb,
            "target_gb": target_usage_gb,
            "need_free_gb": need_free_gb,
            "plan_freed_gb": freed_gb,
            "cleanup_plan": cleanup_plan,
            "sufficient": freed_gb >= need_free_gb
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
@click.option('--dry-run/--no-dry-run', default=True, help='Dry run mode (не удаляет)')
@click.pass_context
def cli(ctx, dry_run: bool):
    """Emergency S3 Cleanup для освобождения места."""
    ctx.ensure_object(dict)
    ctx.obj['dry_run'] = dry_run


@cli.command()
@click.option('--target-gb', type=float, default=12.0, help='Целевое свободное место (GB)')
@click.pass_context
def auto(ctx, target_gb: float):
    """Автоматическая очистка до target_gb свободного места."""
    console.print(f"[bold blue]Emergency Cleanup (target: {target_gb} GB free)[/bold blue]\n")
    
    if ctx.obj['dry_run']:
        console.print("[yellow]⚠️  DRY RUN MODE - изменения не будут применены[/yellow]\n")
    
    s3_service = asyncio.run(get_s3_service())
    if not s3_service:
        sys.exit(1)
    
    with console.status("[bold green]Расчёт плана очистки..."):
        plan = asyncio.run(get_target_cleanup(s3_service, target_gb, ctx.obj['dry_run']))
    
    if plan.get('status') == 'error':
        console.print(f"[red]❌ Ошибка: {plan.get('error')}[/red]")
        sys.exit(1)
    elif plan.get('status') == 'no_cleanup_needed':
        console.print("[green]✓ Очистка не требуется[/green]")
        console.print(f"  Current: {plan.get('current_gb', 0):.2f} GB")
        console.print(f"  Target: {plan.get('target_gb', 0):.2f} GB")
        sys.exit(0)
    
    # Выводим план
    console.print(f"[bold]Current: {plan.get('current_gb', 0):.2f} GB[/bold]")
    console.print(f"[bold]Target: {plan.get('target_gb', 0):.2f} GB[/bold]")
    console.print(f"[bold]Need to free: {plan.get('need_free_gb', 0):.2f} GB[/bold]")
    console.print(f"[bold]Plan will free: {plan.get('plan_freed_gb', 0):.2f} GB[/bold]\n")
    
    if not plan.get('sufficient'):
        console.print("[yellow]⚠️  План очистки может быть недостаточным[/yellow]\n")
    
    # Выполняем очистку
    total_freed = 0.0
    
    async def execute_cleanup_steps():
        nonlocal total_freed
        for step_name, step_result in plan.get('cleanup_plan', []):
            console.print(f"[cyan]Cleaning up: {step_name}[/cyan]")
            
            if step_name == "crawl_3d":
                result = await cleanup_crawl_cache(s3_service, max_age_days=3, dry_run=ctx.obj['dry_run'])
            elif step_name == "vision_7d":
                result = await cleanup_vision_cache(s3_service, max_age_days=7, dry_run=ctx.obj['dry_run'])
            else:
                continue
            
            if result.get('status') == 'error':
                console.print(f"  [red]❌ Ошибка: {result.get('error')}[/red]")
            else:
                deleted = result.get('deleted', result.get('to_delete', 0))
                freed = result.get('size_freed_gb', 0)
                console.print(f"  [green]✓ Deleted: {deleted} objects ({freed:.2f} GB)[/green]")
                total_freed += freed
    
    asyncio.run(execute_cleanup_steps())
    
    console.print(f"\n[bold green]Total freed: {total_freed:.2f} GB[/bold green]")


@cli.command()
@click.option('--max-age-days', default=3, help='Максимальный возраст в днях')
@click.pass_context
def crawl(ctx, max_age_days: int):
    """Очистка crawl кэша."""
    console.print(f"[bold blue]Cleaning crawl cache (>{max_age_days} days)[/bold blue]\n")
    
    if ctx.obj['dry_run']:
        console.print("[yellow]⚠️  DRY RUN MODE[/yellow]\n")
    
    s3_service = asyncio.run(get_s3_service())
    if not s3_service:
        sys.exit(1)
    
    result = asyncio.run(cleanup_crawl_cache(s3_service, max_age_days, ctx.obj['dry_run']))
    
    if result.get('status') == 'error':
        console.print(f"[red]❌ Ошибка: {result.get('error')}[/red]")
        sys.exit(1)
    
    console.print(f"[green]✓ Found: {result.get('found', 0)} objects[/green]")
    console.print(f"[green]✓ {'Would delete' if ctx.obj['dry_run'] else 'Deleted'}: {result.get('to_delete' if ctx.obj['dry_run'] else 'deleted', 0)} objects[/green]")
    console.print(f"[green]✓ {'Would free' if ctx.obj['dry_run'] else 'Freed'}: {result.get('size_freed_gb', 0):.2f} GB[/green]")


@cli.command()
@click.option('--max-age-days', default=7, help='Максимальный возраст в днях')
@click.pass_context
def vision(ctx, max_age_days: int):
    """Очистка vision кэша."""
    console.print(f"[bold blue]Cleaning vision cache (>{max_age_days} days)[/bold blue]\n")
    
    if ctx.obj['dry_run']:
        console.print("[yellow]⚠️  DRY RUN MODE[/yellow]\n")
    
    s3_service = asyncio.run(get_s3_service())
    if not s3_service:
        sys.exit(1)
    
    result = asyncio.run(cleanup_vision_cache(s3_service, max_age_days, ctx.obj['dry_run']))
    
    if result.get('status') == 'error':
        console.print(f"[red]❌ Ошибка: {result.get('error')}[/red]")
        sys.exit(1)
    
    console.print(f"[green]✓ Found: {result.get('found', 0)} objects[/green]")
    console.print(f"[green]✓ {'Would delete' if ctx.obj['dry_run'] else 'Deleted'}: {result.get('to_delete' if ctx.obj['dry_run'] else 'deleted', 0)} objects[/green]")
    console.print(f"[green]✓ {'Would free' if ctx.obj['dry_run'] else 'Freed'}: {result.get('size_freed_gb', 0):.2f} GB[/green]")


@cli.command()
@click.pass_context
def multipart(ctx):
    """Очистка незавершённых multipart uploads."""
    console.print("[bold blue]Cleaning orphaned multipart uploads[/bold blue]\n")
    
    if ctx.obj['dry_run']:
        console.print("[yellow]⚠️  DRY RUN MODE[/yellow]\n")
    
    s3_service = asyncio.run(get_s3_service())
    if not s3_service:
        sys.exit(1)
    
    result = asyncio.run(cleanup_orphaned_multipart(s3_service, ctx.obj['dry_run']))
    
    if result.get('status') == 'error':
        console.print(f"[red]❌ Ошибка: {result.get('error')}[/red]")
        sys.exit(1)
    
    console.print(f"[green]✓ Found: {result.get('found', 0)} uploads[/green]")
    console.print(f"[green]✓ {'Would abort' if ctx.obj['dry_run'] else 'Aborted'}: {result.get('aborted', 0)} uploads[/green]")


if __name__ == '__main__':
    cli()

