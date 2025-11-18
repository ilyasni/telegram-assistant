"""
History Replay Service - reverse итерация истории каналов с offset_date для ретроаналитики (Context7 P1.3).

Поддерживает:
- reverse=True итерацию (от старых к новым)
- offset_date для backfilling с идемпотентностью
- батчевую обработку для эффективного парсинга больших объемов
"""
import structlog
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional, AsyncGenerator
from telethon import TelegramClient
from telethon.tl.types import Channel

logger = structlog.get_logger()


async def backfill_channel_history(
    client: TelegramClient,
    channel: Channel,
    start_date: datetime,
    end_date: Optional[datetime] = None,
    batch_size: int = 100,
    max_messages: Optional[int] = None,
    redis_client: Optional[Any] = None,
    floodwait_manager: Optional[Any] = None,
    account_id: Optional[str] = None
) -> AsyncGenerator[List[Any], None]:
    """
    Backfilling истории канала с reverse итерацией (Context7 P1.3).
    
    Использует iter_messages с reverse=True и offset_date для последовательного парсинга
    истории канала от старых сообщений к новым с идемпотентностью.
    
    Args:
        client: Telethon TelegramClient
        channel: Канал для парсинга
        start_date: Дата начала backfilling (самая старая дата)
        end_date: Дата окончания backfilling (самая новая дата, по умолчанию now)
        batch_size: Размер батча сообщений
        max_messages: Максимальное количество сообщений для парсинга (None = без ограничений)
        redis_client: Redis клиент для cooldown (опционально)
        floodwait_manager: FloodWaitManager для управления лимитами (опционально)
        account_id: Идентификатор аккаунта для FloodWaitManager (опционально)
        
    Yields:
        Батчи сообщений (List[Message])
    """
    from services.telethon_retry import fetch_messages_with_retry
    
    if end_date is None:
        end_date = datetime.now(timezone.utc)
    
    # Нормализация дат к UTC
    if start_date.tzinfo is None:
        start_date = start_date.replace(tzinfo=timezone.utc)
    if end_date.tzinfo is None:
        end_date = end_date.replace(tzinfo=timezone.utc)
    
    # Context7 P1.3: Проверка корректности диапазона
    if start_date > end_date:
        logger.error("Invalid date range: start_date > end_date",
                    start_date=start_date.isoformat(),
                    end_date=end_date.isoformat())
        return
    
    current_offset = start_date
    total_messages = 0
    
    logger.info("Starting backfill history replay",
                channel_id=getattr(channel, 'id', None),
                start_date=start_date.isoformat(),
                end_date=end_date.isoformat(),
                batch_size=batch_size,
                max_messages=max_messages)
    
    while current_offset < end_date:
        # Проверка ограничения по количеству сообщений
        if max_messages and total_messages >= max_messages:
            logger.info("Reached max_messages limit",
                       total_messages=total_messages,
                       max_messages=max_messages)
            break
        
        try:
            # Context7 P1.3: Получаем сообщения с reverse=True и offset_date
            # reverse=True возвращает сообщения от старых к новым
            # offset_date=current_offset возвращает сообщения ПОСЛЕ этой даты (для backfilling)
            messages = await fetch_messages_with_retry(
                client=client,
                channel=channel,
                limit=batch_size,
                redis_client=redis_client,
                offset_date=current_offset,
                reverse=True,  # Context7 P1.3: Reverse итерация для backfilling
                floodwait_manager=floodwait_manager,
                account_id=account_id
            )
            
            if not messages:
                logger.info("No more messages found, backfill complete",
                           current_offset=current_offset.isoformat(),
                           total_messages=total_messages)
                break
            
            # Context7 P1.3: Обновляем current_offset на самую новую дату в батче
            # (для следующей итерации)
            batch_dates = [msg.date for msg in messages if msg.date]
            if batch_dates:
                # Находим самую новую дату в батче
                max_date_in_batch = max(batch_dates)
                if max_date_in_batch.tzinfo is None:
                    max_date_in_batch = max_date_in_batch.replace(tzinfo=timezone.utc)
                
                # Проверяем, не превысили ли end_date
                if max_date_in_batch >= end_date:
                    # Фильтруем сообщения до end_date
                    messages = [msg for msg in messages 
                              if msg.date and 
                              (msg.date.tzinfo or timezone.utc) and 
                              msg.date.replace(tzinfo=timezone.utc) < end_date]
                    if messages:
                        yield messages
                    logger.info("Reached end_date, backfill complete",
                               current_offset=current_offset.isoformat(),
                               end_date=end_date.isoformat(),
                               total_messages=total_messages + len(messages))
                    break
                
                # Обновляем current_offset на самую новую дату + 1 секунда
                # (чтобы избежать пропуска сообщений на границе)
                current_offset = max_date_in_batch + timedelta(seconds=1)
                total_messages += len(messages)
                
                logger.debug("Backfill batch processed",
                            current_offset=current_offset.isoformat(),
                            batch_size=len(messages),
                            total_messages=total_messages,
                            oldest_in_batch=min(batch_dates).isoformat() if batch_dates else None,
                            newest_in_batch=max_date_in_batch.isoformat())
                
                yield messages
            else:
                # Нет дат в сообщениях - пропускаем
                logger.warning("Batch has no dates, skipping",
                             batch_size=len(messages))
                break
                
        except Exception as e:
            logger.error("Failed to fetch backfill batch",
                        current_offset=current_offset.isoformat(),
                        error=str(e),
                        exc_info=True)
            # Продолжаем с следующей итерации (с увеличенным offset)
            # Это предотвращает застревание на одной точке
            current_offset += timedelta(days=1)
            
            if current_offset >= end_date:
                logger.info("Backfill terminated due to errors",
                           final_offset=current_offset.isoformat(),
                           total_messages=total_messages)
                break
    
    logger.info("Backfill history replay completed",
               channel_id=getattr(channel, 'id', None),
               total_messages=total_messages,
               start_date=start_date.isoformat(),
               end_date=end_date.isoformat(),
               final_offset=current_offset.isoformat())


async def replay_channel_period(
    client: TelegramClient,
    channel: Channel,
    since_date: datetime,
    until_date: Optional[datetime] = None,
    batch_size: int = 100,
    redis_client: Optional[Any] = None,
    floodwait_manager: Optional[Any] = None,
    account_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Replay периода истории канала (Context7 P1.3).
    
    Парсит все сообщения в указанном диапазоне дат с использованием reverse итерации.
    Обеспечивает идемпотентность через уникальный индекс (channel_id, telegram_message_id).
    
    Args:
        client: Telethon TelegramClient
        channel: Канал для парсинга
        since_date: Начальная дата (самая старая)
        until_date: Конечная дата (самая новая, по умолчанию now)
        batch_size: Размер батча сообщений
        redis_client: Redis клиент для cooldown (опционально)
        floodwait_manager: FloodWaitManager для управления лимитами (опционально)
        account_id: Идентификатор аккаунта для FloodWaitManager (опционально)
        
    Returns:
        Словарь со статистикой парсинга:
        {
            'total_batches': int,
            'total_messages': int,
            'start_date': datetime,
            'end_date': datetime,
            'completed': bool
        }
    """
    if until_date is None:
        until_date = datetime.now(timezone.utc)
    
    # Нормализация дат к UTC
    if since_date.tzinfo is None:
        since_date = since_date.replace(tzinfo=timezone.utc)
    if until_date.tzinfo is None:
        until_date = until_date.replace(tzinfo=timezone.utc)
    
    total_batches = 0
    total_messages = 0
    completed = False
    
    logger.info("Starting channel period replay",
                channel_id=getattr(channel, 'id', None),
                since_date=since_date.isoformat(),
                until_date=until_date.isoformat(),
                batch_size=batch_size)
    
    try:
        async for batch in backfill_channel_history(
            client=client,
            channel=channel,
            start_date=since_date,
            end_date=until_date,
            batch_size=batch_size,
            redis_client=redis_client,
            floodwait_manager=floodwait_manager,
            account_id=account_id
        ):
            total_batches += 1
            total_messages += len(batch)
            
            logger.debug("Replay batch processed",
                        batch_number=total_batches,
                        batch_size=len(batch),
                        total_messages=total_messages)
        
        completed = True
        
    except Exception as e:
        logger.error("Channel period replay failed",
                    channel_id=getattr(channel, 'id', None),
                    error=str(e),
                    total_batches=total_batches,
                    total_messages=total_messages,
                    exc_info=True)
    
    result = {
        'total_batches': total_batches,
        'total_messages': total_messages,
        'start_date': since_date,
        'end_date': until_date,
        'completed': completed
    }
    
    logger.info("Channel period replay completed",
               channel_id=getattr(channel, 'id', None),
               **result)
    
    return result

