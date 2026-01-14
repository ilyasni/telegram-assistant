// [C7-ID: MINIAPP-JS-001]
const API_BASE = '/api';
let accessToken = null;
let channels = [];
let manualChannels = [];
let themeChannels = [];
let currentView = 'all'; // 'all', 'manual', 'theme'

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
        // Загружаем все каналы (без фильтра по source)
        const resp = await fetch(`${API_BASE}/channels/users/${userId}/list?source=all`, {
            headers: {'Authorization': `Bearer ${accessToken}`}
        });
        
        if (resp.ok) {
            const data = await resp.json();
            channels = data.channels || [];
            
            // Разделяем каналы по источникам
            manualChannels = channels.filter(ch => ch.source === 'manual');
            themeChannels = channels.filter(ch => ch.source === 'theme');
            
            renderChannels();
            updateQuota(userId);
        } else {
            const errorData = await resp.json().catch(() => ({}));
            showToast(`Не удалось загрузить каналы: ${errorData.detail || resp.status}`, 'error');
        }
    } catch (e) {
        showToast('Ошибка сети при загрузке каналов', 'error');
    }
}

// Отображение каналов с разделением по источникам
function renderChannels() {
    const container = document.getElementById('channels-list');
    
    // Используем отфильтрованные данные, если есть поиск
    let displayManual = manualChannels;
    let displayTheme = themeChannels;
    let displayAll = channels;
    
    if (filteredChannels) {
        displayManual = filteredChannels.manual;
        displayTheme = filteredChannels.theme;
        displayAll = filteredChannels.all;
    }
    
    // Определяем, какие каналы показывать
    let channelsToRender = [];
    if (currentView === 'manual') {
        channelsToRender = displayManual;
    } else if (currentView === 'theme') {
        channelsToRender = displayTheme;
    } else {
        // 'all' - показываем все, но группируем
        channelsToRender = displayAll;
    }
    
    if (channelsToRender.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <h3>📺 Нет каналов</h3>
                <p>${currentView === 'manual' ? 'Добавьте канал вручную' : currentView === 'theme' ? 'Подключите подборку' : 'Добавьте канал или подключите подборку'}</p>
            </div>
        `;
        return;
    }
    
    // Если показываем все, группируем по источникам
    if (currentView === 'all') {
        const html = `
            ${displayManual.length > 0 ? `
                <div class="channels-section">
                    <h2 class="section-title">📝 Мои каналы (${displayManual.length})</h2>
                    ${renderChannelCards(displayManual)}
                </div>
            ` : ''}
            ${displayTheme.length > 0 ? `
                <div class="channels-section">
                    <h2 class="section-title">📚 Каналы из подборок (${displayTheme.length})</h2>
                    ${renderChannelCards(displayTheme)}
                </div>
            ` : ''}
        `;
        container.innerHTML = html;
    } else {
        container.innerHTML = renderChannelCards(channelsToRender);
    }
}

// Рендеринг карточек каналов
function renderChannelCards(channelsToRender) {
    return channelsToRender.map(channel => {
        // Определяем бейджи источников
        const badges = [];
        if (channel.source === 'manual') {
            badges.push('<span class="badge badge-manual">📝 Вручную</span>');
        }
        if (channel.source === 'theme' && channel.theme_id) {
            badges.push('<span class="badge badge-theme">📚 Подборка</span>');
        }
        
        // Если канал в обоих списках (проверяем по оригинальным массивам, не отфильтрованным)
        const channelInManual = manualChannels.some(c => c.id === channel.id);
        const channelInTheme = themeChannels.some(c => c.id === channel.id);
        const isInBoth = channelInManual && channelInTheme;
        
        const bothWarning = isInBoth && currentView === 'all' ? 
            '<p class="channel-hint">ℹ️ Отключение подборки не отключит канал, т.к. он подключён вручную</p>' : '';
        
        return `
            <div class="channel-card" data-channel-id="${channel.id}" data-source="${channel.source || 'manual'}">
                <div class="channel-info">
                    <div class="channel-header">
                        <h3>${escapeHtml(channel.title)}</h3>
                        <div class="channel-badges">${badges.join('')}</div>
                    </div>
                    <p>${channel.subscribers_count || 0} подписчиков</p>
                    ${bothWarning}
                </div>
                <div class="channel-actions">
                    <button class="btn btn-danger" onclick="deleteChannel('${channel.id}', '${channel.source || 'manual'}')">
                        🗑️
                    </button>
                </div>
            </div>
        `;
    }).join('');
}

// Переключение вида (all/manual/theme)
function switchView(view) {
    currentView = view;
    renderChannels();
    updateViewButtons();
    
    // Обновляем поиск при переключении вида
    const searchInput = document.getElementById('search');
    if (searchInput && searchInput.value) {
        searchInput.dispatchEvent(new Event('input'));
    }
}

// Обновление кнопок переключения вида
function updateViewButtons() {
    const buttons = document.querySelectorAll('.view-btn');
    buttons.forEach(btn => {
        if (btn.dataset.view === currentView) {
            btn.classList.add('active');
        } else {
            btn.classList.remove('active');
        }
    });
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
async function deleteChannel(channelId, source) {
    const userId = Telegram.WebApp.initDataUnsafe?.user?.id;
    
    if (!userId) {
        showToast('Ошибка: ID пользователя не найден', 'error');
        return;
    }
    
    // Проверяем, есть ли канал в обоих списках
    const isInManual = manualChannels.some(c => c.id === channelId);
    const isInTheme = themeChannels.some(c => c.id === channelId);
    
    let confirmMessage = 'Удалить канал из подписок?';
    if (isInManual && isInTheme) {
        if (source === 'theme') {
            confirmMessage = 'Отключить канал из подборки? Канал останется активным, т.к. подключён вручную.';
        } else {
            confirmMessage = 'Удалить канал? Он также подключён через подборку.';
        }
    }
    
    if (!confirm(confirmMessage)) {
        return;
    }
    
    try {
        const resp = await fetch(`${API_BASE}/channels/users/${userId}/unsubscribe/${channelId}`, {
            method: 'DELETE',
            headers: {'Authorization': `Bearer ${accessToken}`}
        });
        
        if (resp.status === 204) {
            if (isInManual && isInTheme && source === 'theme') {
                showToast('✅ Канал отключён из подборки, но остаётся активным (подключён вручную)', 'success');
            } else {
                showToast('✅ Канал удален', 'success');
            }
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
let searchQuery = '';
let filteredChannels = null; // Кэш отфильтрованных каналов

function setupSearch() {
    const searchInput = document.getElementById('search');
    if (!searchInput) return;
    
    searchInput.addEventListener('input', (e) => {
        searchQuery = e.target.value.toLowerCase();
        applySearchFilter();
    });
}

// Применение фильтра поиска
function applySearchFilter() {
    if (!searchQuery) {
        filteredChannels = null;
        renderChannels();
        return;
    }
    
    // Фильтруем в зависимости от текущего вида
    let filteredManual = manualChannels.filter(channel => 
        channel.title.toLowerCase().includes(searchQuery) ||
        (channel.username && channel.username.toLowerCase().includes(searchQuery))
    );
    
    let filteredTheme = themeChannels.filter(channel => 
        channel.title.toLowerCase().includes(searchQuery) ||
        (channel.username && channel.username.toLowerCase().includes(searchQuery))
    );
    
    let filteredAll = channels.filter(channel => 
        channel.title.toLowerCase().includes(searchQuery) ||
        (channel.username && channel.username.toLowerCase().includes(searchQuery))
    );
    
    // Сохраняем отфильтрованные данные
    filteredChannels = {
        all: filteredAll,
        manual: filteredManual,
        theme: filteredTheme
    };
    
    renderChannels();
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
            updateViewButtons(); // Инициализация кнопок переключения вида
        }
    } catch (e) {
        console.error('App initialization failed', e);
        showToast('Ошибка инициализации приложения', 'error');
    }
})();
