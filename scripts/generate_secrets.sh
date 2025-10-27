#!/bin/bash
# Generate secure secrets for Telegram Assistant
# Usage: ./scripts/generate_secrets.sh

set -e

# Функция для безопасного добавления переменных в .env
append_if_absent() {
    local key="$1"
    local value="$2"
    
    if ! grep -q "^$key=" .env 2>/dev/null; then
        echo "$key=$value" >> .env
        echo "✅ Added $key to .env"
    else
        echo "⚠️  $key already exists in .env"
    fi
}

# Проверяем наличие .env файла
if [ ! -f .env ]; then
    echo "📋 Creating .env from env.example..."
    cp env.example .env
    echo "✅ Created .env file"
fi

echo "🔐 Generating secure secrets..."

# Neo4j пароль
NEO4J_PASSWORD=$(openssl rand -base64 32 | tr -d "=+/" | cut -c1-32)
append_if_absent "NEO4J_PASSWORD" "$NEO4J_PASSWORD"
append_if_absent "NEO4J_AUTH" "neo4j/$NEO4J_PASSWORD"

# JWT Secret
JWT_SECRET=$(openssl rand -base64 64 | tr -d "=+/" | cut -c1-64)
append_if_absent "JWT_SECRET" "$JWT_SECRET"

# Grafana пароль
GRAFANA_PASSWORD=$(openssl rand -base64 16 | tr -d "=+/" | cut -c1-16)
append_if_absent "GRAFANA_PASSWORD" "$GRAFANA_PASSWORD"

# PostgreSQL пароль
POSTGRES_PASSWORD=$(openssl rand -base64 32 | tr -d "=+/" | cut -c1-32)
append_if_absent "POSTGRES_PASSWORD" "$POSTGRES_PASSWORD"

# Prometheus Basic Auth (если нужен внешний доступ к метрикам)
if command -v htpasswd >/dev/null 2>&1; then
    PROM_PASSWORD=$(openssl rand -base64 16 | tr -d "=+/" | cut -c1-16)
    PROM_HASH=$(htpasswd -nbB prometheus "$PROM_PASSWORD" | cut -d: -f2)
    append_if_absent "PROM_BASICAUTH_USER" "prometheus"
    append_if_absent "PROM_BASICAUTH_HASH" "$PROM_HASH"
    echo "🔑 Prometheus Basic Auth: prometheus / $PROM_PASSWORD"
else
    echo "⚠️  htpasswd not found, skipping Prometheus Basic Auth"
fi

# Обновляем DATABASE_URL с новым паролем
if grep -q "DATABASE_URL=" .env; then
    sed -i "s|DATABASE_URL=.*|DATABASE_URL=postgresql+asyncpg://telegram_user:$POSTGRES_PASSWORD@localhost:5432/telegram_assistant|" .env
    echo "✅ Updated DATABASE_URL with new password"
fi

echo ""
echo "🎉 Secrets generated successfully!"
echo ""
echo "📝 Next steps:"
echo "1. Review .env file and update any missing values"
echo "2. Set your Telegram Bot Token: TELEGRAM_BOT_TOKEN=your_token"
echo "3. Set your Telegram API credentials: TELEGRAM_API_ID and TELEGRAM_API_HASH"
echo "4. Configure GigaChat credentials if needed"
echo "5. Run: docker-compose up -d"
echo ""
echo "🔒 Security notes:"
echo "- Keep .env file secure and never commit it to version control"
echo "- Use strong passwords for production"
echo "- Consider using Docker secrets for production deployment"
