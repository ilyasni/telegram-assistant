"""
FSM States для Telegram Bot.
[C7-ID: BOT-FSM-001]

Состояния для различных потоков взаимодействия с пользователем.
"""

from aiogram.fsm.state import State, StatesGroup

class AddChannelStates(StatesGroup):
    """Состояния для добавления канала."""
    await_username = State()  # Ожидание username канала

class ChannelManagementStates(StatesGroup):
    """Состояния для управления каналами."""
    viewing_channel = State()  # Просмотр информации о канале
    confirming_delete = State()  # Подтверждение удаления канала

class SearchStates(StatesGroup):
    """Состояния для поиска."""
    awaiting_query = State()  # Ожидание поискового запроса

class SubscriptionStates(StatesGroup):
    """Состояния для управления подпиской."""
    viewing_plans = State()  # Просмотр тарифных планов
    confirming_upgrade = State()  # Подтверждение обновления тарифа
