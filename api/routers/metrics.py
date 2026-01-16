"""
Endpoint для агрегированной информации по метрикам.

Context7: Сводная информация по метрикам для dashboard и мониторинга.
"""

from fastapi import APIRouter
from typing import Dict, Any, List
import structlog
from prometheus_client import REGISTRY, generate_latest
from datetime import datetime, timezone

router = APIRouter()
logger = structlog.get_logger()


@router.get("/summary")
async def metrics_summary():
    """
    Агрегированная информация по метрикам системы.
    
    Context7: Сводная информация о состоянии всех компонентов,
    топ проблемных компонентов и рекомендации по улучшению.
    """
    try:
        summary = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "components": {},
            "issues": [],
            "recommendations": []
        }
        
        # Получаем все метрики из Prometheus registry
        metrics_data = {}
        for collector in REGISTRY._collector_to_names.keys():
            if collector is None:
                continue
            
            # Получаем метрики из коллектора
            if hasattr(collector, 'collect'):
                try:
                    for metric_family in collector.collect():
                        metrics_data[metric_family.name] = metric_family
                except Exception as e:
                    logger.debug("Failed to collect metrics", collector=str(collector), error=str(e))
        
        # Анализируем метрики scheduler
        scheduler_running = _get_metric_value(metrics_data, "scheduler_running")
        scheduler_jobs = _get_metric_value(metrics_data, "scheduler_jobs_total")
        
        summary["components"]["scheduler"] = {
            "running": scheduler_running == 1 if scheduler_running is not None else False,
            "jobs_count": int(scheduler_jobs) if scheduler_jobs is not None else 0,
            "status": "healthy" if scheduler_running == 1 else "unhealthy"
        }
        
        if scheduler_running != 1:
            summary["issues"].append({
                "component": "scheduler",
                "severity": "critical",
                "message": "Scheduler is not running",
                "recommendation": "Check logs and restart API service"
            })
        
        # Анализируем метрики health checks
        health_check_failures = _get_metric_value(metrics_data, "health_check_failures_total", label_filter={"check_type": "database"})
        if health_check_failures and health_check_failures > 5:
            summary["issues"].append({
                "component": "health_checks",
                "severity": "high",
                "message": f"Database health checks failing: {health_check_failures} failures",
                "recommendation": "Check database connection and circuit breaker status"
            })
        
        # Анализируем метрики пайплайна
        posts_parsed = _get_metric_value(metrics_data, "pipeline_posts_parsed_total")
        posts_tagged = _get_metric_value(metrics_data, "pipeline_posts_tagged_total")
        posts_indexed = _get_metric_value(metrics_data, "pipeline_posts_indexed_total")
        
        summary["components"]["pipeline"] = {
            "posts_parsed": int(posts_parsed) if posts_parsed else 0,
            "posts_tagged": int(posts_tagged) if posts_tagged else 0,
            "posts_indexed": int(posts_indexed) if posts_indexed else 0,
            "tagging_rate": round(posts_tagged / posts_parsed * 100, 2) if posts_parsed and posts_parsed > 0 else 0,
            "indexing_rate": round(posts_indexed / posts_parsed * 100, 2) if posts_parsed and posts_parsed > 0 else 0
        }
        
        # Анализируем pending сообщения
        pending_parsed = _get_metric_value(metrics_data, "redis_stream_pending_messages", label_filter={"stream": "stream:posts:parsed"})
        pending_tagged = _get_metric_value(metrics_data, "redis_stream_pending_messages", label_filter={"stream": "stream:posts:tagged"})
        
        total_pending = (pending_parsed or 0) + (pending_tagged or 0)
        
        if total_pending > 100:
            summary["issues"].append({
                "component": "redis_streams",
                "severity": "high",
                "message": f"High pending messages: {total_pending}",
                "recommendation": "Check worker tasks and increase concurrency"
            })
        
        # Формируем рекомендации
        if not summary["issues"]:
            summary["recommendations"].append("All systems operational")
        else:
            # Группируем проблемы по severity
            critical_count = sum(1 for issue in summary["issues"] if issue["severity"] == "critical")
            high_count = sum(1 for issue in summary["issues"] if issue["severity"] == "high")
            
            if critical_count > 0:
                summary["recommendations"].append(f"Address {critical_count} critical issue(s) immediately")
            if high_count > 0:
                summary["recommendations"].append(f"Monitor {high_count} high priority issue(s)")
        
        summary["status"] = "healthy" if not summary["issues"] else "degraded"
        
        return summary
        
    except Exception as e:
        logger.error("Failed to generate metrics summary", error=str(e), exc_info=True)
        return {
            "status": "error",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "error": str(e)
        }


def _get_metric_value(metrics_data: Dict, metric_name: str, label_filter: Dict[str, str] = None) -> Any:
    """
    Получить значение метрики из registry.
    
    Args:
        metrics_data: Словарь метрик
        metric_name: Имя метрики
        label_filter: Фильтр по labels
    
    Returns:
        Значение метрики или None
    """
    if metric_name not in metrics_data:
        return None
    
    metric_family = metrics_data[metric_name]
    
    # Обрабатываем разные типы метрик
    for sample in metric_family.samples:
        if label_filter:
            # Проверяем labels
            match = all(
                sample.labels.get(key) == value
                for key, value in label_filter.items()
            )
            if not match:
                continue
        
        return sample.value
    
    return None

