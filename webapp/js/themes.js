// [C7-ID: MINIAPP-THEMES-001] Управление подборками
const API_BASE = '/api';
let accessToken = null;
let themes = [];
let subscribedThemes = [];

// Инициализация Telegram WebApp
if (typeof Telegram !== 'undefined' && Telegram.WebApp) {
    Telegram.WebApp.ready();
    Telegram.WebApp.expand();
}

// Аутентификация
async function authenticate() {
    if (typeof Telegram === 'undefined' || !Telegram.WebApp) {
        console.warn('Telegram WebApp not available');
        return false;
    }
    
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

// Загрузка списка подборок
async function loadThemes() {
    try {
        const resp = await fetch(`${API_BASE}/themes?limit=100`, {
            headers: {'Authorization': `Bearer ${accessToken}`}
        });
        
        if (resp.ok) {
            const data = await resp.json();
            themes = data.themes || [];
            renderThemes();
        } else {
            const errorData = await resp.json().catch(() => ({}));
            showToast(`Не удалось загрузить подборки: ${errorData.detail || resp.status}`, 'error');
        }
    } catch (e) {
        showToast('Ошибка сети при загрузке подборок', 'error');
    }
}

// Загрузка подключенных подборок
async function loadSubscribedThemes() {
    const userId = Telegram?.WebApp?.initDataUnsafe?.user?.id;
    
    if (!userId) {
        return;
    }
    
    try {
        const resp = await fetch(`${API_BASE}/themes/users/${userId}/subscribed`, {
            headers: {'Authorization': `Bearer ${accessToken}`}
        });
        
        if (resp.ok) {
            const data = await resp.json();
            subscribedThemes = data.themes || [];
            updateSubscribedBadges();
        }
    } catch (e) {
        console.error('Failed to load subscribed themes', e);
    }
}

// Отображение подборок
function renderThemes() {
    const container = document.getElementById('themes-list');
    if (!container) return;
    
    if (themes.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <h3>📚 Нет подборок</h3>
                <p>Подборки будут доступны после синхронизации</p>
            </div>
        `;
        return;
    }
    
    const html = themes.map(theme => {
        const isSubscribed = subscribedThemes.some(t => t.theme_id === theme.id);
        
        return `
            <div class="theme-card" data-theme-id="${theme.id}">
                <div class="theme-info">
                    <h3>${escapeHtml(theme.name)}</h3>
                    <p>${theme.description || ''}</p>
                    <div class="theme-meta">
                        <span>📺 ${theme.channels_count} каналов</span>
                        ${isSubscribed ? '<span class="badge badge-subscribed">✓ Подключено</span>' : ''}
                    </div>
                </div>
                <div class="theme-actions">
                    ${isSubscribed ? 
                        `<button class="btn btn-secondary" onclick="unsubscribeTheme('${theme.slug}')">Отключить</button>` :
                        `<button class="btn btn-primary" onclick="subscribeTheme('${theme.slug}')">Подключить</button>`
                    }
                </div>
            </div>
        `;
    }).join('');
    
    container.innerHTML = html;
}

// Обновление бейджей подключенных подборок
function updateSubscribedBadges() {
    themes.forEach(theme => {
        const card = document.querySelector(`[data-theme-id="${theme.id}"]`);
        if (card) {
            const isSubscribed = subscribedThemes.some(t => t.theme_id === theme.id);
            const badge = card.querySelector('.badge-subscribed');
            const button = card.querySelector('.theme-actions button');
            
            if (isSubscribed && !badge) {
                const meta = card.querySelector('.theme-meta');
                if (meta) {
                    meta.innerHTML += '<span class="badge badge-subscribed">✓ Подключено</span>';
                }
                if (button) {
                    button.textContent = 'Отключить';
                    button.className = 'btn btn-secondary';
                    button.onclick = () => unsubscribeTheme(theme.slug);
                }
            } else if (!isSubscribed && badge) {
                badge.remove();
                if (button) {
                    button.textContent = 'Подключить';
                    button.className = 'btn btn-primary';
                    button.onclick = () => subscribeTheme(theme.slug);
                }
            }
        }
    });
}

// Подключение подборки
async function subscribeTheme(themeSlug) {
    const userId = Telegram?.WebApp?.initDataUnsafe?.user?.id;
    
    if (!userId) {
        showToast('Ошибка: ID пользователя не найден', 'error');
        return;
    }
    
    try {
        const resp = await fetch(`${API_BASE}/themes/${themeSlug}/subscribe/${userId}`, {
            method: 'POST',
            headers: {'Authorization': `Bearer ${accessToken}`}
        });
        
        if (resp.status === 201) {
            const data = await resp.json();
            showToast(`✅ Подборка подключена! Добавлено каналов: ${data.channels_added}`, 'success');
            await loadSubscribedThemes();
            renderThemes();
        } else {
            const errorData = await resp.json().catch(() => ({}));
            showToast(`❌ Ошибка: ${errorData.detail || resp.status}`, 'error');
        }
    } catch (e) {
        showToast('❌ Ошибка сети', 'error');
    }
}

// Отключение подборки
async function unsubscribeTheme(themeSlug) {
    const userId = Telegram?.WebApp?.initDataUnsafe?.user?.id;
    
    if (!userId) {
        showToast('Ошибка: ID пользователя не найден', 'error');
        return;
    }
    
    if (!confirm('Отключить подборку? Каналы из подборки будут отключены, но каналы, подключённые вручную, останутся активными.')) {
        return;
    }
    
    try {
        const resp = await fetch(`${API_BASE}/themes/${themeSlug}/unsubscribe/${userId}`, {
            method: 'DELETE',
            headers: {'Authorization': `Bearer ${accessToken}`}
        });
        
        if (resp.status === 200) {
            const data = await resp.json();
            showToast(`✅ Подборка отключена. Деактивировано каналов: ${data.channels_deactivated}`, 'success');
            await loadSubscribedThemes();
            renderThemes();
        } else {
            const errorData = await resp.json().catch(() => ({}));
            showToast(`❌ Ошибка: ${errorData.detail || resp.status}`, 'error');
        }
    } catch (e) {
        showToast('❌ Ошибка сети', 'error');
    }
}

// Toast уведомления
function showToast(message, type = 'info') {
    const toast = document.getElementById('toast');
    if (!toast) return;
    
    toast.textContent = message;
    toast.className = `toast show ${type}`;
    setTimeout(() => toast.className = 'toast', 3000);
}

// Экранирование HTML
function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Инициализация приложения
(async () => {
    try {
        if (await authenticate()) {
            await Promise.all([loadThemes(), loadSubscribedThemes()]);
        }
    } catch (e) {
        console.error('App initialization failed', e);
        showToast('Ошибка инициализации приложения', 'error');
    }
})();
