// [C7-ID: miniapp-admin-004] Управление feedback

let feedbackPage = 0;
let feedbackLimit = 20;
let feedbackFilter = {
    status: null,
    user_id: null
};

const FEEDBACK_API_BASE = '/api/feedback';

/**
 * Загрузка раздела feedback
 */
async function loadFeedbackSection() {
    const content = document.getElementById('admin-content');
    if (!content) return;
    
    content.innerHTML = `
        <div class="admin-feedback-section">
            <div class="admin-filters">
                <select id="feedback-status-filter" class="filter-select" onchange="filterFeedbackByStatus()">
                    <option value="">Все статусы</option>
                    <option value="pending">⏳ Ожидает</option>
                    <option value="in_progress">🔄 В работе</option>
                    <option value="resolved">✅ Решено</option>
                    <option value="closed">❌ Закрыто</option>
                </select>
                <button class="btn btn-sm btn-primary" onclick="refreshFeedback()">
                    🔄 Обновить
                </button>
            </div>
            <div id="feedback-list" class="feedback-list">
                <div class="loading">Загрузка feedback...</div>
            </div>
            <div id="feedback-pagination" class="pagination"></div>
        </div>
    `;
    
    await loadFeedback();
}

/**
 * Загрузка списка feedback
 */
async function loadFeedback(forceRefresh = false) {
    const list = document.getElementById('feedback-list');
    if (!list) return;
    
    if (!forceRefresh) {
        list.innerHTML = '<div class="loading">Загрузка...</div>';
    }
    
    try {
        const params = new URLSearchParams({
            limit: feedbackLimit,
            offset: feedbackPage * feedbackLimit
        });
        
        if (feedbackFilter.status) {
            params.append('status', feedbackFilter.status);
        }
        if (feedbackFilter.user_id) {
            params.append('user_id', feedbackFilter.user_id);
        }
        
        if (forceRefresh) {
            params.append('_t', Date.now().toString());
        }
        
        const data = await feedbackApiCall(`/?${params.toString()}`);
        
        if (data.items.length === 0) {
            list.innerHTML = '<div class="empty-state">Feedback не найден</div>';
            return;
        }
        
        renderFeedbackList(data.items);
        renderFeedbackPagination(data.total, data.limit, data.offset);
        
    } catch (error) {
        list.innerHTML = `<div class="error">Ошибка загрузки: ${error.message}</div>`;
        showToast(`Ошибка загрузки feedback: ${error.message}`, 'error');
    }
}

/**
 * Выполнение API запроса к feedback endpoints
 */
async function feedbackApiCall(endpoint, options = {}) {
    const accessToken = getAccessToken();
    
    if (!accessToken) {
        throw new Error('Необходима аутентификация');
    }
    
    const defaultOptions = {
        headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${accessToken}`
        }
    };
    
    const mergedOptions = {
        ...defaultOptions,
        ...options,
        headers: {
            ...defaultOptions.headers,
            ...(options.headers || {})
        }
    };
    
    try {
        const response = await fetch(`${FEEDBACK_API_BASE}${endpoint}`, mergedOptions);
        
        if (!response.ok) {
            const errorData = await response.json().catch(() => ({ detail: `HTTP ${response.status}` }));
            throw new Error(errorData.detail || `HTTP ${response.status}`);
        }
        
        return await response.json();
    } catch (error) {
        console.error('Feedback API error:', error);
        throw error;
    }
}

/**
 * Отображение списка feedback
 */
function renderFeedbackList(feedbackItems) {
    const list = document.getElementById('feedback-list');
    if (!list) return;
    
    list.innerHTML = feedbackItems.map(feedback => `
        <div class="feedback-card" data-feedback-id="${feedback.id}">
            <div class="feedback-info">
                <div class="feedback-header">
                    <div class="feedback-status-badge">
                        ${formatFeedbackStatus(feedback.status)}
                    </div>
                    <div class="feedback-meta">
                        <span>${feedback.user_username ? `@${escapeHtml(feedback.user_username)}` : escapeHtml(feedback.user_first_name || 'Пользователь')}</span>
                        <span>${formatRelativeTime(feedback.created_at)}</span>
                    </div>
                </div>
                <div class="feedback-message">
                    ${escapeHtml(feedback.message.substring(0, 200))}${feedback.message.length > 200 ? '...' : ''}
                </div>
                ${feedback.admin_notes ? `
                    <div class="feedback-admin-notes">
                        <strong>Заметки админа:</strong> ${escapeHtml(feedback.admin_notes)}
                    </div>
                ` : ''}
            </div>
            <div class="feedback-actions">
                <button class="btn btn-sm btn-primary" onclick="viewFeedbackDetail('${feedback.id}')">
                    👁️ Детали
                </button>
                <button class="btn btn-sm btn-secondary" onclick="editFeedbackStatus('${feedback.id}', '${feedback.status}')">
                    ✏️ Статус
                </button>
            </div>
        </div>
    `).join('');
}

/**
 * Форматирование статуса feedback
 */
function formatFeedbackStatus(status) {
    const statusMap = {
        'pending': '<span class="badge" style="background: rgba(251, 191, 36, 0.2); color: #f59e0b;">⏳ Ожидает</span>',
        'in_progress': '<span class="badge" style="background: rgba(59, 130, 246, 0.2); color: #3b82f6;">🔄 В работе</span>',
        'resolved': '<span class="badge" style="background: rgba(34, 197, 94, 0.2); color: #22c55e;">✅ Решено</span>',
        'closed': '<span class="badge" style="background: rgba(107, 114, 128, 0.2); color: #6b7280;">❌ Закрыто</span>'
    };
    return statusMap[status] || status;
}

/**
 * Отображение пагинации
 */
function renderFeedbackPagination(total, limit, offset) {
    const pagination = document.getElementById('feedback-pagination');
    if (!pagination) return;
    
    const totalPages = Math.ceil(total / limit);
    const currentPage = Math.floor(offset / limit) + 1;
    
    if (totalPages <= 1) {
        pagination.innerHTML = '';
        return;
    }
    
    let html = '<div class="pagination-controls">';
    
    if (currentPage > 1) {
        html += `<button class="btn btn-sm" onclick="goToFeedbackPage(${currentPage - 2})">← Назад</button>`;
    }
    
    html += `<span>Страница ${currentPage} из ${totalPages} (всего: ${total})</span>`;
    
    if (currentPage < totalPages) {
        html += `<button class="btn btn-sm" onclick="goToFeedbackPage(${currentPage})">Вперёд →</button>`;
    }
    
    html += '</div>';
    pagination.innerHTML = html;
}

/**
 * Переход на страницу
 */
function goToFeedbackPage(page) {
    feedbackPage = page;
    loadFeedback();
}

/**
 * Фильтрация по статусу
 */
function filterFeedbackByStatus() {
    const select = document.getElementById('feedback-status-filter');
    feedbackFilter.status = select.value || null;
    feedbackPage = 0;
    loadFeedback();
}

/**
 * Обновление списка feedback
 */
function refreshFeedback() {
    feedbackPage = 0;
    loadFeedback(true);
}

/**
 * Просмотр деталей feedback
 */
async function viewFeedbackDetail(feedbackId) {
    try {
        const feedback = await feedbackApiCall(`/${feedbackId}`);
        
        const content = `
            <div class="feedback-detail">
                <div class="detail-item">
                    <strong>ID:</strong> ${feedback.id}
                </div>
                <div class="detail-item">
                    <strong>Пользователь:</strong> ${feedback.user_username ? `@${escapeHtml(feedback.user_username)}` : escapeHtml(feedback.user_first_name || 'Пользователь')}
                </div>
                <div class="detail-item">
                    <strong>Статус:</strong> ${formatFeedbackStatus(feedback.status)}
                </div>
                <div class="detail-item">
                    <strong>Создан:</strong> ${formatDate(feedback.created_at)}
                </div>
                <div class="detail-item">
                    <strong>Обновлён:</strong> ${formatDate(feedback.updated_at)}
                </div>
                ${feedback.resolved_by ? `
                    <div class="detail-item">
                        <strong>Решено пользователем:</strong> ${feedback.resolved_by}
                    </div>
                ` : ''}
                <div class="detail-item" style="flex-direction: column; align-items: flex-start;">
                    <strong>Сообщение:</strong>
                    <div style="margin-top: 8px; padding: 12px; background: var(--admin-surface-weak-bg); border-radius: 8px; white-space: pre-wrap;">
                        ${escapeHtml(feedback.message)}
                    </div>
                </div>
                ${feedback.admin_notes ? `
                    <div class="detail-item" style="flex-direction: column; align-items: flex-start;">
                        <strong>Заметки админа:</strong>
                        <div style="margin-top: 8px; padding: 12px; background: var(--admin-surface-weak-bg); border-radius: 8px; white-space: pre-wrap;">
                            ${escapeHtml(feedback.admin_notes)}
                        </div>
                    </div>
                ` : ''}
                <div class="detail-actions" style="margin-top: 16px;">
                    <button class="btn btn-primary" onclick="editFeedbackStatus('${feedback.id}', '${feedback.status}')">
                        ✏️ Изменить статус
                    </button>
                    <button class="btn btn-secondary" onclick="editFeedbackNotes('${feedback.id}', '${feedback.admin_notes || ''}')">
                        📝 Заметки
                    </button>
                    <button class="btn btn-secondary" onclick="loadFeedbackSection()">
                        ← Назад к списку
                    </button>
                </div>
            </div>
        `;
        
        const list = document.getElementById('feedback-list');
        if (list) {
            list.innerHTML = content;
        }
        
    } catch (error) {
        showToast(`Ошибка загрузки деталей: ${error.message}`, 'error');
    }
}

/**
 * Редактирование статуса feedback
 */
async function editFeedbackStatus(feedbackId, currentStatus) {
    const statusOptions = [
        { value: 'pending', label: '⏳ Ожидает' },
        { value: 'in_progress', label: '🔄 В работе' },
        { value: 'resolved', label: '✅ Решено' },
        { value: 'closed', label: '❌ Закрыто' }
    ];
    
    const optionsHtml = statusOptions.map(opt => 
        `<option value="${opt.value}" ${opt.value === currentStatus ? 'selected' : ''}>${opt.label}</option>`
    ).join('');
    
    const newStatus = prompt(`Выберите новый статус:\n\n${statusOptions.map(opt => `${opt.value === currentStatus ? '→ ' : '  '}${opt.label}`).join('\n')}\n\nВведите: pending, in_progress, resolved или closed`, currentStatus);
    
    if (!newStatus || newStatus === currentStatus) {
        return;
    }
    
    if (!['pending', 'in_progress', 'resolved', 'closed'].includes(newStatus)) {
        showToast('Неверный статус', 'error');
        return;
    }
    
    try {
        await feedbackApiCall(`/${feedbackId}`, {
            method: 'PATCH',
            body: JSON.stringify({ status: newStatus })
        });
        
        showToast('Статус обновлён', 'success');
        loadFeedback(true);
        
        // Если открыты детали, обновляем их
        const list = document.getElementById('feedback-list');
        if (list && list.querySelector('.feedback-detail')) {
            viewFeedbackDetail(feedbackId);
        }
        
    } catch (error) {
        showToast(`Ошибка обновления статуса: ${error.message}`, 'error');
    }
}

/**
 * Редактирование заметок админа
 */
async function editFeedbackNotes(feedbackId, currentNotes) {
    const newNotes = prompt('Введите заметки админа:', currentNotes || '');
    
    if (newNotes === null) {
        return; // Пользователь отменил
    }
    
    try {
        await feedbackApiCall(`/${feedbackId}`, {
            method: 'PATCH',
            body: JSON.stringify({ admin_notes: newNotes || null })
        });
        
        showToast('Заметки обновлены', 'success');
        loadFeedback(true);
        
        // Если открыты детали, обновляем их
        const list = document.getElementById('feedback-list');
        if (list && list.querySelector('.feedback-detail')) {
            viewFeedbackDetail(feedbackId);
        }
        
    } catch (error) {
        showToast(`Ошибка обновления заметок: ${error.message}`, 'error');
    }
}

// Делаем функции глобальными
window.loadFeedbackSection = loadFeedbackSection;
window.filterFeedbackByStatus = filterFeedbackByStatus;
window.refreshFeedback = refreshFeedback;
window.viewFeedbackDetail = viewFeedbackDetail;
window.editFeedbackStatus = editFeedbackStatus;
window.editFeedbackNotes = editFeedbackNotes;
window.goToFeedbackPage = goToFeedbackPage;

