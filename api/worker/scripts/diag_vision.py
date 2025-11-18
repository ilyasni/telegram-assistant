#!/usr/bin/env python3
"""
Diagnostic CLI для Vision Analysis компонентов.
[C7-ID: DIAG-VISION-001]

Использование:
    python diag_vision.py check --post-id <uuid>
    python diag_vision.py stats --tenant-id <uuid>
    python diag_vision.py test --media-file image.jpg
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Any, Optional

# Добавляем пути
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

try:
    import click
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.syntax import Syntax
except ImportError:
    print("⚠️  Установите зависимости: pip install click rich")
    sys.exit(1)

console = Console()


# ============================================================================
# DIAGNOSTIC FUNCTIONS
# ============================================================================

async def check_vision_enrichment(post_id: str, db_url: str) -> Dict[str, Any]:
    """Проверка Vision обогащения для поста."""
    try:
        import asyncpg
        
        conn = await asyncpg.connect(db_url)
        
        # Context7: Получаем данные из post_enrichment (новый формат через data JSONB)
        row = await conn.fetchrow("""
            SELECT 
                post_id,
                kind,
                provider,
                status,
                data,
                vision_classification,
                vision_description,
                vision_ocr_text,
                vision_is_meme,
                vision_provider,
                vision_model,
                vision_analyzed_at,
                vision_tokens_used,
                s3_vision_keys,
                s3_media_keys,
                ocr_present
            FROM post_enrichment
            WHERE post_id = $1 AND kind = 'vision'
            ORDER BY updated_at DESC
            LIMIT 1
        """, post_id)
        
        await conn.close()
        
        if not row:
            return {
                "status": "not_found",
                "message": f"Post {post_id} не найден или не имеет Vision обогащения"
            }
        
        # Context7: Анализ данных (новый формат через data JSONB)
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
        
        # Legacy поля
        has_classification = bool(row.get('vision_classification'))
        has_description = bool(row.get('vision_description'))
        has_ocr_legacy = bool(row.get('vision_ocr_text'))
        has_ocr_new = bool(ocr_text_new)
        has_s3_keys = bool(row.get('s3_vision_keys'))
        
        return {
            "status": "found",
            "post_id": str(row['post_id']),
            "kind": row.get('kind'),
            "provider": row.get('provider') or row.get('vision_provider'),
            "status_db": row.get('status'),
            "analyzed_at": row.get('vision_analyzed_at').isoformat() if row.get('vision_analyzed_at') else None,
            "model": row.get('vision_model') or (data.get('model') if data else None),
            "tokens_used": row.get('vision_tokens_used') or (data.get('tokens_used') if data else None),
            "is_meme": row.get('vision_is_meme') or (data.get('is_meme') if data else False),
            "has_classification": has_classification or bool(data.get('classification') if data else None),
            "has_description": has_description or bool(data.get('description') if data else None),
            "has_ocr": has_ocr_new or has_ocr_legacy,
            "has_ocr_new": has_ocr_new,
            "has_ocr_legacy": has_ocr_legacy,
            "ocr_engine": ocr_engine,
            "ocr_confidence": ocr_confidence,
            "has_s3_keys": has_s3_keys,
            "s3_vision_keys_count": len(row['s3_vision_keys']) if row.get('s3_vision_keys') else 0,
            "classification": row.get('vision_classification') or (data.get('classification') if data else None),
            "description_preview": (row.get('vision_description') or (data.get('description') if data else None) or '')[:200],
            "ocr_preview": (ocr_text_new or row.get('vision_ocr_text') or '')[:200],
            "data_keys": list(data.keys()) if data and isinstance(data, dict) else []
        }
        
    except Exception as e:
        return {
            "status": "error",
            "error": str(e)
        }


async def check_vision_events(redis_url: str, post_id: Optional[str] = None) -> Dict[str, Any]:
    """Проверка Vision событий в Redis Streams."""
    try:
        import redis.asyncio as redis
        
        client = redis.from_url(redis_url, decode_responses=True)
        
        # Проверяем stream:posts:vision
        uploaded_count = await client.xlen("stream:posts:vision")
        analyzed_count = await client.xlen("stream:posts:vision:analyzed")
        
        # Проверяем PEL
        try:
            uploaded_pending = await client.xpending("stream:posts:vision", "vision_workers")
            uploaded_pending_count = uploaded_pending[0] if uploaded_pending else 0
        except:
            uploaded_pending_count = 0
        
        try:
            analyzed_pending = await client.xpending("stream:posts:vision:analyzed", "vision_workers")
            analyzed_pending_count = analyzed_pending[0] if analyzed_pending else 0
        except:
            analyzed_pending_count = 0
        
        await client.close()
        
        return {
            "uploaded_events": uploaded_count,
            "analyzed_events": analyzed_count,
            "uploaded_pending": uploaded_pending_count,
            "analyzed_pending": analyzed_pending_count
        }
        
    except Exception as e:
        return {
            "status": "error",
            "error": str(e)
        }


async def check_s3_vision_cache(s3_service, tenant_id: str, sha256: Optional[str] = None) -> Dict[str, Any]:
    """Проверка Vision кэша в S3."""
    try:
        if sha256:
            # Проверяем конкретный файл
            # Формат: vision/{tenant}/{sha256[:2]}/{sha256}_{provider}_{model}_v{schema}.json
            # Упрощённая проверка
            prefix = f"vision/{tenant_id}/{sha256[:2]}"
            objects = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: list(s3_service.s3_client.list_objects_v2(
                    Bucket=s3_service.bucket_name,
                    Prefix=prefix
                ).get('Contents', []))
            )
            
            return {
                "found": len(objects) > 0,
                "objects_count": len(objects),
                "objects": [obj['Key'] for obj in objects[:5]]  # Первые 5
            }
        else:
            # Общая статистика
            prefix = f"vision/{tenant_id}/"
            objects = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: list(s3_service.s3_client.list_objects_v2(
                    Bucket=s3_service.bucket_name,
                    Prefix=prefix
                ).get('Contents', []))
            )
            
            total_size = sum(obj['Size'] for obj in objects)
            
            return {
                "objects_count": len(objects),
                "total_size_gb": total_size / (1024 ** 3),
                "sample_keys": [obj['Key'] for obj in objects[:5]]
            }
        
    except Exception as e:
        return {
            "status": "error",
            "error": str(e)
        }


async def test_vision_policy(media_file: str, tenant_id: str, policy_engine) -> Dict[str, Any]:
    """Тест Vision Policy Engine."""
    try:
        # Проверяем размер файла
        file_size = os.path.getsize(media_file)
        file_size_mb = file_size / (1024 ** 2)
        
        # Получаем MIME тип (упрощённо)
        ext = Path(media_file).suffix.lower()
        mime_map = {
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.gif': 'image/gif',
            '.pdf': 'application/pdf'
        }
        mime_type = mime_map.get(ext, 'application/octet-stream')
        
        # Эмулируем media file
        class MockMediaFile:
            def __init__(self):
                self.size_bytes = file_size
                self.mime_type = mime_type
        
        media = MockMediaFile()
        
        # Проверяем policy
        decision = await policy_engine.evaluate_policy(
            tenant_id=tenant_id,
            media_file=media,
            daily_budget_remaining=100000
        )
        
        return {
            "file_size_mb": file_size_mb,
            "mime_type": mime_type,
            "allowed": decision.get("allowed", False),
            "reason": decision.get("reason"),
            "routing": decision.get("routing", {}),
            "policy_decision": decision
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
@click.option('--db-url', envvar='DATABASE_URL', help='Database URL')
@click.option('--redis-url', envvar='REDIS_URL', default='redis://localhost:6379', help='Redis URL')
@click.option('--tenant-id', envvar='S3_DEFAULT_TENANT_ID', help='Default tenant ID')
@click.pass_context
def cli(ctx, db_url, redis_url, tenant_id):
    """Диагностика Vision Analysis компонентов."""
    ctx.ensure_object(dict)
    ctx.obj['db_url'] = db_url
    ctx.obj['redis_url'] = redis_url
    ctx.obj['tenant_id'] = tenant_id


@cli.command()
@click.option('--post-id', required=True, help='Post ID для проверки')
@click.pass_context
def check(ctx, post_id: str):
    """Проверка Vision обогащения для поста."""
    console.print(f"[bold blue]Проверка Vision обогащения для поста:[/bold blue] {post_id}\n")
    
    if not ctx.obj['db_url']:
        console.print("[red]❌ DATABASE_URL не установлен[/red]")
        sys.exit(1)
    
    result = asyncio.run(check_vision_enrichment(post_id, ctx.obj['db_url']))
    
    if result.get('status') == 'error':
        console.print(f"[red]❌ Ошибка: {result.get('error')}[/red]")
        sys.exit(1)
    elif result.get('status') == 'not_found':
        console.print(f"[yellow]⚠️  {result.get('message')}[/yellow]")
        sys.exit(0)
    
    # Выводим результаты в таблице
    table = Table(title="Vision Enrichment Data")
    table.add_column("Field", style="cyan")
    table.add_column("Value", style="green")
    
    table.add_row("Status", result.get('status', 'unknown'))
    table.add_row("Analyzed At", result.get('analyzed_at') or 'N/A')
    table.add_row("Provider", result.get('provider') or 'N/A')
    table.add_row("Model", result.get('model') or 'N/A')
    table.add_row("Tokens Used", str(result.get('tokens_used', 0)))
    table.add_row("Is Meme", "✓" if result.get('is_meme') else "✗")
    table.add_row("Has Classification", "✓" if result.get('has_classification') else "✗")
    table.add_row("Has Description", "✓" if result.get('has_description') else "✗")
    table.add_row("Has OCR", "✓" if result.get('has_ocr') else "✗")
    table.add_row("S3 Keys Count", str(result.get('s3_vision_keys_count', 0)))
    
    console.print(table)
    
    # Дополнительные данные
    if result.get('classification'):
        console.print("\n[bold]Classification:[/bold]")
        console.print_json(json.dumps(result['classification'], default=str))
    
    if result.get('description_preview'):
        console.print(f"\n[bold]Description Preview:[/bold] {result['description_preview']}...")


@cli.command()
@click.option('--tenant-id', help='Tenant ID для статистики')
@click.pass_context
def stats(ctx, tenant_id: Optional[str]):
    """Статистика Vision событий и обогащений."""
    console.print("[bold blue]Статистика Vision компонентов[/bold blue]\n")
    
    tenant_id = tenant_id or ctx.obj['tenant_id']
    
    # Проверка Redis events
    console.print("[cyan]Redis Streams:[/cyan]")
    events = asyncio.run(check_vision_events(ctx.obj['redis_url']))
    
    if events.get('status') == 'error':
        console.print(f"[red]❌ Ошибка Redis: {events.get('error')}[/red]")
    else:
        table = Table()
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="green")
        
        table.add_row("Uploaded Events", str(events.get('uploaded_events', 0)))
        table.add_row("Analyzed Events", str(events.get('analyzed_events', 0)))
        table.add_row("Uploaded Pending", str(events.get('uploaded_pending', 0)))
        table.add_row("Analyzed Pending", str(events.get('analyzed_pending', 0)))
        
        console.print(table)
    
    # Проверка S3 cache
    if tenant_id:
        try:
            from api.services.s3_storage import S3StorageService
            
            s3_service = S3StorageService(
                endpoint_url=os.getenv('S3_ENDPOINT_URL', 'https://s3.cloud.ru'),
                access_key_id=os.getenv('S3_ACCESS_KEY_ID', ''),
                secret_access_key=os.getenv('S3_SECRET_ACCESS_KEY', ''),
                bucket_name=os.getenv('S3_BUCKET_NAME', 'test-467940'),
                region=os.getenv('S3_REGION', 'ru-central-1')
            )
            
            console.print(f"\n[cyan]S3 Vision Cache (tenant: {tenant_id}):[/cyan]")
            s3_stats = asyncio.run(check_s3_vision_cache(s3_service, tenant_id))
            
            if s3_stats.get('status') == 'error':
                console.print(f"[red]❌ Ошибка S3: {s3_stats.get('error')}[/red]")
            else:
                table2 = Table()
                table2.add_column("Metric", style="cyan")
                table2.add_column("Value", style="green")
                
                table2.add_row("Objects Count", str(s3_stats.get('objects_count', 0)))
                table2.add_row("Total Size (GB)", f"{s3_stats.get('total_size_gb', 0):.2f}")
                
                console.print(table2)
                
                if s3_stats.get('sample_keys'):
                    console.print("\n[dim]Sample keys:[/dim]")
                    for key in s3_stats['sample_keys']:
                        console.print(f"  • {key}")
                        
        except ImportError:
            console.print("[yellow]⚠️  S3 service не доступен (пропущено)[/yellow]")
        except Exception as e:
            console.print(f"[yellow]⚠️  S3 check failed: {str(e)}[/yellow]")


@cli.command()
@click.option('--media-file', required=True, type=click.Path(exists=True), help='Путь к медиа файлу')
@click.option('--tenant-id', help='Tenant ID')
@click.pass_context
def test(ctx, media_file: str, tenant_id: Optional[str]):
    """Тест Vision Policy Engine с медиа файлом."""
    console.print(f"[bold blue]Тест Vision Policy Engine[/bold blue]\n")
    
    tenant_id = tenant_id or ctx.obj['tenant_id']
    if not tenant_id:
        console.print("[red]❌ Tenant ID не указан[/red]")
        sys.exit(1)
    
    try:
        from worker.services.vision_policy_engine import VisionPolicyEngine
        
        policy_path = os.getenv('VISION_POLICY_CONFIG_PATH', 'worker/config/vision_policy.yml')
        policy_engine = VisionPolicyEngine(policy_config_path=policy_path)
        
        result = asyncio.run(test_vision_policy(media_file, tenant_id, policy_engine))
        
        if result.get('status') == 'error':
            console.print(f"[red]❌ Ошибка: {result.get('error')}[/red]")
            sys.exit(1)
        
        # Выводим результаты
        table = Table(title="Policy Decision")
        table.add_column("Field", style="cyan")
        table.add_column("Value", style="green")
        
        table.add_row("File Size (MB)", f"{result.get('file_size_mb', 0):.2f}")
        table.add_row("MIME Type", result.get('mime_type', 'N/A'))
        table.add_row("Allowed", "✓" if result.get('allowed') else "✗")
        table.add_row("Reason", result.get('reason') or 'N/A')
        
        if result.get('routing'):
            table.add_row("Routing", result.get('routing', {}).get('provider', 'N/A'))
        
        console.print(table)
        
        if result.get('policy_decision'):
            console.print("\n[bold]Полное решение policy:[/bold]")
            console.print_json(json.dumps(result['policy_decision'], default=str))
            
    except ImportError as e:
        console.print(f"[red]❌ Импорт не удался: {str(e)}[/red]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]❌ Ошибка: {str(e)}[/red]")
        sys.exit(1)


if __name__ == '__main__':
    cli()

