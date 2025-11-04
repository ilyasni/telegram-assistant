// [C7-ID: miniapp-admin-006] Общие утилиты для админ-панели

const ADMIN_API_BASE = '/api/admin';

/**
 * Выполнение API запроса с обработкой ошибок
 */
async function adminApiCall(endpoint, options = {}) {
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
        const response = await fetch(`${ADMIN_API_BASE}${endpoint}`, mergedOptions);
        
        if (!response.ok) {
            const errorData = await response.json().catch(() => ({ detail: `HTTP ${response.status}` }));
            throw new Error(errorData.detail || `HTTP ${response.status}`);
        }
        
        return await response.json();
    } catch (error) {
        console.error('Admin API error:', error);
        throw error;
    }
}

/**
 * Получение access token из глобальной переменной
 */
function getAccessToken() {
    // Пытаемся получить из глобальной переменной (устанавливается в index.html)
    if (typeof accessToken !== 'undefined' && accessToken) {
        return accessToken;
    }
    
    // Пытаемся получить из localStorage как fallback
    return localStorage.getItem('admin_access_token');
}

/**
 * Форматирование даты для отображения
 */
function formatDate(dateString) {
    if (!dateString) return '-';
    
    const date = new Date(dateString);
    return new Intl.DateTimeFormat('ru-RU', {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit'
    }).format(date);
}

/**
 * Форматирование относительного времени
 */
function formatRelativeTime(dateString) {
    if (!dateString) return '-';
    
    const date = new Date(dateString);
    const now = new Date();
    const diffMs = now - date;
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMs / 3600000);
    const diffDays = Math.floor(diffMs / 86400000);
    
    if (diffMins < 1) return 'только что';
    if (diffMins < 60) return `${diffMins} мин. назад`;
    if (diffHours < 24) return `${diffHours} ч. назад`;
    if (diffDays < 7) return `${diffDays} дн. назад`;
    
    return formatDate(dateString);
}

/**
 * Валидация email (если понадобится)
 */
function isValidEmail(email) {
    const re = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    return re.test(email);
}

/**
 * Экранирование HTML для предотвращения XSS
 */
function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

/**
 * Показ уведомления (toast)
 */
function showToast(message, type = 'info') {
    // Используем Telegram WebApp показ уведомлений если доступно
    if (window.Telegram && window.Telegram.WebApp) {
        window.Telegram.WebApp.showAlert(message);
        return;
    }
    
    // Fallback: создаем простой toast
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    toast.style.cssText = `
        position: fixed;
        bottom: 20px;
        left: 50%;
        transform: translateX(-50%);
        background: var(--tg-bg-color, #fff);
        color: var(--tg-text-color, #000);
        padding: 12px 24px;
        border-radius: 8px;
        box-shadow: 0 4px 12px rgba(0,0,0,0.15);
        z-index: 10000;
        max-width: 90%;
    `;
    
    document.body.appendChild(toast);
    
    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transition = 'opacity 0.3s';
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

/**
 * Показ подтверждения (confirmation)
 */
async function showConfirm(message) {
    // Используем Telegram WebApp подтверждение если доступно
    if (window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.showConfirm) {
        return await new Promise((resolve) => {
            window.Telegram.WebApp.showConfirm(message, (confirmed) => {
                resolve(confirmed);
            });
        });
    }
    
    // Fallback: используем стандартный confirm
    return confirm(message);
}

/**
 * Дебаунс функция
 */
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

/**
 * Форматирование статуса
 */
function formatStatus(status, active = true) {
    if (typeof status === 'boolean') {
        return status ? '✅ Активен' : '❌ Неактивен';
    }
    
    if (status === 'active' || (active === true && status !== 'revoked')) {
        return '✅ Активен';
    }
    
    if (status === 'revoked' || active === false) {
        return '❌ Отозван';
    }
    
    if (status === 'expired') {
        return '⏰ Истёк';
    }
    
    return status;
}

/**
 * Форматирование tier
 */
function formatTier(tier) {
    const tierMap = {
        'free': '🆓 Бесплатный',
        'basic': '📦 Базовый',
        'premium': '⭐ Премиум',
        'pro': '💎 Профессиональный',
        'enterprise': '🏢 Корпоративный'
    };
    
    return tierMap[tier] || tier;
}

/**
 * Форматирование роли
 */
function formatRole(role) {
    const roleMap = {
        'user': '👤 Пользователь',
        'admin': '👑 Администратор'
    };
    
    return roleMap[role] || role;
}

/**
 * Создание элемента списка с действиями
 */
function createListItem(item, actions = []) {
    const li = document.createElement('div');
    li.className = 'list-item';
    li.style.cssText = `
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 12px 16px;
        border-bottom: 1px solid var(--tg-section-bg-color, #f0f0f0);
    `;
    
    const content = document.createElement('div');
    content.className = 'list-item-content';
    content.innerHTML = item;
    
    if (actions.length > 0) {
        const actionsDiv = document.createElement('div');
        actionsDiv.className = 'list-item-actions';
        actions.forEach(action => {
            const btn = document.createElement('button');
            btn.className = `btn btn-${action.type || 'secondary'}`;
            btn.textContent = action.label;
            btn.onclick = action.onClick;
            actionsDiv.appendChild(btn);
        });
        li.appendChild(actionsDiv);
    }
    
    li.appendChild(content);
    
    return li;
}

/**
 * Создание модального окна
 */
function createModal(title, content, buttons = []) {
    const modal = document.createElement('div');
    modal.className = 'modal';
    modal.style.cssText = `
        position: fixed;
        top: 0;
        left: 0;
        right: 0;
        bottom: 0;
        background: rgba(0,0,0,0.5);
        display: flex;
        align-items: center;
        justify-content: center;
        z-index: 10000;
    `;
    
    const modalContent = document.createElement('div');
    modalContent.className = 'modal-content';
    modalContent.style.cssText = `
        background: var(--tg-bg-color, #fff);
        border-radius: 12px;
        padding: 24px;
        max-width: 90%;
        max-height: 90vh;
        overflow-y: auto;
        box-shadow: 0 4px 20px rgba(0,0,0,0.3);
    `;
    
    const modalTitle = document.createElement('h2');
    modalTitle.textContent = title;
    modalTitle.style.cssText = `
        margin: 0 0 16px 0;
        font-size: 20px;
        font-weight: 600;
    `;
    
    const modalBody = document.createElement('div');
    modalBody.className = 'modal-body';
    modalBody.innerHTML = content;
    
    const modalFooter = document.createElement('div');
    modalFooter.className = 'modal-footer';
    modalFooter.style.cssText = `
        display: flex;
        justify-content: flex-end;
        gap: 8px;
        margin-top: 16px;
    `;
    
    buttons.forEach(button => {
        const btn = document.createElement('button');
        btn.className = `btn btn-${button.type || 'secondary'}`;
        btn.textContent = button.label;
        btn.onclick = () => {
            if (button.onClick) {
                button.onClick();
            }
            modal.remove();
        };
        modalFooter.appendChild(btn);
    });
    
    // Кнопка закрытия по умолчанию
    if (buttons.length === 0) {
        const closeBtn = document.createElement('button');
        closeBtn.className = 'btn btn-secondary';
        closeBtn.textContent = 'Закрыть';
        closeBtn.onclick = () => modal.remove();
        modalFooter.appendChild(closeBtn);
    }
    
    modalContent.appendChild(modalTitle);
    modalContent.appendChild(modalBody);
    modalContent.appendChild(modalFooter);
    modal.appendChild(modalContent);
    
    // Закрытие по клику вне модального окна
    modal.onclick = (e) => {
        if (e.target === modal) {
            modal.remove();
        }
    };
    
    document.body.appendChild(modal);
    
    return modal;
}

/**
 * Создание формы в модальном окне
 */
function createFormModal(title, fields, onSubmit) {
    // Context7: Создаем HTML формы
    const formHTML = fields.map(field => {
        if (field.type === 'select') {
            return `
                <div class="form-group">
                    <label>${field.label}</label>
                    <select name="${field.name}" class="form-control" ${field.required ? 'required' : ''}>
                        ${field.options.map(opt => 
                            `<option value="${opt.value}" ${opt.selected ? 'selected' : ''}>${opt.label}</option>`
                        ).join('')}
                    </select>
                </div>
            `;
        }
        
        if (field.type === 'textarea') {
            return `
                <div class="form-group">
                    <label>${field.label}</label>
                    <textarea name="${field.name}" class="form-control" ${field.required ? 'required' : ''} ${field.rows ? `rows="${field.rows}"` : ''}>${field.value || ''}</textarea>
                </div>
            `;
        }
        
        return `
            <div class="form-group">
                <label>${field.label}</label>
                <input type="${field.type || 'text'}" 
                       name="${field.name}" 
                       class="form-control" 
                       value="${field.value || ''}" 
                       ${field.required ? 'required' : ''}
                       ${field.placeholder ? `placeholder="${field.placeholder}"` : ''}>
            </div>
        `;
    }).join('');
    
    // Context7: Создаем модальное окно с формой
    const modal = createModal(title, `<form>${formHTML}</form>`, [
        {
            label: 'Отмена',
            type: 'secondary',
            onClick: () => {
                modal.remove();
            }
        },
        {
            label: 'Сохранить',
            type: 'primary',
            onClick: () => {
                // Context7: Находим форму в модальном окне и триггерим submit
                const modalForm = modal.querySelector('form');
                if (modalForm) {
                    modalForm.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
                }
            }
        }
    ]);
    
    // Context7: Находим форму внутри модального окна и привязываем обработчик
    const modalForm = modal.querySelector('form');
    if (!modalForm) {
        console.error('[Form] Form not found in modal');
        return modal;
    }
    
    let formSubmitted = false;
    
    // Context7: Обработчик submit формы
    modalForm.onsubmit = async (e) => {
        e.preventDefault();
        if (formSubmitted) {
            console.warn('[Form] Form already submitted, ignoring');
            return;
        }
        
        // Context7: Получаем данные из формы
        const formData = new FormData(modalForm);
        const data = {};
        formData.forEach((value, key) => {
            data[key] = value;
        });
        
        // Context7: Дополнительная проверка для select - получаем значение напрямую из DOM
        const selectElements = modalForm.querySelectorAll('select');
        selectElements.forEach(select => {
            if (select.name) {
                const selectedValue = select.value;
                data[select.name] = selectedValue;
                console.log(`[Form] Select ${select.name} value: ${selectedValue} (selectedIndex: ${select.selectedIndex})`);
            }
        });
        
        console.log('[Form] Collected form data:', data);
        
        formSubmitted = true;
        
        try {
            // Context7: Вызываем onSubmit и ждём завершения
            await onSubmit(data);
            // Закрываем модальное окно только после успешного сохранения
            modal.remove();
        } catch (error) {
            formSubmitted = false;
            console.error('Form submission error:', error);
            // Ошибка обрабатывается в onSubmit, модальное окно не закрываем
        }
    };
    
    return modal;
}

