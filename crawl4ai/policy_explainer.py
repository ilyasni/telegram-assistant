"""
Policy Explainer для Crawl4AI
[C7-ID: CRAWL4AI-EXPLAIN-001]

Объясняет решения политики обогащения для диагностики
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
import structlog

logger = structlog.get_logger()

# ============================================================================
# POLICY EXPLAINER
# ============================================================================

class PolicyExplainer:
    """
    Объясняет решения политики обогащения.
    
    Поддерживает:
    - Детальное объяснение причин пропуска/допуска
    - Логирование для диагностики
    - Метрики по причинам решений
    """
    
    def __init__(self):
        self.explanation_templates = {
            'no_urls': "Пост не содержит URL для обогащения",
            'no_trigger_tags': "Пост не содержит теги-триггеры для обогащения",
            'below_word_count': "Количество слов в посте ниже минимального порога",
            'rate_limited': "Превышен лимит запросов к хосту",
            'policy_denied': "Пост заблокирован политикой обогащения",
            'budget_exceeded': "Превышен бюджет обогащения для пользователя",
            'robots_txt_denied': "robots.txt запрещает обогащение",
            'invalid_url': "URL невалиден или недоступен",
            'content_filtered': "Контент отфильтрован по правилам безопасности",
            'success': "Пост успешно обогащен"
        }
        
        logger.info("PolicyExplainer initialized")
    
    def explain_decision(
        self, 
        post_data: Dict[str, Any],
        policy_config: Dict[str, Any],
        decision: str,
        details: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Создает детальное объяснение решения политики.
        
        Args:
            post_data: Данные поста
            policy_config: Конфигурация политики
            decision: Принятое решение
            details: Детали решения
            
        Returns:
            Объяснение решения
        """
        explanation = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'post_id': post_data.get('post_id'),
            'user_id': post_data.get('user_id'),
            'decision': decision,
            'reason': self.explanation_templates.get(decision, f"Неизвестная причина: {decision}"),
            'details': details,
            'policy_applied': self._get_applied_policy(policy_config),
            'post_metadata': self._extract_post_metadata(post_data),
            'recommendations': self._generate_recommendations(decision, details)
        }
        
        # Логирование объяснения
        logger.info("Policy decision explained",
                  post_id=post_data.get('post_id'),
                  decision=decision,
                  reason=explanation['reason'])
        
        return explanation
    
    def _get_applied_policy(self, policy_config: Dict[str, Any]) -> Dict[str, Any]:
        """Извлечение примененной политики."""
        return {
            'trigger_tags': policy_config.get('crawl4ai', {}).get('trigger_tags', []),
            'min_word_count': policy_config.get('crawl4ai', {}).get('min_word_count', 100),
            'rate_limits': policy_config.get('limits', {}).get('per_host', {}),
            'budget_limits': policy_config.get('limits', {}).get('per_user', {}),
            'content_filters': policy_config.get('quality', {}).get('content_filters', [])
        }
    
    def _extract_post_metadata(self, post_data: Dict[str, Any]) -> Dict[str, Any]:
        """Извлечение метаданных поста для объяснения."""
        return {
            'text_length': len(post_data.get('text', '')),
            'word_count': len(post_data.get('text', '').split()),
            'urls_count': len(post_data.get('urls', [])),
            'tags_count': len(post_data.get('tags', [])),
            'has_media': post_data.get('has_media', False),
            'language': post_data.get('language', 'unknown'),
            'posted_at': post_data.get('posted_at'),
            'channel_id': post_data.get('channel_id')
        }
    
    def _generate_recommendations(
        self, 
        decision: str, 
        details: Dict[str, Any]
    ) -> List[str]:
        """Генерация рекомендаций на основе решения."""
        recommendations = []
        
        if decision == 'no_trigger_tags':
            trigger_tags = details.get('trigger_tags', [])
            post_tags = details.get('post_tags', [])
            recommendations.append(
                f"Добавьте теги из списка триггеров: {', '.join(trigger_tags)}"
            )
            recommendations.append(
                f"Текущие теги поста: {', '.join(post_tags)}"
            )
        
        elif decision == 'below_word_count':
            word_count = details.get('word_count', 0)
            min_required = details.get('min_required', 100)
            recommendations.append(
                f"Увеличьте количество слов в посте. Текущее: {word_count}, требуется: {min_required}"
            )
        
        elif decision == 'rate_limited':
            host = details.get('url', '').split('/')[2] if details.get('url') else 'unknown'
            recommendations.append(
                f"Превышен лимит запросов к хосту {host}. Попробуйте позже."
            )
        
        elif decision == 'budget_exceeded':
            recommendations.append(
                "Превышен бюджет обогащения. Обратитесь к администратору для увеличения лимитов."
            )
        
        elif decision == 'robots_txt_denied':
            recommendations.append(
                "Сайт запрещает автоматическое обогащение согласно robots.txt"
            )
        
        elif decision == 'success':
            recommendations.append(
                "Пост успешно обогащен согласно политике"
            )
        
        return recommendations
    
    def explain_batch_decisions(
        self, 
        batch_results: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Объяснение решений для батча постов.
        
        Args:
            batch_results: Результаты обработки батча
            
        Returns:
            Сводное объяснение батча
        """
        total_posts = len(batch_results)
        successful = sum(1 for result in batch_results if result.get('success', False))
        failed = total_posts - successful
        
        # Группировка по причинам
        reasons = {}
        for result in batch_results:
            reason = result.get('reason', 'unknown')
            reasons[reason] = reasons.get(reason, 0) + 1
        
        # Статистика
        stats = {
            'total_posts': total_posts,
            'successful': successful,
            'failed': failed,
            'success_rate': successful / total_posts if total_posts > 0 else 0,
            'reasons_breakdown': reasons
        }
        
        # Рекомендации для батча
        batch_recommendations = []
        
        if stats['success_rate'] < 0.5:
            batch_recommendations.append(
                "Низкий процент успешного обогащения. Проверьте политики и лимиты."
            )
        
        if 'rate_limited' in reasons:
            batch_recommendations.append(
                "Частые ограничения по rate limit. Рассмотрите увеличение лимитов."
            )
        
        if 'below_word_count' in reasons:
            batch_recommendations.append(
                "Много постов с недостаточным количеством слов. Пересмотрите минимальный порог."
            )
        
        explanation = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'batch_stats': stats,
            'recommendations': batch_recommendations,
            'detailed_results': batch_results
        }
        
        logger.info("Batch decisions explained",
                  total_posts=total_posts,
                  success_rate=stats['success_rate'],
                  reasons_breakdown=reasons)
        
        return explanation
    
    def explain_policy_changes(
        self, 
        old_policy: Dict[str, Any],
        new_policy: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Объяснение изменений в политике.
        
        Args:
            old_policy: Старая политика
            new_policy: Новая политика
            
        Returns:
            Объяснение изменений
        """
        changes = []
        
        # Сравнение trigger_tags
        old_tags = set(old_policy.get('crawl4ai', {}).get('trigger_tags', []))
        new_tags = set(new_policy.get('crawl4ai', {}).get('trigger_tags', []))
        
        added_tags = new_tags - old_tags
        removed_tags = old_tags - new_tags
        
        if added_tags:
            changes.append(f"Добавлены теги-триггеры: {', '.join(added_tags)}")
        if removed_tags:
            changes.append(f"Удалены теги-триггеры: {', '.join(removed_tags)}")
        
        # Сравнение min_word_count
        old_min_words = old_policy.get('crawl4ai', {}).get('min_word_count', 100)
        new_min_words = new_policy.get('crawl4ai', {}).get('min_word_count', 100)
        
        if old_min_words != new_min_words:
            changes.append(
                f"Изменен минимальный порог слов: {old_min_words} → {new_min_words}"
            )
        
        # Сравнение rate limits
        old_rate_limit = old_policy.get('limits', {}).get('per_host', {}).get('requests_per_minute', 10)
        new_rate_limit = new_policy.get('limits', {}).get('per_host', {}).get('requests_per_minute', 10)
        
        if old_rate_limit != new_rate_limit:
            changes.append(
                f"Изменен rate limit: {old_rate_limit} → {new_rate_limit} запросов/минуту"
            )
        
        explanation = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'changes': changes,
            'impact_analysis': self._analyze_policy_impact(old_policy, new_policy),
            'recommendations': self._generate_policy_recommendations(changes)
        }
        
        logger.info("Policy changes explained",
                  changes_count=len(changes),
                  changes=changes)
        
        return explanation
    
    def _analyze_policy_impact(
        self, 
        old_policy: Dict[str, Any],
        new_policy: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Анализ влияния изменений политики."""
        impact = {
            'enrichment_coverage': 'unknown',
            'performance_impact': 'unknown',
            'cost_impact': 'unknown'
        }
        
        # Анализ влияния на покрытие обогащения
        old_tags = len(old_policy.get('crawl4ai', {}).get('trigger_tags', []))
        new_tags = len(new_policy.get('crawl4ai', {}).get('trigger_tags', []))
        
        if new_tags > old_tags:
            impact['enrichment_coverage'] = 'increased'
        elif new_tags < old_tags:
            impact['enrichment_coverage'] = 'decreased'
        else:
            impact['enrichment_coverage'] = 'unchanged'
        
        # Анализ влияния на производительность
        old_min_words = old_policy.get('crawl4ai', {}).get('min_word_count', 100)
        new_min_words = new_policy.get('crawl4ai', {}).get('min_word_count', 100)
        
        if new_min_words < old_min_words:
            impact['performance_impact'] = 'increased_load'
        elif new_min_words > old_min_words:
            impact['performance_impact'] = 'decreased_load'
        else:
            impact['performance_impact'] = 'unchanged'
        
        return impact
    
    def _generate_policy_recommendations(self, changes: List[str]) -> List[str]:
        """Генерация рекомендаций по изменениям политики."""
        recommendations = []
        
        for change in changes:
            if 'добавлены теги-триггеры' in change.lower():
                recommendations.append(
                    "Мониторьте нагрузку на обогащение после добавления новых тегов"
                )
            
            elif 'удалены теги-триггеры' in change.lower():
                recommendations.append(
                    "Проверьте, не снизилось ли качество обогащения"
                )
            
            elif 'минимальный порог слов' in change.lower():
                recommendations.append(
                    "Отслеживайте статистику обогащения для оценки нового порога"
                )
            
            elif 'rate limit' in change.lower():
                recommendations.append(
                    "Мониторьте производительность и ошибки rate limiting"
                )
        
        return recommendations
