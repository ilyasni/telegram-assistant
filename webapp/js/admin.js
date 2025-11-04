// [C7-ID: miniapp-admin-002] Основной модуль админки

let isAdmin = false;
let currentSection = 'users'; // 'users', 'invites', 'subscriptions'
let accessToken = null;

/**
 * Инициализация админки
 * [C7-ID: miniapp-admin-002] Context7: Проверка роли уже выполнена в index.html
 */
async function initAdmin() {
    console.log('[Admin] Initializing admin panel...');
    
    // [C7-ID: miniapp-admin-002] Инициализация Telegram WebApp SDK
    if (window.Telegram && window.Telegram.WebApp) {
        window.Telegram.WebApp.ready();
        window.Telegram.WebApp.expand();
        
        // Поддержка темной/светлой темы
        const themeParams = window.Telegram.WebApp.themeParams;
        if (themeParams) {
            updateTheme(themeParams);
        }
        
        // Обработка изменения темы
        window.Telegram.WebApp.onEvent('themeChanged', () => {
            updateTheme(window.Telegram.WebApp.themeParams);
        });
        
        // Обработка изменения viewport
        window.Telegram.WebApp.onEvent('viewportChanged', () => {
            handleViewportChange();
        });
    }
    
    // Context7: Проверка прав доступа (упрощенная, т.к. основная проверка уже в index.html)
    // Проверяем только наличие токена и корректность роли
    accessToken = getAccessToken();
    
    if (!accessToken) {
        console.error('[Admin] No access token available');
        showAdminAccessDenied();
        return;
    }
    
    // Дополнительная проверка роли из токена
    try {
        const payload = parseJWT(accessToken);
        isAdmin = payload.role === 'admin';
        
        if (!isAdmin) {
            console.warn('[Admin] User role is not admin:', payload.role);
            showAdminAccessDenied();
            return;
        }
    } catch (e) {
        console.error('[Admin] Failed to parse JWT:', e);
        showAdminAccessDenied();
        return;
    }
    
    // Инициализация админ-панели
    console.log('[Admin] Showing admin panel');
    showAdminPanel();
    loadCurrentSection();
}

/**
 * Проверка прав доступа админа
 */
async function checkAdminAccess() {
    try {
        // Получаем access token из глобальной переменной или localStorage
        accessToken = getAccessToken();
        
        if (!accessToken) {
            // Попытка аутентификации
            const initData = window.Telegram?.WebApp?.initData;
            if (initData) {
                const authResponse = await fetch('/api/auth/telegram-webapp', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ init_data: initData })
                });
                
                if (authResponse.ok) {
                    const authData = await authResponse.json();
                    accessToken = authData.access_token;
                    if (typeof window !== 'undefined') {
                        window.accessToken = accessToken;
                    }
                }
            }
        }
        
        if (!accessToken) {
            isAdmin = false;
            return;
        }
        
        // Проверяем роль из JWT токена
        try {
            const payload = parseJWT(accessToken);
            isAdmin = payload.role === 'admin';
        } catch (e) {
            console.error('Failed to parse JWT:', e);
            isAdmin = false;
        }
        
    } catch (error) {
        console.error('Admin access check failed:', error);
        isAdmin = false;
    }
}

/**
 * Парсинг JWT токена
 * [C7-ID: security-jwt-parse-001] Context7: Правильная обработка base64 padding
 */
function parseJWT(token) {
    try {
        const parts = token.split('.');
        if (parts.length !== 3) {
            throw new Error('Invalid token format');
        }
        
        // Context7: Правильная обработка base64 с padding
        let payloadBase64 = parts[1];
        payloadBase64 = payloadBase64.replace(/-/g, '+').replace(/_/g, '/');
        // Добавляем padding если нужно
        while (payloadBase64.length % 4) {
            payloadBase64 += '=';
        }
        
        const decoded = atob(payloadBase64);
        return JSON.parse(decoded);
    } catch (error) {
        console.error('[Admin] JWT parse error:', error);
        throw error;
    }
}

/**
 * Получение access token
 */
function getAccessToken() {
    if (typeof window !== 'undefined' && window.accessToken) {
        return window.accessToken;
    }
    
    if (typeof localStorage !== 'undefined') {
        return localStorage.getItem('admin_access_token');
    }
    
    return null;
}

/**
 * Обновление темы
 */
function updateTheme(themeParams) {
    if (!themeParams) return;
    
    const root = document.documentElement;
    
    if (themeParams.bg_color) {
        root.style.setProperty('--tg-theme-bg-color', themeParams.bg_color);
    }
    if (themeParams.text_color) {
        root.style.setProperty('--tg-theme-text-color', themeParams.text_color);
    }
    if (themeParams.hint_color) {
        root.style.setProperty('--tg-theme-hint-color', themeParams.hint_color);
    }
    if (themeParams.button_color) {
        root.style.setProperty('--tg-theme-button-color', themeParams.button_color);
    }
    if (themeParams.button_text_color) {
        root.style.setProperty('--tg-theme-button-text-color', themeParams.button_text_color);
    }
    if (themeParams.secondary_bg_color) {
        root.style.setProperty('--tg-theme-secondary-bg-color', themeParams.secondary_bg_color);
    }
}

/**
 * Обработка изменения viewport
 */
function handleViewportChange() {
    if (window.Telegram && window.Telegram.WebApp) {
        const viewport = window.Telegram.WebApp.viewportHeight;
        // Можно использовать для адаптации UI
        document.documentElement.style.setProperty('--tg-viewport-height', `${viewport}px`);
    }
}

/**
 * Показ сообщения об отсутствии доступа
 */
function showAdminAccessDenied() {
    const adminSection = document.getElementById('admin-section');
    if (adminSection) {
        adminSection.innerHTML = `
            <div class="admin-access-denied">
                <h2>⛔ Доступ запрещён</h2>
                <p>У вас нет прав администратора для доступа к этой панели.</p>
            </div>
        `;
    }
}

/**
 * Показ админ-панели
 */
function showAdminPanel() {
    const adminSection = document.getElementById('admin-section');
    if (!adminSection) return;
    
    adminSection.innerHTML = `
        <div class="admin-panel">
            <div class="admin-header">
                <h1>👑 Админ-панель</h1>
            </div>
            
            <div class="admin-tabs">
                <button class="tab-btn ${currentSection === 'users' ? 'active' : ''}" 
                        onclick="switchAdminSection('users')">
                    👥 Пользователи
                </button>
                <button class="tab-btn ${currentSection === 'invites' ? 'active' : ''}" 
                        onclick="switchAdminSection('invites')">
                    🎫 Инвайт-коды
                </button>
                <button class="tab-btn ${currentSection === 'subscriptions' ? 'active' : ''}" 
                        onclick="switchAdminSection('subscriptions')">
                    📋 Подписки
                </button>
            </div>
            
            <div id="admin-content" class="admin-content">
                <!-- Контент загружается динамически -->
            </div>
        </div>
    `;
}

/**
 * Переключение раздела админки
 */
function switchAdminSection(section) {
    currentSection = section;
    
    // Обновляем активные табы
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.classList.remove('active');
        if (btn.textContent.includes(section === 'users' ? 'Пользователи' : section === 'invites' ? 'Инвайт-коды' : 'Подписки')) {
            btn.classList.add('active');
        }
    });
    
    // Загружаем контент раздела
    loadCurrentSection();
}

// Делаем функцию глобальной
window.switchAdminSection = switchAdminSection;

/**
 * Загрузка текущего раздела
 */
function loadCurrentSection() {
    const content = document.getElementById('admin-content');
    if (!content) return;
    
    content.innerHTML = '<div class="loading">Загрузка...</div>';
    
    switch (currentSection) {
        case 'users':
            if (typeof loadUsersSection === 'function') {
                loadUsersSection();
            } else {
                content.innerHTML = '<p>Модуль управления пользователями не загружен</p>';
            }
            break;
        case 'invites':
            if (typeof loadInvitesSection === 'function') {
                loadInvitesSection();
            } else {
                content.innerHTML = '<p>Модуль управления инвайт-кодами не загружен</p>';
            }
            break;
        case 'subscriptions':
            if (typeof loadSubscriptionsSection === 'function') {
                loadSubscriptionsSection();
            } else {
                content.innerHTML = '<p>Модуль управления подписками не загружен</p>';
            }
            break;
        default:
            content.innerHTML = '<p>Неизвестный раздел</p>';
    }
}

// Context7: Инициализация админки вызывается явно из index.html после проверки роли
// Не вызываем автоматически, чтобы избежать конфликтов с проверкой роли
console.log('[Admin] Admin module loaded, waiting for explicit initAdmin() call');

// Делаем функцию initAdmin глобальной для вызова из index.html
window.initAdmin = initAdmin;

