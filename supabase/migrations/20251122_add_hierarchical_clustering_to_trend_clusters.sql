-- Migration: Add hierarchical clustering fields to trend_clusters table
-- Purpose: Support two-level clustering (main topics + subtopics)
-- Context7: Allows clusters to have parent clusters and sub-clusters

-- Add parent_cluster_id for hierarchical structure
ALTER TABLE trend_clusters 
ADD COLUMN IF NOT EXISTS parent_cluster_id UUID REFERENCES trend_clusters(id) ON DELETE SET NULL;

-- Add cluster_level for hierarchy level (1 = main topic, 2 = subtopic)
ALTER TABLE trend_clusters 
ADD COLUMN IF NOT EXISTS cluster_level INTEGER NOT NULL DEFAULT 1;

-- Index for finding sub-clusters of a parent
CREATE INDEX IF NOT EXISTS idx_trend_clusters_parent 
ON trend_clusters(parent_cluster_id) 
WHERE parent_cluster_id IS NOT NULL;

-- Index for filtering by cluster level
CREATE INDEX IF NOT EXISTS idx_trend_clusters_level 
ON trend_clusters(cluster_level) 
WHERE cluster_level > 1;

-- Composite index for finding sub-clusters efficiently
CREATE INDEX IF NOT EXISTS idx_trend_clusters_parent_level 
ON trend_clusters(parent_cluster_id, cluster_level) 
WHERE parent_cluster_id IS NOT NULL;

-- Comment on columns for documentation
COMMENT ON COLUMN trend_clusters.parent_cluster_id IS 'Parent cluster ID for hierarchical clustering (NULL for level 1 clusters)';
COMMENT ON COLUMN trend_clusters.cluster_level IS 'Cluster hierarchy level: 1 = main topic, 2 = subtopic';

