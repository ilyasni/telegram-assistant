-- Migration: Add is_generic field to trend_clusters table
-- Purpose: Filter out generic/low-quality trends (e.g., "которые", "начали", "против")

ALTER TABLE trend_clusters 
ADD COLUMN IF NOT EXISTS is_generic BOOLEAN NOT NULL DEFAULT false;

-- Index for filtering non-generic trends (partial index for better performance)
CREATE INDEX IF NOT EXISTS idx_trend_clusters_is_generic 
ON trend_clusters(is_generic) 
WHERE is_generic = false;

-- Update existing clusters: mark as generic if primary_topic is generic
-- This is a one-time cleanup for existing data
UPDATE trend_clusters
SET is_generic = true
WHERE (
    primary_topic IS NULL 
    OR LENGTH(primary_topic) < 4
    OR (primary_topic NOT LIKE '% %' AND primary_topic NOT LIKE '#%')
    OR primary_topic IN ('которые', 'начали', 'против', 'можно', 'тащусь', 'рублей', 'сервис', 'крупнейший', 'мужчина', 'женщина', 'первый', 'просто', 'очень', 'сегодня')
);

