#!/bin/bash

# ============================================================================
# TELEGRAM ASSISTANT - STARTUP SCRIPT
# ============================================================================
# Context7 Best Practices: Comprehensive startup with health checks and validation

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# ============================================================================
# ENVIRONMENT VALIDATION
# ============================================================================

validate_environment() {
    log_info "Validating environment configuration..."
    
    # Check if .env file exists
    if [[ ! -f "$PROJECT_DIR/.env" ]]; then
        log_warning ".env file not found. Creating from example..."
        if [[ -f "$PROJECT_DIR/env.example" ]]; then
            cp "$PROJECT_DIR/env.example" "$PROJECT_DIR/.env"
            log_warning "Please edit .env file with your configuration before running again."
            exit 1
        else
            log_error "env.example file not found. Cannot create .env file."
            exit 1
        fi
    fi
    
    # Load environment variables
    set -a
    source "$PROJECT_DIR/.env"
    set +a
    
    # Validate required variables
    local required_vars=(
        "GIGACHAT_API_KEY"
        "TELEGRAM_BOT_TOKEN"
        "TELEGRAM_API_ID"
        "TELEGRAM_API_HASH"
        "JWT_SECRET_KEY"
    )
    
    for var in "${required_vars[@]}"; do
        if [[ -z "${!var:-}" ]]; then
            log_error "Required environment variable $var is not set."
            exit 1
        fi
    done
    
    log_success "Environment validation completed."
}

# ============================================================================
# DOCKER VALIDATION
# ============================================================================

check_docker() {
    log_info "Checking Docker installation..."
    
    if ! command -v docker &> /dev/null; then
        log_error "Docker is not installed. Please install Docker first."
        exit 1
    fi
    
    if ! command -v docker-compose &> /dev/null; then
        log_error "Docker Compose is not installed. Please install Docker Compose first."
        exit 1
    fi
    
    # Check if Docker daemon is running
    if ! docker info &> /dev/null; then
        log_error "Docker daemon is not running. Please start Docker first."
        exit 1
    fi
    
    log_success "Docker validation completed."
}

# ============================================================================
# INFRASTRUCTURE STARTUP
# ============================================================================

start_infrastructure() {
    log_info "Starting infrastructure services..."
    
    cd "$PROJECT_DIR"
    
    # Start infrastructure services first
    docker-compose up -d postgres redis qdrant neo4j
    
    log_info "Waiting for infrastructure services to be ready..."
    
    # Wait for PostgreSQL
    log_info "Waiting for PostgreSQL..."
    timeout 60 bash -c 'until docker-compose exec postgres pg_isready -U telegram_user; do sleep 2; done'
    
    # Wait for Redis
    log_info "Waiting for Redis..."
    timeout 30 bash -c 'until docker-compose exec redis redis-cli ping; do sleep 2; done'
    
    # Wait for Qdrant
    log_info "Waiting for Qdrant..."
    timeout 30 bash -c 'until curl -f http://localhost:6333/health; do sleep 2; done'
    
    # Wait for Neo4j
    log_info "Waiting for Neo4j..."
    timeout 60 bash -c 'until docker-compose exec neo4j cypher-shell -u neo4j -p neo4j_password "RETURN 1"; do sleep 5; done'
    
    log_success "Infrastructure services are ready."
}

# ============================================================================
# DATABASE MIGRATION
# ============================================================================

run_migrations() {
    log_info "Running database migrations..."
    
    # Wait a bit more for PostgreSQL to be fully ready
    sleep 5
    
    # Run migrations using Alembic
    if [[ -f "$PROJECT_DIR/api/alembic.ini" ]]; then
        cd "$PROJECT_DIR/api"
        docker-compose exec -T postgres psql -U telegram_user -d telegram_assistant -c "SELECT 1;" || {
            log_error "Database connection failed."
            exit 1
        }
        
        # Run migrations
        docker-compose run --rm api alembic upgrade head || {
            log_error "Database migration failed."
            exit 1
        }
        
        log_success "Database migrations completed."
    else
        log_warning "Alembic configuration not found. Skipping migrations."
    fi
}

# ============================================================================
# APPLICATION STARTUP
# ============================================================================

start_application() {
    log_info "Starting application services..."
    
    cd "$PROJECT_DIR"
    
    # Start API service
    docker-compose up -d api
    
    # Wait for API to be ready
    log_info "Waiting for API service..."
    timeout 60 bash -c 'until curl -f http://localhost:8000/health; do sleep 2; done'
    
    # Start worker services
    docker-compose up -d worker telethon-ingest
    
    # Start monitoring services
    docker-compose up -d prometheus grafana nginx
    
    log_success "Application services started."
}

# ============================================================================
# HEALTH CHECKS
# ============================================================================

run_health_checks() {
    log_info "Running health checks..."
    
    local services=(
        "http://localhost:8000/health:API"
        "http://localhost:9090/-/healthy:Prometheus"
        "http://localhost:3000/api/health:Grafana"
    )
    
    for service in "${services[@]}"; do
        local url="${service%%:*}"
        local name="${service##*:}"
        
        if curl -f -s "$url" > /dev/null; then
            log_success "$name is healthy"
        else
            log_warning "$name health check failed"
        fi
    done
}

# ============================================================================
# MONITORING SETUP
# ============================================================================

setup_monitoring() {
    log_info "Setting up monitoring..."
    
    # Wait for Grafana to be ready
    log_info "Waiting for Grafana..."
    timeout 60 bash -c 'until curl -f http://localhost:3000/api/health; do sleep 5; done'
    
    # Import Grafana dashboard
    if [[ -f "$PROJECT_DIR/grafana/dashboards/channel_processing.json" ]]; then
        log_info "Grafana dashboard will be available at http://localhost:3000"
        log_info "Default credentials: admin / ${GRAFANA_PASSWORD:-admin}"
    fi
    
    log_success "Monitoring setup completed."
}

# ============================================================================
# STATUS DISPLAY
# ============================================================================

show_status() {
    log_info "System Status:"
    echo ""
    echo "🌐 Web Services:"
    echo "  • API: http://localhost:8000"
    echo "  • Mini App: http://localhost:8000/webapp/channels.html"
    echo "  • Grafana: http://localhost:3000 (admin / ${GRAFANA_PASSWORD:-admin})"
    echo "  • Prometheus: http://localhost:9090"
    echo ""
    echo "🗄️  Databases:"
    echo "  • PostgreSQL: localhost:5432"
    echo "  • Redis: localhost:6379"
    echo "  • Qdrant: http://localhost:6333"
    echo "  • Neo4j: http://localhost:7474 (neo4j / ${NEO4J_PASSWORD:-neo4j_password})"
    echo ""
    echo "📊 Monitoring:"
    echo "  • Grafana Dashboard: Channel Processing"
    echo "  • Prometheus Metrics: /metrics endpoints"
    echo ""
    echo "🤖 Bot Commands:"
    echo "  • /add_channel - Add a channel"
    echo "  • /my_channels - List channels"
    echo "  • /channel_stats - View statistics"
    echo ""
    echo "📱 Mini App:"
    echo "  • Open in Telegram WebApp"
    echo "  • Full channel management interface"
    echo ""
}

# ============================================================================
# CLEANUP ON EXIT
# ============================================================================

cleanup() {
    log_info "Cleaning up..."
    # Add any cleanup logic here if needed
}

trap cleanup EXIT

# ============================================================================
# MAIN EXECUTION
# ============================================================================

main() {
    log_info "Starting Telegram Assistant System..."
    echo ""
    
    # Validate environment
    validate_environment
    
    # Check Docker
    check_docker
    
    # Start infrastructure
    start_infrastructure
    
    # Run migrations
    run_migrations
    
    # Start application
    start_application
    
    # Setup monitoring
    setup_monitoring
    
    # Run health checks
    run_health_checks
    
    # Show status
    show_status
    
    log_success "Telegram Assistant System is ready!"
    echo ""
    log_info "To stop the system, run: docker-compose down"
    log_info "To view logs, run: docker-compose logs -f"
    echo ""
}

# Run main function
main "$@"
