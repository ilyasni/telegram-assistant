"""
SaluteSpeech Service для транскрибации голосовых сообщений
Context7: кэширование результатов транскрибации
"""

import time
import hashlib
import base64
import uuid
from typing import Optional, Dict, Any
from datetime import datetime, timezone, timedelta
import structlog
import httpx
import redis.asyncio as redis
from config import settings

logger = structlog.get_logger()

# ============================================================================
# SALUTESPEECH SERVICE
# ============================================================================

class SaluteSpeechService:
    """Сервис для транскрибации голосовых сообщений через SaluteSpeech API."""
    
    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        scope: Optional[str] = None,
        api_url: Optional[str] = None,
        redis_client: Optional[redis.Redis] = None
    ):
        """
        Инициализация SaluteSpeech Service.
        
        Args:
            client_id: Client ID для SaluteSpeech API
            client_secret: Client Secret для SaluteSpeech API
            scope: Scope для SaluteSpeech API
            api_url: URL SaluteSpeech API
            redis_client: Redis клиент для кэширования
        """
        self.client_id = client_id or settings.salutespeech_client_id
        self.client_secret = client_secret or settings.salutespeech_client_secret.get_secret_value()
        self.scope = scope or settings.salutespeech_scope
        self.api_url = api_url or settings.salutespeech_url
        self.redis_client = redis_client
        self.cache_ttl = settings.voice_cache_ttl
        
        # Токен доступа (будет обновляться при необходимости)
        self._access_token: Optional[str] = None
        self._token_expires_at: Optional[datetime] = None
        
        logger.info(
            "SaluteSpeech Service initialized",
            api_url=self.api_url,
            cache_enabled=redis_client is not None
        )
    
    async def _get_access_token(self) -> str:
        """
        Получение access token для SaluteSpeech API.
        
        Context7: Кэширование токена до истечения срока действия.
        """
        # Проверяем, есть ли валидный токен
        if self._access_token and self._token_expires_at:
            if datetime.now(timezone.utc) < self._token_expires_at:
                return self._access_token
        
        # Получаем новый токен
        try:
            # Context7: Используем сертификат НУЦ Минцифры, установленный в Dockerfile
            # Согласно документации: https://developers.sber.ru/docs/ru/salutespeech/quick-start/certificates
            # Endpoint для получения токена: POST https://ngw.devices.sberbank.ru:9443/api/v2/oauth
            # Используется Basic авторизация с Authorization key (client_secret в base64)
            async with httpx.AsyncClient(timeout=10) as client:
                # Context7: Правильный endpoint согласно официальной документации SaluteSpeech
                token_url = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
                
                # Context7: Authorization key - это base64 кодированная пара Client ID:Client Secret
                # Согласно документации: https://developers.sber.ru/docs/ru/salutespeech/api/authentication
                # Формат: base64(client_id:client_secret)
                # Можно использовать готовый ключ из личного кабинета или сформировать самостоятельно
                if not self.client_secret:
                    raise Exception("SaluteSpeech Authorization key (client_secret) not configured")
                
                # Context7: Проверяем, является ли client_secret уже готовым Authorization Key
                # Готовый ключ из личного кабинета обычно длинный (80+ символов) и содержит только base64 символы
                # Если client_secret короткий (например, UUID) или содержит ':', то это Client Secret
                client_secret_clean = self.client_secret.strip()
                
                # Context7: Проверяем разные варианты
                # Вариант 1: Готовый Authorization Key (длинный base64, без ':', не начинается с Client ID)
                is_likely_authorization_key = (
                    len(client_secret_clean) > 60 and  # Длинный ключ (готовый Authorization Key)
                    ':' not in client_secret_clean and  # Не содержит ':' (не client_id:client_secret)
                    not client_secret_clean.startswith('0199') and  # Не начинается с Client ID
                    not client_secret_clean.startswith('d944') and  # Не начинается с Client Secret
                    not client_secret_clean.startswith('77e5')  # Не начинается с другого Client Secret
                )
                
                if is_likely_authorization_key:
                    # Используем готовый Authorization Key из личного кабинета
                    authorization_key = client_secret_clean
                    logger.info("Using provided Authorization Key from personal cabinet (base64)")
                elif self.client_id and self.client_secret:
                    # Вариант 2: Формируем Authorization Key из client_id:client_secret
                    # Согласно документации: base64(client_id:client_secret)
                    # Аналогично GigaChat, где используется client_id:client_secret
                    auth_pair = f"{self.client_id}:{self.client_secret}"
                    authorization_key = base64.b64encode(auth_pair.encode('utf-8')).decode('utf-8')
                    logger.info(
                        "Generated Authorization Key from client_id:client_secret",
                        client_id=self.client_id,
                        client_secret_length=len(self.client_secret),
                        auth_pair_length=len(auth_pair),
                        generated_key_length=len(authorization_key),
                        generated_key_prefix=authorization_key[:30] + "..."
                    )
                else:
                    # Fallback: используем client_secret как есть (может быть уже готовым ключом)
                    authorization_key = client_secret_clean
                    logger.warning(
                        "Using client_secret as Authorization Key without validation",
                        client_secret_length=len(client_secret_clean)
                    )
                
                if not authorization_key or len(authorization_key.strip()) < 10:
                    raise Exception("SaluteSpeech Authorization key is empty or too short")
                
                # Генерируем RqUID согласно документации
                rquid = str(uuid.uuid4())
                
                # Context7: Заголовки согласно документации SaluteSpeech
                # Authorization: Basic <base64_encoded_key> - ключ уже в base64 формате
                headers = {
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                    "RqUID": rquid,
                    "Authorization": f"Basic {authorization_key.strip()}"  # Убираем пробелы
                }
                
                # Context7: Scope передается в data согласно документации
                # Согласно примеру: 'scope': 'SALUTE_SPEECH_PERS' (БЕЗ пробела перед значением)
                data = {
                    "scope": self.scope.strip()  # Без пробела перед scope
                }
                
                # Context7: Детальное логирование для диагностики
                logger.info(
                    "Requesting SaluteSpeech token",
                    url=token_url,
                    scope=self.scope,
                    rquid=rquid,
                    has_authorization_key=bool(authorization_key),
                    auth_key_length=len(authorization_key) if authorization_key else 0,
                    auth_header_prefix=authorization_key[:30] + "..." if authorization_key and len(authorization_key) > 30 else authorization_key if authorization_key else None,
                    auth_header_full=authorization_key if authorization_key else None,  # Полный ключ для диагностики
                    client_id=self.client_id if self.client_id else None,
                    client_secret_length=len(self.client_secret) if self.client_secret else 0,
                    client_secret_prefix=self.client_secret[:20] + "..." if self.client_secret and len(self.client_secret) > 20 else self.client_secret if self.client_secret else None,
                    is_generated_key=not is_likely_authorization_key and self.client_id and self.client_secret,
                    authorization_header=f"Basic {authorization_key[:30]}..." if authorization_key and len(authorization_key) > 30 else f"Basic {authorization_key}" if authorization_key else None
                )
                
                response = await client.post(
                    token_url,
                    headers=headers,
                    data=data
                )
                
                # Context7: Детальное логирование для отладки
                logger.info(
                    "SaluteSpeech token response",
                    status_code=response.status_code,
                    response_headers=dict(response.headers),
                    response_text=response.text[:500] if response.text else None,
                    request_url=token_url,
                    request_scope=self.scope
                )
                
                if response.status_code == 200:
                    response_data = response.json()
                    self._access_token = response_data.get("access_token")
                    expires_in = response_data.get("expires_in", 3600)
                    
                    # Устанавливаем время истечения (с запасом 60 секунд)
                    self._token_expires_at = datetime.now(timezone.utc).replace(
                        second=0,
                        microsecond=0
                    ) + timedelta(seconds=expires_in - 60)
                    
                    logger.info(
                        "SaluteSpeech token obtained",
                        expires_in=expires_in,
                        token_prefix=self._access_token[:10] if self._access_token else None
                    )
                    return self._access_token
                else:
                    logger.error(
                        "Failed to get SaluteSpeech token",
                        status_code=response.status_code,
                        response_text=response.text[:500],
                        response_headers=dict(response.headers)
                    )
                    raise Exception(f"Failed to get token: {response.status_code} - {response.text[:200]}")
        
        except Exception as e:
            logger.error("Error getting SaluteSpeech token", error=str(e))
            raise
    
    def _get_cache_key(self, audio_data: bytes) -> str:
        """Генерация ключа кэша на основе хеша аудио данных."""
        audio_hash = hashlib.sha256(audio_data).hexdigest()
        return f"salutespeech:transcription:{audio_hash}"
    
    async def _get_from_cache(self, cache_key: str) -> Optional[str]:
        """Получение транскрипции из кэша."""
        if not self.redis_client:
            return None
        
        try:
            # Context7: Используем async Redis операции
            cached = await self.redis_client.get(cache_key)
            if cached:
                # Context7: decode_responses=True уже декодирует в строки
                return cached if isinstance(cached, str) else cached.decode('utf-8')
        except Exception as e:
            logger.warning("Error reading from cache", error=str(e))
        
        return None
    
    async def _save_to_cache(self, cache_key: str, transcription: str) -> None:
        """Сохранение транскрипции в кэш."""
        if not self.redis_client:
            return
        
        try:
            # Context7: Используем async Redis операции
            await self.redis_client.setex(cache_key, self.cache_ttl, transcription)
        except Exception as e:
            logger.warning("Error saving to cache", error=str(e))
    
    async def transcribe(
        self,
        audio_data: bytes,
        audio_format: str = "ogg_opus",
        language: str = "ru"
    ) -> Dict[str, Any]:
        """
        Транскрибация аудио через SaluteSpeech API.
        
        Args:
            audio_data: Байты аудио файла
            audio_format: Формат аудио (ogg_opus, wav, mp3 и т.д.)
            language: Язык (ru, en и т.д.)
        
        Returns:
            Dict с транскрипцией и метаданными
        """
        start_time = time.time()
        
        # Проверяем кэш
        cache_key = self._get_cache_key(audio_data)
        cached_transcription = await self._get_from_cache(cache_key)
        
        if cached_transcription:
            logger.debug("Transcription found in cache", cache_key=cache_key[:20])
            return {
                "text": cached_transcription,
                "cached": True,
                "processing_time_ms": int((time.time() - start_time) * 1000)
            }
        
        # Получаем токен доступа
        access_token = await self._get_access_token()
        
        try:
            # Context7: Используем сертификат НУЦ Минцифры, установленный в Dockerfile
            logger.debug(
                "Starting transcription request",
                api_url=self.api_url,
                audio_length=len(audio_data),
                audio_format=audio_format,
                language=language,
                has_access_token=bool(access_token),
                token_prefix=access_token[:10] if access_token else None
            )
            
            async with httpx.AsyncClient(timeout=60) as client:
                # Context7: Согласно документации SaluteSpeech API
                # Endpoint для синхронного распознавания речи: /rest/v1/speech:recognize
                # Аналогично синтезу: /rest/v1/text:synthesize
                # API требует отправку аудио напрямую с Content-Type audio/ogg;codecs=opus (не JSON!)
                stt_url = f"{self.api_url}/speech:recognize"
                
                # Context7: Определяем правильный Content-Type для аудио
                # Для OGG Opus: audio/ogg;codecs=opus
                content_type_map = {
                    "ogg_opus": "audio/ogg;codecs=opus",
                    "wav": "audio/x-pcm;bit=16;rate=16000",
                    "mp3": "audio/mpeg",
                    "flac": "audio/flac"
                }
                audio_content_type = content_type_map.get(audio_format, "audio/ogg;codecs=opus")
                
                logger.info(
                    "Sending transcription request",
                    url=stt_url,
                    api_url=self.api_url,
                    audio_length=len(audio_data),
                    audio_content_type=audio_content_type,
                    format=audio_format,
                    language=language,
                    has_access_token=bool(access_token)
                )
                
                # Context7: Согласно документации SaluteSpeech API для распознавания речи
                # Отправляем аудио напрямую (не base64 в JSON), с правильным Content-Type
                response = await client.post(
                    stt_url,
                    content=audio_data,  # Отправляем raw аудио данные
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": audio_content_type,  # Правильный Content-Type для аудио
                        "Accept": "application/json"
                    }
                )
                
                # Context7: Детальное логирование ответа
                logger.debug(
                    "Transcription response received",
                    status_code=response.status_code,
                    response_headers=dict(response.headers),
                    response_preview=response.text[:500] if response.text else None
                )
                
                if response.status_code == 200:
                    try:
                        data = response.json()
                        
                        # Context7: Согласно документации SaluteSpeech API
                        # Ответ содержит поле "result" с массивом строк распознанного текста
                        # Формат: {"result": ["распознанный текст"], "emotions": [...], "status": 200}
                        transcription = ""
                        
                        if "result" in data and isinstance(data["result"], list) and len(data["result"]) > 0:
                            # Берем первый элемент из массива result
                            transcription = data["result"][0]
                        elif "text" in data:
                            # Fallback: если есть поле text
                            transcription = data.get("text", "")
                        else:
                            # Пробуем найти текст в других полях
                            logger.warning(
                                "Unexpected response format",
                                response_data=data,
                                available_keys=list(data.keys()) if isinstance(data, dict) else None
                            )
                        
                        if not transcription:
                            logger.warning(
                                "Empty transcription received",
                                response_data=data,
                                audio_length=len(audio_data),
                                response_keys=list(data.keys()) if isinstance(data, dict) else None
                            )
                        
                        # Сохраняем в кэш
                        await self._save_to_cache(cache_key, transcription)
                        
                        processing_time = int((time.time() - start_time) * 1000)
                        
                        logger.info(
                            "Transcription completed",
                            audio_length=len(audio_data),
                            transcription_length=len(transcription),
                            processing_time_ms=processing_time,
                            cached=False
                        )
                        
                        return {
                            "text": transcription,
                            "cached": False,
                            "processing_time_ms": processing_time,
                            "language": language,
                            "format": audio_format
                        }
                    except Exception as parse_error:
                        logger.error(
                            "Error parsing transcription response",
                            error=str(parse_error),
                            status_code=response.status_code,
                            response_text=response.text[:500],
                            exc_info=True
                        )
                        raise Exception(f"Failed to parse transcription response: {str(parse_error)}")
                else:
                    logger.error(
                        "SaluteSpeech API error",
                        status_code=response.status_code,
                        response_text=response.text[:500],
                        response_headers=dict(response.headers),
                        stt_url=stt_url,
                        has_access_token=bool(access_token)
                    )
                    raise Exception(f"SaluteSpeech API error: {response.status_code} - {response.text[:200]}")
        
        except httpx.TimeoutException as timeout_error:
            logger.error(
                "SaluteSpeech API timeout",
                timeout_seconds=60,
                audio_length=len(audio_data),
                exc_info=True
            )
            raise Exception("Timeout while transcribing audio (60 seconds)")
        except httpx.RequestError as request_error:
            logger.error(
                "SaluteSpeech API request error",
                error=str(request_error),
                error_type=type(request_error).__name__,
                exc_info=True
            )
            raise Exception(f"Request error while transcribing audio: {str(request_error)}")
        except Exception as e:
            logger.error(
                "Error transcribing audio",
                error=str(e),
                error_type=type(e).__name__,
                audio_length=len(audio_data),
                audio_format=audio_format,
                language=language,
                exc_info=True
            )
            raise


# ============================================================================
# SINGLETON INSTANCE
# ============================================================================

_salutespeech_service: Optional[SaluteSpeechService] = None


def get_salutespeech_service(redis_client: Optional[redis.Redis] = None) -> SaluteSpeechService:
    """
    Получение singleton экземпляра SaluteSpeechService.
    
    Context7: Если redis_client не передан, создает async Redis клиент из settings.
    """
    global _salutespeech_service
    if _salutespeech_service is None:
        # Context7: Если redis_client не передан, создаем из settings
        if redis_client is None:
            redis_client = redis.from_url(settings.redis_url, decode_responses=True)
        _salutespeech_service = SaluteSpeechService(redis_client=redis_client)
    return _salutespeech_service

