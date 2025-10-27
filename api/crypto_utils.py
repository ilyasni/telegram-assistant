"""Утилиты для шифрования StringSession."""

from cryptography.fernet import Fernet
import os
import structlog

logger = structlog.get_logger()


def get_encryption_key() -> bytes:
    """Получение ключа шифрования из переменной окружения."""
    key = os.getenv("ENCRYPTION_KEY")
    if not key:
        raise ValueError("ENCRYPTION_KEY environment variable is required")
    
    try:
        return key.encode() if isinstance(key, str) else key
    except Exception as e:
        logger.error("Failed to decode encryption key", error=str(e))
        raise ValueError("Invalid ENCRYPTION_KEY format")


def encrypt_session(session_string: str) -> str:
    """Шифрование StringSession для безопасного хранения."""
    try:
        key = get_encryption_key()
        cipher = Fernet(key)
        encrypted = cipher.encrypt(session_string.encode())
        return encrypted.decode()
    except Exception as e:
        logger.error("Failed to encrypt session", error=str(e))
        raise


def decrypt_session(encrypted_session: str) -> str:
    """Расшифровка StringSession."""
    try:
        key = get_encryption_key()
        cipher = Fernet(key)
        decrypted = cipher.decrypt(encrypted_session.encode())
        return decrypted.decode()
    except Exception as e:
        logger.error("Failed to decrypt session", error=str(e))
        raise


def generate_encryption_key() -> str:
    """Генерация нового ключа шифрования (для .env)."""
    return Fernet.generate_key().decode()
