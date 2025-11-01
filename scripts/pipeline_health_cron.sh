#!/usr/bin/env bash
# Context7: production-ready cron wrapper с flock и timeout

set -Eeuo pipefail
umask 027

INTERVAL=${1:-30}
REPORT_DIR="/opt/telegram-assistant/reports/health"
LOCK_FILE="/var/lock/pipeline_health.lock"
LOG_FILE="/var/log/pipeline_health.log"

mkdir -p "$REPORT_DIR"

{
    # Exclusive lock (non-blocking)
    flock -n 9 || { 
        echo "$(date -u +"%Y-%m-%d %H:%M:%S UTC") - Already running" >&2
        exit 0
    }
    
    TIMESTAMP=$(date -u +"%Y%m%d_%H%M%S")
    JSON="$REPORT_DIR/health_${TIMESTAMP}.json"
    MD="$REPORT_DIR/health_${TIMESTAMP}.md"
    
    # Load environment
    if [ -f /opt/telegram-assistant/.env ]; then
        set -a
        source /opt/telegram-assistant/.env
        set +a
    fi
    
    # Run with timeout
    EXIT_CODE=0
    timeout 5m python3 /opt/telegram-assistant/scripts/check_pipeline_health.py \
        --mode deep \
        --window 3600 \
        --output-json "$JSON" \
        --output-md "$MD" \
        --prometheus-pushgateway "${PROMETHEUS_PUSHGATEWAY_URL:-}" \
        --thresholds-file "/opt/telegram-assistant/config/slo_thresholds.json" \
        2>&1 | tee -a "$LOG_FILE" || EXIT_CODE=$?
    
    # Create symlinks to latest
    ln -sf "$JSON" "$REPORT_DIR/latest.json"
    ln -sf "$MD" "$REPORT_DIR/latest.md"
    
    # Cleanup old reports (>7 days)
    find "$REPORT_DIR" -name "health_*.json" -mtime +7 -delete
    find "$REPORT_DIR" -name "health_*.md" -mtime +7 -delete
    
    # Rotate logs (>30 days)
    find /var/log -name "pipeline_health.log*" -mtime +30 -delete
    
    if [ $EXIT_CODE -ne 0 ]; then
        echo "$(date -u +"%Y-%m-%d %H:%M:%S UTC") - Health check failed with exit code $EXIT_CODE" >&2
    fi
    
    exit $EXIT_CODE
    
} 9>"$LOCK_FILE"

