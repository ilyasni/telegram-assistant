"""
Auth Diagnostics Middleware
===========================

"Детектор правды" для диагностики database_save_failed с точечной телеметрией.
"""

import logging
import time
import uuid
from typing import Optional, Dict, Any, Tuple
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError, SQLAlchemyError
from prometheus_client import Counter, Histogram

logger = logging.getLogger("auth.diagnostics")

# Prometheus метрики для детальной диагностики
AUTH_FINALIZE_ATTEMPTS = Counter(
    'auth_finalize_attempts_total',
    'Total auth finalize attempts',
    ['step', 'status']
)

AUTH_FINALIZE_FAILURES = Counter(
    'auth_finalize_failures_total',
    'Auth finalize failures by reason',
    ['reason', 'error_type', 'sqlstate']
)

AUTH_FINALIZE_DURATION = Histogram(
    'auth_finalize_duration_seconds',
    'Auth finalize duration by step',
    ['step']
)

AUTH_SESSION_LENGTH = Histogram(
    'auth_session_length_bytes',
    'Session string length distribution',
    buckets=[100, 500, 1000, 2000, 5000, 10000]
)


class AuthDiagnostics:
    """Детектор правды для диагностики auth finalize ошибок."""
    
    def __init__(self):
        self.correlation_id: Optional[str] = None
        self.user_id: Optional[str] = None
        self.session_length: Optional[int] = None
        self.active_session_id: Optional[str] = None
    
    def start_diagnosis(
        self, 
        user_id: str, 
        session_string: str, 
        active_session_id: Optional[str] = None
    ) -> str:
        """Начать диагностику с correlation_id."""
        self.correlation_id = str(uuid.uuid4())
        self.user_id = user_id
        self.session_length = len(session_string)
        self.active_session_id = active_session_id
        
        logger.info(
            "Auth finalize started",
            correlation_id=self.correlation_id,
            user_id=user_id,
            session_length=self.session_length,
            active_session_id=active_session_id
        )
        
        return self.correlation_id
    
    def log_step_start(self, step: str) -> None:
        """Логирование начала шага."""
        logger.info(
            f"Auth finalize step: {step}",
            correlation_id=self.correlation_id,
            user_id=self.user_id,
            step=step
        )
        AUTH_FINALIZE_ATTEMPTS.labels(step=step, status='started').inc()
    
    def log_step_success(self, step: str, duration: float) -> None:
        """Логирование успешного завершения шага."""
        logger.info(
            f"Auth finalize step success: {step}",
            correlation_id=self.correlation_id,
            user_id=self.user_id,
            step=step,
            duration=duration
        )
        AUTH_FINALIZE_ATTEMPTS.labels(step=step, status='success').inc()
        AUTH_FINALIZE_DURATION.labels(step=step).observe(duration)
    
    def log_step_failure(
        self, 
        step: str, 
        error: Exception, 
        duration: float,
        error_type: str = "unknown"
    ) -> None:
        """Логирование ошибки шага с детальной диагностикой."""
        sqlstate, pgerror, statement, params = self._extract_db_cause(error)
        
        logger.exception(
            f"Auth finalize step failed: {step}",
            correlation_id=self.correlation_id,
            user_id=self.user_id,
            step=step,
            duration=duration,
            error_type=error_type,
            sqlstate=sqlstate,
            pgerror=pgerror,
            statement=statement[:200] if statement else None,
            params_length=len(params) if params else 0,
            error_class=type(error).__name__,
            error_message=str(error)
        )
        
        AUTH_FINALIZE_ATTEMPTS.labels(step=step, status='failed').inc()
        AUTH_FINALIZE_FAILURES.labels(
            reason=error_type,
            error_type=type(error).__name__,
            sqlstate=sqlstate or 'none'
        ).inc()
        AUTH_FINALIZE_DURATION.labels(step=step).observe(duration)
    
    def _extract_db_cause(self, exc: Exception) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[list]]:
        """Извлечение деталей ошибки БД."""
        if isinstance(exc, DBAPIError):
            # SQLAlchemy -> psycopg
            orig = getattr(exc, "orig", None)
            if orig:
                pgcode = getattr(orig, "pgcode", None)
                pgerror = getattr(orig, "pgerror", None)
            else:
                pgcode = None
                pgerror = None
            
            statement = getattr(exc, "statement", None)
            params = getattr(exc, "params", None)
            
            return pgcode, pgerror, statement, params
        
        return None, None, None, None
    
    def log_session_metrics(self, session_string: str) -> None:
        """Логирование метрик сессии."""
        AUTH_SESSION_LENGTH.observe(len(session_string))
        
        logger.info(
            "Session metrics recorded",
            correlation_id=self.correlation_id,
            user_id=self.user_id,
            session_length=len(session_string)
        )


class AuthFinalizeDiagnostics:
    """Диагностика финализации авторизации с детектором правды."""
    
    def __init__(self):
        self.diagnostics = AuthDiagnostics()
    
    async def diagnose_session_upsert(
        self,
        session_saver,
        tenant_id: str,
        user_id: str,
        session_string: str,
        telegram_user_id: int,
        **kwargs
    ) -> Tuple[bool, Optional[str], Optional[str], Optional[str]]:
        """Диагностика upsert сессии с детальной телеметрией."""
        correlation_id = self.diagnostics.start_diagnosis(user_id, session_string)
        
        try:
            self.diagnostics.log_step_start("session_upsert")
            start_time = time.time()
            
            # Логирование метрик сессии
            self.diagnostics.log_session_metrics(session_string)
            
            # Вызов upsert с детальной диагностикой
            success, session_id, error_code, error_details = await session_saver.save_telegram_session(
                tenant_id=tenant_id,
                user_id=user_id,
                session_string=session_string,
                telegram_user_id=telegram_user_id,
                **kwargs
            )
            
            duration = time.time() - start_time
            
            if success:
                self.diagnostics.log_step_success("session_upsert", duration)
                return True, session_id, None, None
            else:
                # Ошибка в session_saver
                self.diagnostics.log_step_failure(
                    "session_upsert", 
                    Exception(f"Session saver failed: {error_code} - {error_details}"),
                    duration,
                    "session_saver_failed"
                )
                return False, None, error_code, error_details
                
        except IntegrityError as e:
            duration = time.time() - start_time
            self.diagnostics.log_step_failure("session_upsert", e, duration, "db_integrity")
            return False, None, "db_integrity", str(e)
            
        except OperationalError as e:
            duration = time.time() - start_time
            self.diagnostics.log_step_failure("session_upsert", e, duration, "db_operational")
            return False, None, "db_operational", str(e)
            
        except DBAPIError as e:
            duration = time.time() - start_time
            self.diagnostics.log_step_failure("session_upsert", e, duration, "db_generic")
            return False, None, "db_generic", str(e)
            
        except Exception as e:
            duration = time.time() - start_time
            self.diagnostics.log_step_failure("session_upsert", e, duration, "non_db")
            return False, None, "non_db", str(e)
    
    async def diagnose_domain_updates(
        self,
        db_session,
        user_id: str,
        telegram_user_id: int,
        **kwargs
    ) -> Tuple[bool, Optional[str]]:
        """Диагностика доменных обновлений."""
        try:
            self.diagnostics.log_step_start("domain_updates")
            start_time = time.time()
            
            # Проверка существования пользователя
            if not user_id or user_id == "0":
                raise ValueError(f"Invalid user_id: {user_id}")
            
            # Доменные обновления
            # TODO: Добавить конкретные доменные обновления
            
            duration = time.time() - start_time
            self.diagnostics.log_step_success("domain_updates", duration)
            return True, None
            
        except IntegrityError as e:
            duration = time.time() - start_time
            self.diagnostics.log_step_failure("domain_updates", e, duration, "db_integrity")
            return False, str(e)
            
        except OperationalError as e:
            duration = time.time() - start_time
            self.diagnostics.log_step_failure("domain_updates", e, duration, "db_operational")
            return False, str(e)
            
        except DBAPIError as e:
            duration = time.time() - start_time
            self.diagnostics.log_step_failure("domain_updates", e, duration, "db_generic")
            return False, str(e)
            
        except Exception as e:
            duration = time.time() - start_time
            self.diagnostics.log_step_failure("domain_updates", e, duration, "non_db")
            return False, str(e)
    
    async def diagnose_redis_updates(
        self,
        redis_client,
        redis_key: str,
        session_id: str,
        **kwargs
    ) -> Tuple[bool, Optional[str]]:
        """Диагностика обновлений Redis."""
        try:
            self.diagnostics.log_step_start("redis_updates")
            start_time = time.time()
            
            # Обновление Redis
            redis_client.hset(redis_key, mapping={
                "status": "authorized",
                "session_id": session_id,
                "reason": None
            })
            
            duration = time.time() - start_time
            self.diagnostics.log_step_success("redis_updates", duration)
            return True, None
            
        except Exception as e:
            duration = time.time() - start_time
            self.diagnostics.log_step_failure("redis_updates", e, duration, "redis_error")
            return False, str(e)


# Глобальный экземпляр для использования
auth_diagnostics = AuthFinalizeDiagnostics()
