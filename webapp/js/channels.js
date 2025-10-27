// [C7-ID: MINIAPP-JS-001]
const API_BASE = '/api';
let accessToken = null;
let channels = [];

// Инициализация Telegram WebApp
Telegram.WebApp.ready();
Telegram.WebApp.expand();

// Аутентификация
async function authenticate() {
    const initData = Telegram.WebApp.initData;
    
    if (!initData) {
        showToast('Ошибка: initData не найден', 'error');
        return false;
    }
    
    try {
        const resp = await fetch(`${API_BASE}/auth/telegram-webapp`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({init_data: initData})
        });
        
        if (resp.ok) {
            const data = await resp.json();
            accessToken = data.access_token;
            return true;
        } else {
            const errorData = await resp.json().catch(() => ({}));
            showToast(`Ошибка аутентификации: ${errorData.detail || resp.status}`, 'error');
            return false;
        }
    } catch (e) {
        showToast('Ошибка сети при аутентификации', 'error');
        return false;
    }
}

// Загрузка каналов
async function loadChannels() {
    const userId = Telegram.WebApp.initDataUnsafe?.user?.id;
    
    if (!userId) {
        showToast('Ошибка: ID пользователя не найден', 'error');
        return;
    }
    
    try {
        const resp = await fetch(`${API_BASE}/channels/users/${userId}/list`, {
            headers: {'Authorization': `Bearer ${accessToken}`}
        });
        
        if (resp.ok) {
            const data = await resp.json();
            channels = data.channels || [];
            renderChannels(channels);
            updateQuota(userId);
        } else {
            const errorData = await resp.json().catch(() => ({}));
            showToast(`Не удалось загрузить каналы: ${errorData.detail || resp.status}`, 'error');
        }
    } catch (e) {
        showToast('Ошибка сети при загрузке каналов', 'error');
    }
}

// Отображение каналов
function renderChannels(channelsToRender) {
    const container = document.getElementById('channels-list');
    
    if (!channelsToRender || channelsToRender.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <h3>📺 Нет каналов</h3>
                <p>Добавьте канал, чтобы начать получать уведомления</p>
            </div>
        `;
        return;
    }
    
    const html = channelsToRender.map(channel => `
        <div class="channel-card" data-channel-id="${channel.id}">
            <div class="channel-info">
                <h3>${escapeHtml(channel.title)}</h3>
                <p>${channel.subscribers_count || 0} подписчиков</p>
            </div>
            <div class="channel-actions">
                <button class="btn btn-danger" onclick="deleteChannel('${channel.id}')">
                    🗑️
                </button>
            </div>
        </div>
    `).join('');
    
    container.innerHTML = html;
}

// Добавление канала
async function addChannel() {
    const username = document.getElementById('channel-username').value.trim();
    const userId = Telegram.WebApp.initDataUnsafe?.user?.id;
    
    if (!username) {
        showToast('Введите имя канала', 'warning');
        return;
    }
    
    if (!userId) {
        showToast('Ошибка: ID пользователя не найден', 'error');
        return;
    }
    
    // Валидация username
    if (!username.match(/^@?[a-zA-Z0-9_]{5,32}$/)) {
        showToast('Неверный формат канала. Используйте @channel_name', 'error');
        return;
    }
    
    try {
        const resp = await fetch(`${API_BASE}/channels/users/${userId}/subscribe`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${accessToken}`,
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({username})
        });
        
        if (resp.status === 201) {
            showToast('✅ Канал добавлен!', 'success');
            closeModal();
            loadChannels(); // Перезагрузка списка
        } else if (resp.status === 409) {
            showToast('⚠️ Вы уже подписаны на этот канал', 'warning');
        } else if (resp.status === 429) {
            const data = await resp.json();
            const resetTime = new Date(data.reset * 1000).toLocaleTimeString();
            showToast(`⏳ Превышен лимит запросов. Попробуйте после ${resetTime}`, 'error');
        } else if (resp.status === 403) {
            const data = await resp.json();
            showToast(`❌ Достигнут лимит: ${data.current}/${data.max}`, 'error');
        } else if (resp.status === 422) {
            showToast('❌ Неверный формат канала', 'error');
        } else {
            const errorData = await resp.json().catch(() => ({}));
            showToast(`❌ Ошибка: ${errorData.detail || resp.status}`, 'error');
        }
    } catch (e) {
        showToast('❌ Ошибка сети', 'error');
    }
}

// Удаление канала
async function deleteChannel(channelId) {
    const userId = Telegram.WebApp.initDataUnsafe?.user?.id;
    
    if (!userId) {
        showToast('Ошибка: ID пользователя не найден', 'error');
        return;
    }
    
    if (!confirm('Удалить канал из подписок?')) {
        return;
    }
    
    try {
        const resp = await fetch(`${API_BASE}/channels/users/${userId}/unsubscribe/${channelId}`, {
            method: 'DELETE',
            headers: {'Authorization': `Bearer ${accessToken}`}
        });
        
        if (resp.status === 204) {
            showToast('✅ Канал удален', 'success');
            loadChannels(); // Перезагрузка списка
        } else if (resp.status === 404) {
            showToast('❌ Канал не найден', 'error');
        } else {
            showToast('❌ Ошибка при удалении', 'error');
        }
    } catch (e) {
        showToast('❌ Ошибка сети', 'error');
    }
}

// Обновление квоты
async function updateQuota(userId) {
    try {
        const resp = await fetch(`${API_BASE}/channels/users/${userId}/stats`, {
            headers: {'Authorization': `Bearer ${accessToken}`}
        });
        
        if (resp.ok) {
            const data = await resp.json();
            document.getElementById('quota-text').textContent = 
                `${data.total}/${data.max_allowed} (${data.tier})`;
        }
    } catch (e) {
        console.error('Failed to update quota', e);
    }
}

// Поиск каналов
function setupSearch() {
    const searchInput = document.getElementById('search');
    searchInput.addEventListener('input', (e) => {
        const query = e.target.value.toLowerCase();
        const filtered = channels.filter(channel => 
            channel.title.toLowerCase().includes(query) ||
            (channel.username && channel.username.toLowerCase().includes(query))
        );
        renderChannels(filtered);
    });
}

// Модальное окно
function showAddModal() {
    document.getElementById('add-modal').classList.add('show');
    document.getElementById('channel-username').focus();
}

function closeModal() {
    document.getElementById('add-modal').classList.remove('show');
    document.getElementById('channel-username').value = '';
}

// Toast уведомления
function showToast(message, type = 'info') {
    const toast = document.getElementById('toast');
    toast.textContent = message;
    toast.className = `toast show ${type}`;
    setTimeout(() => toast.className = 'toast', 3000);
}

// Экранирование HTML
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Обработка клавиш в модальном окне
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        closeModal();
    }
});

// Обработка клика вне модального окна
document.getElementById('add-modal').addEventListener('click', (e) => {
    if (e.target.id === 'add-modal') {
        closeModal();
    }
});

// Инициализация приложения
(async () => {
    try {
        if (await authenticate()) {
            await loadChannels();
            setupSearch();
        }
    } catch (e) {
        console.error('App initialization failed', e);
        showToast('Ошибка инициализации приложения', 'error');
    }
})();
