"""Конфигурация сервиса TGStat Parser.
Context7: Используем pydantic-settings для валидации и загрузки из переменных окружения.
"""

from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    """Настройки сервиса TGStat Parser.
    
    Context7: Все настройки загружаются из переменных окружения с валидацией типов.
    """
    
    # Database
    database_url: str
    
    # TGStat
    tgstat_base_url: str = "https://tgstat.ru"
    
    # Logging
    log_level: str = "INFO"
    
    # Rate limiting
    rate_limit_per_second: float = 1.0  # Context7: 1 запрос в секунду
    rate_limit_max_requests: int = 200  # Context7: максимум 200 запросов за сессию
    
    # HTTP client
    http_timeout: int = 30  # Context7: таймаут для HTTP запросов (секунды)
    http_max_retries: int = 3  # Context7: количество попыток retry
    
    # Selenium browser
    selenium_headless: bool = False  # Context7: non-headless для обхода Cloudflare (Xvfb обеспечивает виртуальный display)
    selenium_page_load_timeout: int = 60  # Context7: таймаут для page load (секунды) - используем EAGER стратегию
    selenium_implicit_wait: int = 10  # Context7: неявное ожидание (секунды) - используем explicit waits вместо этого
    selenium_window_size: str = "1920,1080"  # Context7: размер окна браузера
    selenium_page_load_strategy: str = "none"  # Context7: NONE стратегия - максимально быстрая загрузка, используем explicit waits
    
    # Scheduler
    scheduler_timezone: str = "UTC"
    scheduler_monthly_hour: int = 3  # Context7: запуск в 03:00 UTC первого числа месяца
    scheduler_monthly_minute: int = 0
    
    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8020
    
    class Config:
        env_prefix = "TGSTAT_"
        case_sensitive = False


# Context7: Глобальный экземпляр настроек для использования во всём приложении
settings = Settings()
