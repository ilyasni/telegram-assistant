-- Rollback script for posts schema migration
-- Reverts telegram_message_id back to tg_message_id and removes telegram_post_url

-- Drop views first (they depend on columns)
DROP VIEW IF EXISTS posts_with_telegram_links;
DROP VIEW IF EXISTS posts_legacy;

-- Drop trigger
DROP TRIGGER IF EXISTS trg_posts_telegram_url ON posts;

-- Drop functions
DROP FUNCTION IF EXISTS update_telegram_post_url();
DROP FUNCTION IF EXISTS generate_telegram_post_url(TEXT, BIGINT);

-- Drop telegram_post_url column
ALTER TABLE posts DROP COLUMN IF EXISTS telegram_post_url;

-- Rename telegram_message_id back to tg_message_id
ALTER TABLE posts RENAME COLUMN telegram_message_id TO tg_message_id;

-- Verify rollback
DO $$ 
BEGIN
    -- Check that tg_message_id column exists
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name='posts' AND column_name='tg_message_id'
    ) THEN 
        RAISE EXCEPTION 'tg_message_id column missing after rollback';
    END IF;
    
    -- Check that telegram_post_url column is gone
    IF EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name='posts' AND column_name='telegram_post_url'
    ) THEN 
        RAISE EXCEPTION 'telegram_post_url column still exists after rollback';
    END IF;
    
    RAISE NOTICE 'Rollback completed successfully';
END $$;
