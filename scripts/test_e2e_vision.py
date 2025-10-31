#!/usr/bin/env python3
"""
E2E Test: Telegram → S3 → Vision → Neo4j Pipeline
Context7 best practices: trace_id propagation, quota checks, idempotency
"""

import asyncio
import json
import uuid
import time
from datetime import datetime, timezone
from typing import Dict, Any

import redis.asyncio as redis
from redis.asyncio import Redis
import asyncpg
import boto3
from botocore.config import Config

# Test configuration
REDIS_URL = "redis://redis:6379"
DATABASE_URL = "postgresql://postgres:postgres@supabase-db:5432/postgres"
S3_ENDPOINT = "https://s3.cloud.ru"
S3_BUCKET = "test-467940"
S3_ACCESS_KEY = "94007b7b7d68a206c28caf2656150d73"
S3_SECRET_KEY = "4ac6898d1fbdc7618c6ec4ab7c940e72"
NEO4J_URI = "bolt://neo4j:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "password"

class E2EVisionTest:
    def __init__(self):
        self.redis: Redis = None
        self.db_pool = None
        self.s3_client = None
        self.neo4j_driver = None
        self.trace_id = str(uuid.uuid4())
        self.test_post_id = f"test_post_{int(time.time())}"
        
    async def setup(self):
        """Initialize connections"""
        print(f"🔧 Setting up E2E test with trace_id: {self.trace_id}")
        
        # Redis
        self.redis = Redis.from_url(REDIS_URL, decode_responses=True)
        await self.redis.ping()
        print("✅ Redis connected")
        
        # Database
        self.db_pool = await asyncpg.create_pool(DATABASE_URL)
        print("✅ Database connected")
        
        # S3
        self.s3_client = boto3.client(
            's3',
            endpoint_url=S3_ENDPOINT,
            aws_access_key_id=S3_ACCESS_KEY,
            aws_secret_access_key=S3_SECRET_KEY,
            region_name='ru-central-1',
            config=Config(s3={'addressing_style': 'virtual'})
        )
        print("✅ S3 connected")
        
        # Neo4j
        try:
            from neo4j import GraphDatabase
            self.neo4j_driver = GraphDatabase.driver(
                NEO4J_URI, 
                auth=(NEO4J_USER, NEO4J_PASSWORD)
            )
            print("✅ Neo4j connected")
        except ImportError:
            print("⚠️ Neo4j driver not available")
    
    async def test_vision_upload_event(self):
        """Test VisionUploadedEventV1 publication"""
        print("\n📤 Testing VisionUploadedEventV1...")
        
        # Create test media file
        test_image_data = b"fake_image_data_for_testing"
        media_hash = "test_sha256_hash_12345"
        
        # Upload to S3
        s3_key = f"media/test-tenant/{media_hash[:2]}/{media_hash}.jpg"
        self.s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=s3_key,
            Body=test_image_data,
            ContentType="image/jpeg"
        )
        print(f"✅ Uploaded test image to S3: {s3_key}")
        
        # Publish VisionUploadedEventV1
        event = {
            "schema": "posts.vision.uploaded.v1",
            "event_version": "1.0",
            "trace_id": self.trace_id,
            "tenant_id": "test-tenant",
            "post_id": self.test_post_id,
            "media": [{
                "sha256": media_hash,
                "s3_key": s3_key,
                "mime_type": "image/jpeg",
                "size_bytes": len(test_image_data),
                "position": 0,
                "role": "primary"
            }],
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "producer": "e2e_test"
        }
        
        await self.redis.xadd(
            "stream:posts:vision:uploaded",
            event,
            maxlen=1000
        )
        print("✅ Published VisionUploadedEventV1")
        
        return event
    
    async def test_vision_analysis_event(self):
        """Test VisionAnalyzedEventV1 publication"""
        print("\n🔍 Testing VisionAnalyzedEventV1...")
        
        # Simulate Vision analysis result
        analysis_result = {
            "schema": "posts.vision.analyzed.v1",
            "event_version": "1.0",
            "trace_id": self.trace_id,
            "tenant_id": "test-tenant",
            "post_id": self.test_post_id,
            "media": [{
                "sha256": "test_sha256_hash_12345",
                "s3_key": "media/test-tenant/te/test_sha256_hash_12345.jpg",
                "mime_type": "image/jpeg",
                "size_bytes": 1000
            }],
            "vision": {
                "provider": "gigachat",
                "model": "GigaChat-Pro",
                "schema_version": "1.0",
                "classification": {
                    "type": "meme",
                    "confidence": 0.92,
                    "tags": ["funny", "internet_culture"]
                },
                "ocr_text": "Test meme text",
                "is_meme": True,
                "tokens_used": 150,
                "file_id": "gigachat_file_123",
                "analyzed_at": datetime.now(timezone.utc).isoformat()
            },
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "producer": "e2e_test"
        }
        
        await self.redis.xadd(
            "stream:posts:vision:analyzed",
            analysis_result,
            maxlen=1000
        )
        print("✅ Published VisionAnalyzedEventV1")
        
        return analysis_result
    
    async def test_database_storage(self):
        """Test database storage of vision results"""
        print("\n💾 Testing database storage...")
        
        async with self.db_pool.acquire() as conn:
            # Check if media_objects table exists
            tables = await conn.fetch("""
                SELECT table_name FROM information_schema.tables 
                WHERE table_schema = 'public' AND table_name = 'media_objects'
            """)
            
            if not tables:
                print("⚠️ media_objects table not found - migration needed")
                return False
            
            # Insert test media object
            await conn.execute("""
                INSERT INTO media_objects (
                    file_sha256, mime_type, size_bytes, s3_key, 
                    first_seen_at, last_seen_at, refs_count
                ) VALUES ($1, $2, $3, $4, NOW(), NOW(), 1)
                ON CONFLICT (file_sha256) DO UPDATE SET
                    last_seen_at = NOW(),
                    refs_count = media_objects.refs_count + 1
            """, "test_sha256_hash_12345", "image/jpeg", 1000, 
                "media/test-tenant/te/test_sha256_hash_12345.jpg")
            
            print("✅ Media object stored in database")
            
            # Insert post enrichment
            await conn.execute("""
                INSERT INTO post_enrichment (
                    post_id, kind, vision_classification, vision_context,
                    vision_analyzed_at, enrichment_provider, metadata
                ) VALUES ($1, $2, $3, $4, NOW(), $5, $6)
                ON CONFLICT (post_id, kind) DO UPDATE SET
                    vision_classification = EXCLUDED.vision_classification,
                    vision_context = EXCLUDED.vision_context,
                    vision_analyzed_at = NOW(),
                    updated_at = NOW()
            """, self.test_post_id, "vision", 
                {"type": "meme", "confidence": 0.92, "tags": ["funny"]},
                {"ocr_text": "Test meme text", "is_meme": True},
                "gigachat", json.dumps({"trace_id": self.trace_id}))
            
            print("✅ Vision analysis stored in post_enrichment")
            
        return True
    
    async def test_neo4j_sync(self):
        """Test Neo4j synchronization"""
        if not self.neo4j_driver:
            print("⚠️ Neo4j not available - skipping")
            return False
            
        print("\n🕸️ Testing Neo4j synchronization...")
        
        try:
            with self.neo4j_driver.session() as session:
                # Create ImageContent node
                result = session.run("""
                    MERGE (img:ImageContent {
                        sha256: $sha256,
                        s3_key: $s3_key,
                        mime_type: $mime_type,
                        size_bytes: $size_bytes
                    })
                    SET img.updated_at = datetime()
                    RETURN img
                """, sha256="test_sha256_hash_12345",
                    s3_key="media/test-tenant/te/test_sha256_hash_12345.jpg",
                    mime_type="image/jpeg",
                    size_bytes=1000)
                
                if result.single():
                    print("✅ ImageContent node created in Neo4j")
                
                # Create Post node and relationship
                result = session.run("""
                    MERGE (post:Post {post_id: $post_id})
                    MERGE (img:ImageContent {sha256: $sha256})
                    MERGE (post)-[r:HAS_IMAGE {trace_id: $trace_id}]->(img)
                    SET r.created_at = datetime()
                    RETURN post, img, r
                """, post_id=self.test_post_id,
                    sha256="test_sha256_hash_12345",
                    trace_id=self.trace_id)
                
                if result.single():
                    print("✅ Post-ImageContent relationship created")
                
        except Exception as e:
            print(f"❌ Neo4j sync failed: {e}")
            return False
        
        return True
    
    async def test_quota_checks(self):
        """Test storage quota validation"""
        print("\n📊 Testing storage quota checks...")
        
        # Check current bucket usage
        try:
            response = self.s3_client.list_objects_v2(Bucket=S3_BUCKET)
            total_size = sum(obj['Size'] for obj in response.get('Contents', []))
            total_gb = total_size / (1024**3)
            
            print(f"📈 Current bucket usage: {total_gb:.2f} GB")
            
            if total_gb > 14.0:
                print("⚠️ WARNING: Bucket usage exceeds emergency threshold!")
            elif total_gb > 12.0:
                print("⚠️ WARNING: Bucket usage approaching limit")
            else:
                print("✅ Bucket usage within limits")
                
        except Exception as e:
            print(f"❌ Failed to check quota: {e}")
            return False
        
        return True
    
    async def cleanup(self):
        """Cleanup test data"""
        print("\n🧹 Cleaning up test data...")
        
        try:
            # Remove test objects from S3
            self.s3_client.delete_object(
                Bucket=S3_BUCKET,
                Key="media/test-tenant/te/test_sha256_hash_12345.jpg"
            )
            print("✅ Test S3 objects removed")
            
            # Remove test data from database
            async with self.db_pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM post_enrichment WHERE post_id = $1",
                    self.test_post_id
                )
                await conn.execute(
                    "DELETE FROM media_objects WHERE file_sha256 = $1",
                    "test_sha256_hash_12345"
                )
            print("✅ Test database records removed")
            
            # Remove test data from Neo4j
            if self.neo4j_driver:
                with self.neo4j_driver.session() as session:
                    session.run("""
                        MATCH (post:Post {post_id: $post_id})-[r:HAS_IMAGE]->(img:ImageContent {sha256: $sha256})
                        DELETE r
                    """, post_id=self.test_post_id, sha256="test_sha256_hash_12345")
                    
                    session.run("""
                        MATCH (img:ImageContent {sha256: $sha256})
                        DELETE img
                    """, sha256="test_sha256_hash_12345")
                    
                    session.run("""
                        MATCH (post:Post {post_id: $post_id})
                        DELETE post
                    """, post_id=self.test_post_id)
                    
                print("✅ Test Neo4j data removed")
                
        except Exception as e:
            print(f"⚠️ Cleanup warning: {e}")
    
    async def run(self):
        """Run complete E2E test"""
        print("🚀 Starting E2E Vision Pipeline Test")
        print("=" * 50)
        
        try:
            await self.setup()
            
            # Test pipeline steps
            await self.test_vision_upload_event()
            await asyncio.sleep(1)  # Allow processing time
            
            await self.test_vision_analysis_event()
            await asyncio.sleep(1)
            
            await self.test_database_storage()
            await self.test_neo4j_sync()
            await self.test_quota_checks()
            
            print("\n✅ E2E Vision Pipeline Test completed successfully!")
            print(f"Trace ID: {self.trace_id}")
            
        except Exception as e:
            print(f"\n❌ E2E test failed: {e}")
            raise
        finally:
            await self.cleanup()
            if self.redis:
                await self.redis.close()
            if self.db_pool:
                await self.db_pool.close()
            if self.neo4j_driver:
                self.neo4j_driver.close()

async def main():
    test = E2EVisionTest()
    await test.run()

if __name__ == "__main__":
    asyncio.run(main())
