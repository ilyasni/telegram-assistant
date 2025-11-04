// [C7-ID: miniapp-admin-004] Управление инвайт-кодами

let invitesPage = 0;
let invitesLimit = 20;
let invitesFilter = {
    status: null,
    tenant_id: null
};

/**
 * Загрузка раздела инвайт-кодов
 */
async function loadInvitesSection() {
    const content = document.getElementById('admin-content');
    if (!content) return;
    
    content.innerHTML = `
        <div class="admin-invites-section">
            <div class="admin-header-actions">
                <button class="btn btn-primary" onclick="createNewInvite()">
                    ➕ Создать инвайт
                </button>
            </div>
            <div class="admin-filters">
                <select id="invites-status-filter" class="filter-select" onchange="filterInvitesByStatus()">
                    <option value="">Все статусы</option>
                    <option value="active">Активные</option>
                    <option value="revoked">Отозванные</option>
                    <option value="expired">Истёкшие</option>
                </select>
            </div>
            <div id="invites-list" class="invites-list">
                <div class="loading">Загрузка инвайт-кодов...</div>
            </div>
            <div id="invites-pagination" class="pagination"></div>
        </div>
    `;
    
    await loadInvites();
}

/**
 * Загрузка списка инвайт-кодов
 */
async function loadInvites() {
    const list = document.getElementById('invites-list');
    if (!list) return;
    
    list.innerHTML = '<div class="loading">Загрузка...</div>';
    
    try {
        const params = new URLSearchParams({
            limit: invitesLimit,
            offset: invitesPage * invitesLimit
        });
        
        if (invitesFilter.status) {
            params.append('status', invitesFilter.status);
        }
        if (invitesFilter.tenant_id) {
            params.append('tenant_id', invitesFilter.tenant_id);
        }
        
        const data = await adminApiCall(`/invites?${params.toString()}`);
        
        if (data.invites.length === 0) {
            list.innerHTML = '<div class="empty-state">Инвайт-коды не найдены</div>';
            return;
        }
        
        renderInvitesList(data.invites);
        renderInvitesPagination(data.total, data.limit, data.offset);
        
    } catch (error) {
        list.innerHTML = `<div class="error">Ошибка загрузки: ${error.message}</div>`;
        showToast(`Ошибка загрузки инвайт-кодов: ${error.message}`, 'error');
    }
}

/**
 * Отображение списка инвайт-кодов
 */
function renderInvitesList(invites) {
    const list = document.getElementById('invites-list');
    if (!list) return;
    
    list.innerHTML = invites.map(invite => {
        const isExpired = invite.expires_at && new Date(invite.expires_at) < new Date();
        const isRevoked = !invite.active;
        const isUsedUp = invite.uses_limit > 0 && invite.uses_count >= invite.uses_limit;
        
        return `
            <div class="invite-card" data-invite-code="${invite.code}">
                <div class="invite-header">
                    <div class="invite-code">
                        <strong>${escapeHtml(invite.code)}</strong>
                    </div>
                    <div class="invite-status">
                        ${formatStatus(null, invite.active && !isExpired)}
                        ${isExpired ? '⏰ Истёк' : ''}
                        ${isUsedUp ? '✅ Использован' : ''}
                    </div>
                </div>
                <div class="invite-info">
                    <div class="invite-meta">
                        <span>${formatRole(invite.role)}</span>
                        <span>Использований: ${invite.uses_count}/${invite.uses_limit || '∞'}</span>
                    </div>
                    <div class="invite-dates">
                        <div>Создан: ${formatDate(invite.created_at)}</div>
                        ${invite.expires_at ? `<div>Истекает: ${formatDate(invite.expires_at)}</div>` : ''}
                        ${invite.last_used_at ? `<div>Последнее использование: ${formatDate(invite.last_used_at)}</div>` : ''}
                    </div>
                    ${invite.notes ? `<div class="invite-notes">${escapeHtml(invite.notes)}</div>` : ''}
                </div>
                <div class="invite-actions">
                    <button class="btn btn-sm btn-primary" onclick="viewInviteDetail('${invite.code}')">
                        👁️ Детали
                    </button>
                    <button class="btn btn-sm btn-secondary" onclick="editInvite('${invite.code}')">
                        ✏️ Редактировать
                    </button>
                    ${invite.active && !isExpired ? `
                        <button class="btn btn-sm btn-warning" onclick="revokeInvite('${invite.code}')">
                            🚫 Отозвать
                        </button>
                    ` : ''}
                    <button class="btn btn-sm btn-danger" onclick="deleteInvite('${invite.code}')">
                        🗑️ Удалить
                    </button>
                </div>
            </div>
        `;
    }).join('');
}

/**
 * Отображение пагинации
 */
function renderInvitesPagination(total, limit, offset) {
    const pagination = document.getElementById('invites-pagination');
    if (!pagination) return;
    
    const totalPages = Math.ceil(total / limit);
    const currentPage = Math.floor(offset / limit) + 1;
    
    if (totalPages <= 1) {
        pagination.innerHTML = '';
        return;
    }
    
    let html = '<div class="pagination-controls">';
    
    if (currentPage > 1) {
        html += `<button class="btn btn-sm" onclick="goToInvitesPage(${currentPage - 2})">← Назад</button>`;
    }
    
    html += `<span>Страница ${currentPage} из ${totalPages} (всего: ${total})</span>`;
    
    if (currentPage < totalPages) {
        html += `<button class="btn btn-sm" onclick="goToInvitesPage(${currentPage})">Вперёд →</button>`;
    }
    
    html += '</div>';
    pagination.innerHTML = html;
}

/**
 * Переход на страницу
 */
function goToInvitesPage(page) {
    invitesPage = page;
    loadInvites();
}

/**
 * Фильтрация по статусу
 */
function filterInvitesByStatus() {
    const select = document.getElementById('invites-status-filter');
    invitesFilter.status = select.value || null;
    invitesPage = 0;
    loadInvites();
}

/**
 * Создание нового инвайт-кода
 */
function createNewInvite() {
    const fields = [
        {
            name: 'tenant_id',
            label: 'Tenant ID',
            type: 'text',
            required: true,
            placeholder: 'UUID tenant'
        },
        {
            name: 'role',
            label: 'Роль',
            type: 'select',
            required: true,
            options: [
                { value: 'user', label: 'Пользователь', selected: true },
                { value: 'admin', label: 'Администратор' }
            ]
        },
        {
            name: 'uses_limit',
            label: 'Лимит использований (0 = безлимит)',
            type: 'number',
            required: true,
            value: '1'
        },
        {
            name: 'expires_at',
            label: 'Дата истечения (необязательно)',
            type: 'datetime-local'
        },
        {
            name: 'subscription_tier',
            label: 'Tier подписки (необязательно)',
            type: 'select',
            options: [
                { value: '', label: 'Не указан', selected: true },
                { value: 'free', label: 'Free' },
                { value: 'basic', label: 'Basic' },
                { value: 'premium', label: 'Premium' },
                { value: 'pro', label: 'Pro' },
                { value: 'enterprise', label: 'Enterprise' }
            ]
        },
        {
            name: 'notes',
            label: 'Заметки',
            type: 'textarea',
            rows: 3
        }
    ];
    
    createFormModal('Создание инвайт-кода', fields, async (data) => {
        try {
            const payload = {
                tenant_id: data.tenant_id,
                role: data.role,
                uses_limit: parseInt(data.uses_limit) || 1
            };
            
            if (data.expires_at) {
                payload.expires_at = new Date(data.expires_at).toISOString();
            }
            
            if (data.subscription_tier) {
                payload.subscription_tier = data.subscription_tier;
            }
            
            if (data.notes) {
                payload.notes = data.notes;
            }
            
            const result = await adminApiCall('/invites', {
                method: 'POST',
                body: JSON.stringify(payload)
            });
            
            showToast(`Инвайт-код создан: ${result.code}`, 'success');
            loadInvites();
        } catch (error) {
            showToast(`Ошибка создания инвайт-кода: ${error.message}`, 'error');
        }
    });
}

/**
 * Просмотр деталей инвайт-кода
 */
async function viewInviteDetail(code) {
    try {
        const invite = await adminApiCall(`/invites/${code}`);
        const usage = await adminApiCall(`/invites/${code}/usage`).catch(() => ({ usage: [], total: 0 }));
        
        const isExpired = invite.expires_at && new Date(invite.expires_at) < new Date();
        
        const content = `
            <div class="invite-detail">
                <div class="detail-item">
                    <strong>Код:</strong> <code>${escapeHtml(invite.code)}</code>
                </div>
                <div class="detail-item">
                    <strong>Роль:</strong> ${formatRole(invite.role)}
                </div>
                <div class="detail-item">
                    <strong>Статус:</strong> ${formatStatus(null, invite.active && !isExpired)}
                    ${isExpired ? '⏰ Истёк' : ''}
                </div>
                <div class="detail-item">
                    <strong>Использований:</strong> ${invite.uses_count}/${invite.uses_limit || '∞'}
                </div>
                <div class="detail-item">
                    <strong>Создан:</strong> ${formatDate(invite.created_at)}
                </div>
                ${invite.expires_at ? `
                    <div class="detail-item">
                        <strong>Истекает:</strong> ${formatDate(invite.expires_at)}
                    </div>
                ` : ''}
                ${invite.last_used_at ? `
                    <div class="detail-item">
                        <strong>Последнее использование:</strong> ${formatDate(invite.last_used_at)}
                    </div>
                ` : ''}
                ${invite.notes ? `
                    <div class="detail-item">
                        <strong>Заметки:</strong> ${escapeHtml(invite.notes)}
                    </div>
                ` : ''}
                <div class="detail-section">
                    <strong>История использования (${usage.total}):</strong>
                    ${usage.usage.length > 0 ? `
                        <ul>
                            ${usage.usage.map(item => `
                                <li>${formatDate(item.used_at)} - User ID: ${item.user_id || '-'}</li>
                            `).join('')}
                        </ul>
                    ` : '<p>Нет использований</p>'}
                </div>
            </div>
        `;
        
        createModal('Детали инвайт-кода', content);
        
    } catch (error) {
        showToast(`Ошибка загрузки деталей: ${error.message}`, 'error');
    }
}

/**
 * Редактирование инвайт-кода
 */
async function editInvite(code) {
    try {
        const invite = await adminApiCall(`/invites/${code}`);
        
        const fields = [
            {
                name: 'uses_limit',
                label: 'Лимит использований (0 = безлимит)',
                type: 'number',
                value: invite.uses_limit.toString()
            },
            {
                name: 'expires_at',
                label: 'Дата истечения',
                type: 'datetime-local',
                value: invite.expires_at ? new Date(invite.expires_at).toISOString().slice(0, 16) : ''
            },
            {
                name: 'notes',
                label: 'Заметки',
                type: 'textarea',
                rows: 3,
                value: invite.notes || ''
            }
        ];
        
        createFormModal('Редактирование инвайт-кода', fields, async (data) => {
            try {
                const payload = {};
                
                if (data.uses_limit) {
                    payload.uses_limit = parseInt(data.uses_limit);
                }
                
                if (data.expires_at) {
                    payload.expires_at = new Date(data.expires_at).toISOString();
                }
                
                if (data.notes !== undefined) {
                    payload.notes = data.notes;
                }
                
                await adminApiCall(`/invites/${code}`, {
                    method: 'PUT',
                    body: JSON.stringify(payload)
                });
                
                showToast('Инвайт-код успешно обновлён', 'success');
                loadInvites();
            } catch (error) {
                showToast(`Ошибка обновления: ${error.message}`, 'error');
            }
        });
        
    } catch (error) {
        showToast(`Ошибка загрузки инвайт-кода: ${error.message}`, 'error');
    }
}

/**
 * Отзыв инвайт-кода
 */
async function revokeInvite(code) {
    const confirmed = await showConfirm('Вы уверены, что хотите отозвать этот инвайт-код?');
    
    if (!confirmed) return;
    
    try {
        await adminApiCall(`/invites/${code}/revoke`, {
            method: 'POST'
        });
        
        showToast('Инвайт-код отозван', 'success');
        loadInvites();
    } catch (error) {
        showToast(`Ошибка отзыва: ${error.message}`, 'error');
    }
}

/**
 * Удаление инвайт-кода
 */
async function deleteInvite(code) {
    const confirmed = await showConfirm('Вы уверены, что хотите удалить этот инвайт-код? Это действие необратимо!');
    
    if (!confirmed) return;
    
    try {
        await adminApiCall(`/invites/${code}`, {
            method: 'DELETE'
        });
        
        showToast('Инвайт-код удалён', 'success');
        loadInvites();
    } catch (error) {
        showToast(`Ошибка удаления: ${error.message}`, 'error');
    }
}

