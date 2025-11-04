// [C7-ID: miniapp-admin-003] Управление пользователями

let usersPage = 0;
let usersLimit = 20;
let usersFilter = {
    tier: null,
    role: null,
    search: null
};

/**
 * Загрузка раздела пользователей
 */
async function loadUsersSection() {
    const content = document.getElementById('admin-content');
    if (!content) return;
    
    content.innerHTML = `
        <div class="admin-users-section">
            <div class="admin-filters">
                <input type="text" 
                       id="users-search" 
                       placeholder="Поиск по имени, username..." 
                       class="filter-input"
                       onkeyup="debounceUsersSearch(event)">
                <select id="users-tier-filter" class="filter-select" onchange="filterUsersByTier()">
                    <option value="">Все tier</option>
                    <option value="free">Free</option>
                    <option value="basic">Basic</option>
                    <option value="premium">Premium</option>
                    <option value="pro">Pro</option>
                    <option value="enterprise">Enterprise</option>
                </select>
                <select id="users-role-filter" class="filter-select" onchange="filterUsersByRole()">
                    <option value="">Все роли</option>
                    <option value="user">Пользователь</option>
                    <option value="admin">Администратор</option>
                </select>
            </div>
            <div id="users-list" class="users-list">
                <div class="loading">Загрузка пользователей...</div>
            </div>
            <div id="users-pagination" class="pagination"></div>
        </div>
    `;
    
    await loadUsers();
}

/**
 * Загрузка списка пользователей
 * @param {boolean} forceRefresh - Принудительное обновление (cache-busting)
 */
async function loadUsers(forceRefresh = false) {
    const list = document.getElementById('users-list');
    if (!list) return;
    
    // Context7: Не показываем loader, если это обновление после изменения (избегаем мерцания)
    if (!forceRefresh) {
        list.innerHTML = '<div class="loading">Загрузка...</div>';
    }
    
    try {
        const params = new URLSearchParams({
            limit: usersLimit,
            offset: usersPage * usersLimit
        });
        
        if (usersFilter.tier) {
            params.append('tier', usersFilter.tier);
        }
        if (usersFilter.role) {
            params.append('role', usersFilter.role);
        }
        if (usersFilter.search) {
            params.append('search', usersFilter.search);
        }
        
        // Context7: Cache-busting для принудительного обновления
        if (forceRefresh) {
            params.append('_t', Date.now().toString());
        }
        
        const data = await adminApiCall(`/users?${params.toString()}`);
        
        if (data.users.length === 0) {
            list.innerHTML = '<div class="empty-state">Пользователи не найдены</div>';
            return;
        }
        
        renderUsersList(data.users);
        renderUsersPagination(data.total, data.limit, data.offset);
        
    } catch (error) {
        list.innerHTML = `<div class="error">Ошибка загрузки: ${error.message}</div>`;
        showToast(`Ошибка загрузки пользователей: ${error.message}`, 'error');
    }
}

/**
 * Отображение списка пользователей
 */
function renderUsersList(users) {
    const list = document.getElementById('users-list');
    if (!list) return;
    
    list.innerHTML = users.map(user => `
        <div class="user-card" data-user-id="${user.id}">
            <div class="user-info">
                <div class="user-name">
                    ${escapeHtml(user.first_name || '')} ${escapeHtml(user.last_name || '')}
                    ${user.username ? `(@${escapeHtml(user.username)})` : ''}
                </div>
                <div class="user-meta">
                    <span>${formatTier(user.tier)}</span>
                    <span>${formatRole(user.role)}</span>
                    <span>ID: ${user.telegram_id}</span>
                </div>
                <div class="user-date">
                    Создан: ${formatDate(user.created_at)}
                </div>
            </div>
            <div class="user-actions">
                <button class="btn btn-sm btn-primary" onclick="viewUserDetail('${user.id}')">
                    👁️ Детали
                </button>
                <button class="btn btn-sm btn-secondary" onclick="editUserTier('${user.id}', '${user.tier}')">
                    ✏️ Tier
                </button>
                <button class="btn btn-sm btn-secondary" onclick="editUserRole('${user.id}', '${user.role}')">
                    👤 Роль
                </button>
            </div>
        </div>
    `).join('');
}

/**
 * Отображение пагинации
 */
function renderUsersPagination(total, limit, offset) {
    const pagination = document.getElementById('users-pagination');
    if (!pagination) return;
    
    const totalPages = Math.ceil(total / limit);
    const currentPage = Math.floor(offset / limit) + 1;
    
    if (totalPages <= 1) {
        pagination.innerHTML = '';
        return;
    }
    
    let html = '<div class="pagination-controls">';
    
    if (currentPage > 1) {
        html += `<button class="btn btn-sm" onclick="goToUsersPage(${currentPage - 2})">← Назад</button>`;
    }
    
    html += `<span>Страница ${currentPage} из ${totalPages} (всего: ${total})</span>`;
    
    if (currentPage < totalPages) {
        html += `<button class="btn btn-sm" onclick="goToUsersPage(${currentPage})">Вперёд →</button>`;
    }
    
    html += '</div>';
    pagination.innerHTML = html;
}

/**
 * Переход на страницу
 */
function goToUsersPage(page) {
    usersPage = page;
    loadUsers();
}

/**
 * Поиск пользователей (debounced)
 */
const debounceUsersSearch = debounce((event) => {
    usersFilter.search = event.target.value.trim() || null;
    usersPage = 0;
    loadUsers();
}, 500);

/**
 * Фильтрация по tier
 */
function filterUsersByTier() {
    const select = document.getElementById('users-tier-filter');
    usersFilter.tier = select.value || null;
    usersPage = 0;
    loadUsers();
}

/**
 * Фильтрация по роли
 */
function filterUsersByRole() {
    const select = document.getElementById('users-role-filter');
    usersFilter.role = select.value || null;
    usersPage = 0;
    loadUsers();
}

/**
 * Просмотр деталей пользователя
 */
async function viewUserDetail(userId) {
    try {
        const user = await adminApiCall(`/users/${userId}`);
        
        const content = `
            <div class="user-detail">
                <div class="detail-item">
                    <strong>ID:</strong> ${user.id}
                </div>
                <div class="detail-item">
                    <strong>Telegram ID:</strong> ${user.telegram_id}
                </div>
                <div class="detail-item">
                    <strong>Username:</strong> ${user.username || '-'}
                </div>
                <div class="detail-item">
                    <strong>Имя:</strong> ${user.first_name || '-'} ${user.last_name || ''}
                </div>
                <div class="detail-item">
                    <strong>Tier:</strong> ${formatTier(user.tier)}
                </div>
                <div class="detail-item">
                    <strong>Роль:</strong> ${formatRole(user.role)}
                </div>
                <div class="detail-item">
                    <strong>Создан:</strong> ${formatDate(user.created_at)}
                </div>
                <div class="detail-item">
                    <strong>Последняя активность:</strong> ${formatDate(user.last_active_at) || '-'}
                </div>
                <div class="detail-actions" style="margin-top: 16px;">
                    <button class="btn btn-primary" onclick="viewUserSubscriptions('${user.id}')">
                        📋 Подписки
                    </button>
                    <button class="btn btn-secondary" onclick="editUserTier('${user.id}', '${user.tier}')">
                        ✏️ Изменить Tier
                    </button>
                    <button class="btn btn-secondary" onclick="editUserRole('${user.id}', '${user.role}')">
                        👤 Изменить Роль
                    </button>
                </div>
            </div>
        `;
        
        createModal('Детали пользователя', content);
        
    } catch (error) {
        showToast(`Ошибка загрузки деталей: ${error.message}`, 'error');
    }
}

/**
 * Редактирование tier пользователя
 * Context7: OCC - загружаем актуальные данные с version перед обновлением
 */
async function editUserTier(userId, currentTier) {
    // Context7: Загружаем актуальные данные пользователя с version для OCC
    let userData;
    try {
        userData = await adminApiCall(`/users/${userId}`);
        console.log('[Admin] Loaded user data for tier update:', { userId, tier: userData.tier, version: userData.version });
    } catch (error) {
        showToast(`Ошибка загрузки данных пользователя: ${error.message}`, 'error');
        return;
    }
    
    const fields = [
        {
            name: 'tier',
            label: 'Tier',
            type: 'select',
            required: true,
            options: [
                { value: 'free', label: 'Free', selected: userData.tier === 'free' },
                { value: 'basic', label: 'Basic', selected: userData.tier === 'basic' },
                { value: 'premium', label: 'Premium', selected: userData.tier === 'premium' },
                { value: 'pro', label: 'Pro', selected: userData.tier === 'pro' },
                { value: 'enterprise', label: 'Enterprise', selected: userData.tier === 'enterprise' }
            ]
        }
    ];
    
    createFormModal('Изменение Tier', fields, async (data) => {
        // Context7: Валидация данных перед отправкой
        console.log('[Admin] Form data received:', data);
        console.log('[Admin] Current tier:', userData.tier);
        console.log('[Admin] Selected tier:', data.tier);
        
        if (!data.tier) {
            showToast('Выберите tier', 'error');
            throw new Error('Tier не выбран');
        }
        
        // Context7: Проверка что значение действительно изменилось
        if (data.tier === userData.tier) {
            console.warn('[Admin] Tier not changed, skipping update');
            showToast('Tier не изменился', 'info');
            return; // Не обновляем если значение не изменилось
        }
        
        try {
            console.log('[Admin] Updating tier:', { userId, tier: data.tier, version: userData.version, oldTier: userData.tier });
            
            // Context7: Оптимистичное обновление UI до получения ответа
            updateUserCardOptimistically(userId, { tier: data.tier });
            
            // Context7: Передаем version для OCC (Optimistic Concurrency Control)
            const response = await adminApiCall(`/users/${userId}/tier`, {
                method: 'PUT',
                body: JSON.stringify({ 
                    tier: data.tier,
                    version: userData.version 
                })
            });
            
            console.log('[Admin] Tier update response:', response);
            
            // Context7: Обновляем карточку пользователя с актуальными данными (включая новую version)
            updateUserCard(userId, response);
            
            showToast('Tier успешно изменён', 'success');
            
            // Context7: Перезагружаем список пользователей после успешного обновления (с cache-busting)
            await loadUsers(true);
        } catch (error) {
            console.error('[Admin] Tier update error:', error);
            // Context7: Обработка version conflict (409)
            if (error.message && error.message.includes('Version conflict')) {
                showToast('Данные были изменены другим пользователем. Обновляю...', 'warning');
                // Перезагружаем актуальные данные
                await loadUsers(true);
                // Показываем сообщение о необходимости повторить операцию
                setTimeout(() => {
                    showToast('Пожалуйста, повторите операцию с актуальными данными', 'info');
                }, 1000);
            } else {
                // Context7: Откатываем оптимистичное обновление при ошибке
                await loadUsers(true);
                showToast(`Ошибка изменения tier: ${error.message}`, 'error');
            }
            throw error; // Пробрасываем ошибку, чтобы модальное окно не закрылось
        }
    });
}

/**
 * Редактирование роли пользователя
 * Context7: OCC - загружаем актуальные данные с version перед обновлением
 */
async function editUserRole(userId, currentRole) {
    // Context7: Загружаем актуальные данные пользователя с version для OCC
    let userData;
    try {
        userData = await adminApiCall(`/users/${userId}`);
        console.log('[Admin] Loaded user data for role update:', { userId, role: userData.role, version: userData.version });
    } catch (error) {
        showToast(`Ошибка загрузки данных пользователя: ${error.message}`, 'error');
        return;
    }
    
    const fields = [
        {
            name: 'role',
            label: 'Роль',
            type: 'select',
            required: true,
            options: [
                { value: 'user', label: 'Пользователь', selected: userData.role === 'user' },
                { value: 'admin', label: 'Администратор', selected: userData.role === 'admin' }
            ]
        }
    ];
    
    createFormModal('Изменение Роли', fields, async (data) => {
        // Context7: Валидация данных перед отправкой
        console.log('[Admin] Form data received:', data);
        console.log('[Admin] Current role:', userData.role);
        console.log('[Admin] Selected role:', data.role);
        
        if (!data.role) {
            showToast('Выберите роль', 'error');
            throw new Error('Роль не выбрана');
        }
        
        // Context7: Проверка что значение действительно изменилось
        if (data.role === userData.role) {
            console.warn('[Admin] Role not changed, skipping update');
            showToast('Роль не изменилась', 'info');
            return; // Не обновляем если значение не изменилось
        }
        
        try {
            console.log('[Admin] Updating role:', { userId, role: data.role, version: userData.version, oldRole: userData.role });
            
            // Context7: Оптимистичное обновление UI до получения ответа
            updateUserCardOptimistically(userId, { role: data.role });
            
            // Context7: Передаем version для OCC (Optimistic Concurrency Control)
            const response = await adminApiCall(`/users/${userId}/role`, {
                method: 'PUT',
                body: JSON.stringify({ 
                    role: data.role,
                    version: userData.version 
                })
            });
            
            console.log('[Admin] Role update response:', response);
            
            // Context7: Обновляем карточку пользователя с актуальными данными (включая новую version)
            updateUserCard(userId, response);
            
            showToast('Роль успешно изменена', 'success');
            
            // Context7: Перезагружаем список пользователей после успешного обновления (с cache-busting)
            await loadUsers(true);
        } catch (error) {
            console.error('[Admin] Role update error:', error);
            // Context7: Обработка version conflict (409)
            if (error.message && error.message.includes('Version conflict')) {
                showToast('Данные были изменены другим пользователем. Обновляю...', 'warning');
                // Перезагружаем актуальные данные
                await loadUsers(true);
                // Показываем сообщение о необходимости повторить операцию
                setTimeout(() => {
                    showToast('Пожалуйста, повторите операцию с актуальными данными', 'info');
                }, 1000);
            } else {
                // Context7: Откатываем оптимистичное обновление при ошибке
                await loadUsers(true);
                showToast(`Ошибка изменения роли: ${error.message}`, 'error');
            }
            throw error; // Пробрасываем ошибку, чтобы модальное окно не закрылось
        }
    });
}

/**
 * Просмотр подписок пользователя
 */
async function viewUserSubscriptions(userId) {
    try {
        const data = await adminApiCall(`/users/${userId}/subscriptions`);
        
        if (data.subscriptions.length === 0) {
            createModal('Подписки пользователя', '<p>У пользователя нет подписок</p>');
            return;
        }
        
        const content = `
            <div class="subscriptions-list">
                ${data.subscriptions.map(sub => `
                    <div class="subscription-item">
                        <div class="subscription-info">
                            <strong>${sub.type === 'channel' ? '📺 Канал' : '👥 Группа'}:</strong>
                            ${escapeHtml(sub.channel_title || sub.group_title || 'Без названия')}
                        </div>
                        <div class="subscription-meta">
                            <span>${formatStatus(null, sub.is_active)}</span>
                            <span>Подписка: ${formatDate(sub.subscribed_at)}</span>
                        </div>
                        <div class="subscription-actions">
                            <button class="btn btn-sm" 
                                    onclick="toggleSubscription('${userId}', '${sub.id}', ${sub.is_active})">
                                ${sub.is_active ? 'Деактивировать' : 'Активировать'}
                            </button>
                        </div>
                    </div>
                `).join('')}
            </div>
        `;
        
        createModal(`Подписки пользователя (${data.total})`, content);
        
    } catch (error) {
        showToast(`Ошибка загрузки подписок: ${error.message}`, 'error');
    }
}

/**
 * Переключение статуса подписки
 */
async function toggleSubscription(userId, subscriptionId, currentStatus) {
    const confirmed = await showConfirm(`Вы уверены, что хотите ${currentStatus ? 'деактивировать' : 'активировать'} эту подписку?`);
    
    if (!confirmed) return;
    
    try {
        await adminApiCall(`/users/${userId}/subscriptions/${subscriptionId}`, {
            method: 'PUT',
            body: JSON.stringify({ is_active: !currentStatus })
        });
        
        showToast(`Подписка ${!currentStatus ? 'активирована' : 'деактивирована'}`, 'success');
        viewUserSubscriptions(userId);
    } catch (error) {
        showToast(`Ошибка изменения подписки: ${error.message}`, 'error');
    }
}

/**
 * Context7: Оптимистичное обновление карточки пользователя
 */
function updateUserCardOptimistically(userId, updates) {
    const userCard = document.querySelector(`[data-user-id="${userId}"]`);
    if (!userCard) return;
    
    // Обновляем tier
    if (updates.tier !== undefined) {
        const metaSpans = userCard.querySelectorAll('.user-meta span');
        metaSpans.forEach((span) => {
            const text = span.textContent;
            if (text.includes('Бесплатный') || text.includes('Базовый') || text.includes('Премиум') || text.includes('Профессиональный') || text.includes('Корпоративный')) {
                span.textContent = formatTier(updates.tier);
            }
        });
    }
    
    // Обновляем role
    if (updates.role !== undefined) {
        const metaSpans = userCard.querySelectorAll('.user-meta span');
        metaSpans.forEach((span) => {
            const text = span.textContent;
            if (text.includes('Пользователь') || text.includes('Администратор')) {
                span.textContent = formatRole(updates.role);
            }
        });
    }
}

/**
 * Context7: Обновление карточки пользователя с данными из API
 */
function updateUserCard(userId, userData) {
    const userCard = document.querySelector(`[data-user-id="${userId}"]`);
    if (!userCard) return;
    
    // Обновляем все данные пользователя
    const nameDiv = userCard.querySelector('.user-name');
    if (nameDiv) {
        const name = `${escapeHtml(userData.first_name || '')} ${escapeHtml(userData.last_name || '')}`.trim();
        const username = userData.username ? `(@${escapeHtml(userData.username)})` : '';
        nameDiv.innerHTML = name + (username ? ` ${username}` : '');
    }
    
    const metaDiv = userCard.querySelector('.user-meta');
    if (metaDiv) {
        metaDiv.innerHTML = `
            <span>${formatTier(userData.tier)}</span>
            <span>${formatRole(userData.role)}</span>
            <span>ID: ${userData.telegram_id}</span>
        `;
    }
    
    // Обновляем кнопки с актуальными значениями
    const tierBtn = userCard.querySelector('button[onclick*="editUserTier"]');
    if (tierBtn) {
        tierBtn.setAttribute('onclick', `editUserTier('${userId}', '${userData.tier}')`);
    }
    
    const roleBtn = userCard.querySelector('button[onclick*="editUserRole"]');
    if (roleBtn) {
        roleBtn.setAttribute('onclick', `editUserRole('${userId}', '${userData.role}')`);
    }
}

