// [C7-ID: miniapp-admin-005] Управление подписками

let subscriptionsPage = 0;
let subscriptionsLimit = 20;
let selectedUserId = null;

/**
 * Загрузка раздела подписок
 */
async function loadSubscriptionsSection() {
    const content = document.getElementById('admin-content');
    if (!content) return;
    
    content.innerHTML = `
        <div class="admin-subscriptions-section">
            <div class="admin-filters">
                <input type="text" 
                       id="subscriptions-user-search" 
                       placeholder="Поиск пользователя по ID или username..." 
                       class="filter-input"
                       onkeyup="debounceUserSearch(event)">
                <button class="btn btn-primary" onclick="loadUserSubscriptions()">
                    🔍 Загрузить подписки
                </button>
            </div>
            <div id="subscriptions-list" class="subscriptions-list">
                <div class="info-message">
                    Введите ID пользователя или username для загрузки подписок
                </div>
            </div>
        </div>
    `;
}

/**
 * Поиск пользователя (debounced)
 */
const debounceUserSearch = debounce(async (event) => {
    const searchTerm = event.target.value.trim();
    if (!searchTerm) {
        selectedUserId = null;
        return;
    }
    
    // Можно добавить автодополнение для поиска пользователя
    // Пока просто сохраняем значение
}, 500);

/**
 * Загрузка подписок пользователя
 */
async function loadUserSubscriptions() {
    const searchInput = document.getElementById('subscriptions-user-search');
    const userId = searchInput ? searchInput.value.trim() : null;
    
    if (!userId) {
        showToast('Введите ID пользователя', 'error');
        return;
    }
    
    const list = document.getElementById('subscriptions-list');
    if (!list) return;
    
    list.innerHTML = '<div class="loading">Загрузка подписок...</div>';
    
    try {
        const data = await adminApiCall(`/users/${userId}/subscriptions`);
        selectedUserId = userId;
        
        if (data.subscriptions.length === 0) {
            list.innerHTML = '<div class="empty-state">У пользователя нет подписок</div>';
            return;
        }
        
        renderSubscriptionsList(data.subscriptions, userId);
        
    } catch (error) {
        list.innerHTML = `<div class="error">Ошибка загрузки: ${error.message}</div>`;
        showToast(`Ошибка загрузки подписок: ${error.message}`, 'error');
    }
}

/**
 * Отображение списка подписок
 */
function renderSubscriptionsList(subscriptions, userId) {
    const list = document.getElementById('subscriptions-list');
    if (!list) return;
    
    const groupedByType = {
        channels: subscriptions.filter(s => s.type === 'channel'),
        groups: subscriptions.filter(s => s.type === 'group')
    };
    
    list.innerHTML = `
        <div class="subscriptions-summary">
            <h3>Всего подписок: ${subscriptions.length}</h3>
            <p>Каналы: ${groupedByType.channels.length}, Группы: ${groupedByType.groups.length}</p>
        </div>
        ${groupedByType.channels.length > 0 ? `
            <div class="subscriptions-group">
                <h4>📺 Каналы (${groupedByType.channels.length})</h4>
                ${renderSubscriptionsGroup(groupedByType.channels, userId)}
            </div>
        ` : ''}
        ${groupedByType.groups.length > 0 ? `
            <div class="subscriptions-group">
                <h4>👥 Группы (${groupedByType.groups.length})</h4>
                ${renderSubscriptionsGroup(groupedByType.groups, userId)}
            </div>
        ` : ''}
    `;
}

/**
 * Отображение группы подписок
 */
function renderSubscriptionsGroup(subscriptions, userId) {
    return subscriptions.map(sub => `
        <div class="subscription-card" data-subscription-id="${sub.id}">
            <div class="subscription-info">
                <div class="subscription-title">
                    ${escapeHtml(sub.channel_title || sub.group_title || 'Без названия')}
                </div>
                <div class="subscription-meta">
                    <span>${formatStatus(null, sub.is_active)}</span>
                    <span>Подписка: ${formatDate(sub.subscribed_at)}</span>
                </div>
                <div class="subscription-id">
                    ID: ${sub.channel_id || sub.group_id}
                </div>
            </div>
            <div class="subscription-actions">
                <button class="btn btn-sm ${sub.is_active ? 'btn-warning' : 'btn-success'}" 
                        onclick="toggleSubscriptionStatus('${userId}', '${sub.id}', ${sub.is_active})">
                    ${sub.is_active ? 'Деактивировать' : 'Активировать'}
                </button>
            </div>
        </div>
    `).join('');
}

/**
 * Переключение статуса подписки
 */
async function toggleSubscriptionStatus(userId, subscriptionId, currentStatus) {
    const confirmed = await showConfirm(
        `Вы уверены, что хотите ${currentStatus ? 'деактивировать' : 'активировать'} эту подписку?`
    );
    
    if (!confirmed) return;
    
    try {
        await adminApiCall(`/users/${userId}/subscriptions/${subscriptionId}`, {
            method: 'PUT',
            body: JSON.stringify({ is_active: !currentStatus })
        });
        
        showToast(`Подписка ${!currentStatus ? 'активирована' : 'деактивирована'}`, 'success');
        loadUserSubscriptions();
    } catch (error) {
        showToast(`Ошибка изменения подписки: ${error.message}`, 'error');
    }
}

