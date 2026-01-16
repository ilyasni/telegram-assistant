-- Migration: Add coherence metrics fields to trend_clusters table
-- Purpose: Store formal coherence metrics for cluster quality assessment
-- Context7: Metrics include Topic Coherence (NPMI), silhouette score, intra-cluster similarity

-- Add coherence metrics columns
ALTER TABLE trend_clusters 
ADD COLUMN IF NOT EXISTS coherence_score REAL;

ALTER TABLE trend_clusters 
ADD COLUMN IF NOT EXISTS silhouette_score REAL;

ALTER TABLE trend_clusters 
ADD COLUMN IF NOT EXISTS npmi_score REAL;

ALTER TABLE trend_clusters 
ADD COLUMN IF NOT EXISTS intra_cluster_similarity REAL;

-- Add timestamp for last refinement
ALTER TABLE trend_clusters 
ADD COLUMN IF NOT EXISTS last_refinement_at TIMESTAMPTZ;

-- Index for filtering clusters by coherence (for refinement service)
CREATE INDEX IF NOT EXISTS idx_trend_clusters_coherence 
ON trend_clusters(coherence_score) 
WHERE coherence_score IS NOT NULL;

-- Index for refinement service to find clusters that need refinement
CREATE INDEX IF NOT EXISTS idx_trend_clusters_refinement_at 
ON trend_clusters(last_refinement_at) 
WHERE last_refinement_at IS NOT NULL;

-- Comment on columns for documentation
COMMENT ON COLUMN trend_clusters.coherence_score IS 'Overall coherence score (0.0-1.0) based on intra-cluster similarity';
COMMENT ON COLUMN trend_clusters.silhouette_score IS 'Silhouette score (-1.0 to 1.0) measuring cluster separability';
COMMENT ON COLUMN trend_clusters.npmi_score IS 'Topic Coherence score (-1.0 to 1.0) using Normalized Pointwise Mutual Information';
COMMENT ON COLUMN trend_clusters.intra_cluster_similarity IS 'Average cosine similarity between posts within the cluster (0.0-1.0)';
COMMENT ON COLUMN trend_clusters.last_refinement_at IS 'Timestamp of last cluster refinement operation';

