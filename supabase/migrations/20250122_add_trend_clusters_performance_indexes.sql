-- Migration: Add performance indexes for trend_clusters
-- Purpose: Optimize queries in trends_stable_task and _find_cluster_by_label
-- Context7: Best practices for database performance optimization

-- Index for trends_stable_task: filter by status and is_generic
-- This query: WHERE status IN ('emerging', 'stable') AND is_generic = false
CREATE INDEX IF NOT EXISTS idx_trend_clusters_status_generic 
ON trend_clusters(status, is_generic) 
WHERE is_generic = false;

-- Index for _find_cluster_by_label: find existing clusters by label/primary_topic
-- This query: WHERE status = 'emerging' AND (label = $1 OR primary_topic = $1) AND is_generic = false
CREATE INDEX IF NOT EXISTS idx_trend_clusters_label_status 
ON trend_clusters(label, status, is_generic) 
WHERE status = 'emerging' AND is_generic = false;

CREATE INDEX IF NOT EXISTS idx_trend_clusters_primary_topic_status 
ON trend_clusters(primary_topic, status, is_generic) 
WHERE status = 'emerging' AND is_generic = false;

-- Index for trend_metrics: optimize join with trend_clusters in trends_stable_task
-- This query: ORDER BY metrics_at DESC LIMIT 1
-- Already exists: idx_trend_metrics_metrics_at, but we can add composite index for better performance
CREATE INDEX IF NOT EXISTS idx_trend_metrics_cluster_metrics_at 
ON trend_metrics(cluster_id, metrics_at DESC);

-- Comments for documentation
COMMENT ON INDEX idx_trend_clusters_status_generic IS 'Optimizes trends_stable_task: filter by status and is_generic';
COMMENT ON INDEX idx_trend_clusters_label_status IS 'Optimizes _find_cluster_by_label: find existing clusters by label';
COMMENT ON INDEX idx_trend_clusters_primary_topic_status IS 'Optimizes _find_cluster_by_label: find existing clusters by primary_topic';
COMMENT ON INDEX idx_trend_metrics_cluster_metrics_at IS 'Optimizes trends_stable_task: get latest metrics for cluster';

