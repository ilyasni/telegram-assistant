"""
Neo4j Client с async-loop provider для предотвращения "Task got Future attached to a different loop"
[C7-ID: WORKER-NEO4J-PROVIDER-001]

Поддерживает единый провайдер драйвера с проверкой event loop
"""

import asyncio
import os
import logging
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
import structlog
from neo4j import AsyncGraphDatabase
from neo4j.exceptions import ServiceUnavailable, AuthError

logger = structlog.get_logger()

# ============================================================================
# NEO4J CLIENT
# ============================================================================

class Neo4jClient:
    """
    Neo4j клиент с async-loop provider.
    
    Поддерживает:
    - Единый провайдер драйвера с проверкой event loop
    - Health-пинг перед операциями
    - MERGE с параметрами для upsert
    - Сквозной TTL в properties
    - Метрики и мониторинг
    """
    
    def __init__(
        self, 
        uri: str = None,
        username: Optional[str] = None,
        password: Optional[str] = None
    ):
        # Источник правды — env, с возможностью явной передачи аргументов
        env_uri = os.getenv("NEO4J_URI") or os.getenv("NEO4J_URL")
        self.uri = uri or env_uri or "neo4j://neo4j:7687"
        self.username = username or os.getenv("NEO4J_USER", "neo4j")
        self.password = password or os.getenv("NEO4J_PASSWORD", "changeme")
        
        # [C7-ID: WORKER-NEO4J-PROVIDER-001] - Async-loop provider
        self._driver = None
        self._current_loop = None
        self._session_pool = None
        
        logger.info("Neo4jClient initialized", uri=self.uri, username=self.username)
    
    async def connect(self):
        """Подключение к Neo4j с проверкой event loop."""
        try:
            # [C7-ID: WORKER-NEO4J-PROVIDER-001] - Проверка event loop
            current_loop = asyncio.get_running_loop()
            if self._current_loop is not None and self._current_loop != current_loop:
                logger.warning("Event loop changed, reconnecting Neo4j driver")
                await self.close()
            
            if self._driver is None or self._current_loop != current_loop:
                # Создание нового драйвера в текущем event loop
                self._driver = AsyncGraphDatabase.driver(
                    self.uri,
                    auth=(self.username, self.password)
                )
                self._current_loop = current_loop
                
                # Проверка подключения
                await self._ping()
                
                logger.info("Neo4jClient connected successfully")
            
        except Exception as e:
            logger.error("Failed to connect to Neo4j", error=str(e))
            raise
    
    async def _ping(self):
        """Health-пинг перед операциями."""
        try:
            async with self._driver.session() as session:
                result = await session.run("RETURN 1 as ping")
                record = await result.single()
                if record and record["ping"] == 1:
                    logger.debug("Neo4j ping successful")
                else:
                    raise Exception("Neo4j ping failed")
        except Exception as e:
            logger.error("Neo4j ping failed", error=str(e))
            raise
    
    async def create_post_node(
        self,
        post_id: str,
        user_id: str,
        tenant_id: str,
        channel_id: str,
        expires_at: str,
        enrichment_data: Optional[Dict[str, Any]] = None,
        indexed_at: str = None
    ) -> bool:
        """
        Создание узла поста с expires_at property.
        
        [C7-ID: WORKER-NEO4J-PROVIDER-001] - MERGE с параметрами для upsert
        """
        try:
            if not self._driver:
                logger.error("Neo4j driver not initialized")
                return False
            
            # Health-пинг перед операцией
            await self._ping()
            
            async with self._driver.session() as session:
                # [C7-ID: WORKER-NEO4J-PROVIDER-001] - MERGE с параметрами (никогда f-strings)
                # Context7: Сериализация enrichment_data в JSON для Neo4j
                import json
                enrichment_json = json.dumps(enrichment_data) if enrichment_data else None
                
                query = """
                MERGE (p:Post {post_id: $post_id})
                SET p.user_id = $user_id,
                    p.tenant_id = $tenant_id,
                    p.channel_id = $channel_id,
                    p.expires_at = $expires_at,
                    p.indexed_at = $indexed_at,
                    p.enrichment_data = $enrichment_data
                MERGE (u:User {user_id: $user_id})
                SET u.tenant_id = $tenant_id
                MERGE (c:Channel {channel_id: $channel_id})
                SET c.tenant_id = $tenant_id
                MERGE (u)-[:OWNS]->(p)
                MERGE (c)-[:HAS_POST]->(p)
                RETURN p.post_id as post_id
                """
                
                result = await session.run(
                    query,
                    post_id=post_id,
                    user_id=user_id,
                    tenant_id=tenant_id,
                    channel_id=channel_id,
                    expires_at=expires_at,
                    indexed_at=indexed_at or datetime.now(timezone.utc).isoformat(),
                    enrichment_data=enrichment_json
                )
                
                record = await result.single()
                if record and record["post_id"] == post_id:
                    logger.debug("Post node created/updated successfully", post_id=post_id)
                    return True
                else:
                    logger.error("Failed to create post node", post_id=post_id)
                    return False
                    
        except Exception as e:
            logger.error("Error creating post node", 
                        post_id=post_id,
                        error=str(e))
            return False
    
    async def create_tag_relationships(
        self,
        post_id: str,
        tags: List[Dict[str, Any]]
    ) -> bool:
        """Создание связей с тегами."""
        try:
            if not self._driver or not tags:
                return True
            
            await self._ping()
            
            async with self._driver.session() as session:
                for tag in tags:
                    tag_name = tag.get('name', '')
                    tag_category = tag.get('category', 'other')
                    confidence = tag.get('confidence', 0.0)
                    
                    if not tag_name:
                        continue
                    
                    # [C7-ID: WORKER-NEO4J-PROVIDER-001] - MERGE с параметрами
                    query = """
                    MATCH (p:Post {post_id: $post_id})
                    MERGE (t:Tag {name: $tag_name})
                    SET t.category = $tag_category
                    MERGE (p)-[r:TAGGED_AS {confidence: $confidence}]->(t)
                    RETURN p.post_id as post_id, t.name as tag_name
                    """
                    
                    await session.run(
                        query,
                        post_id=post_id,
                        tag_name=tag_name,
                        tag_category=tag_category,
                        confidence=confidence
                    )
                
                logger.debug("Tag relationships created", 
                           post_id=post_id,
                           tags_count=len(tags))
                return True
                
        except Exception as e:
            logger.error("Error creating tag relationships",
                        post_id=post_id,
                        error=str(e))
            return False
    
    async def delete_post_node(self, post_id: str) -> bool:
        """Удаление узла поста и связанных данных."""
        try:
            if not self._driver:
                return False
            
            await self._ping()
            
            async with self._driver.session() as session:
                # [C7-ID: WORKER-NEO4J-PROVIDER-001] - DETACH DELETE с параметрами
                query = """
                MATCH (p:Post {post_id: $post_id})
                DETACH DELETE p
                RETURN count(p) as deleted_count
                """
                
                result = await session.run(query, post_id=post_id)
                record = await result.single()
                
                if record and record["deleted_count"] > 0:
                    logger.debug("Post node deleted successfully", post_id=post_id)
                    return True
                else:
                    logger.warning("Post node not found for deletion", post_id=post_id)
                    return False
                    
        except Exception as e:
            logger.error("Error deleting post node",
                        post_id=post_id,
                        error=str(e))
            return False
    
    async def create_image_content_node(
        self,
        post_id: str,
        sha256: str,
        s3_key: Optional[str] = None,
        mime_type: Optional[str] = None,
        vision_classification: Optional[str] = None,
        is_meme: bool = False,
        labels: Optional[List[str]] = None,
        provider: Optional[str] = None,
        trace_id: Optional[str] = None
    ) -> bool:
        """
        Создание узла ImageContent и связей с Post и Labels.
        
        Context7: Создаёт узлы (:Image {sha256, s3_key, ...}) и связи:
        - (:Post)-[:HAS_IMAGE]->(:Image)
        - (:Image)-[:HAS_LABEL]->(:Label)
        """
        try:
            if not self._driver:
                return False
            
            await self._ping()
            
            async with self._driver.session() as session:
                # Создание узла ImageContent
                query = """
                MATCH (p:Post {post_id: $post_id})
                MERGE (img:Image {sha256: $sha256})
                SET img.s3_key = $s3_key,
                    img.mime_type = $mime_type,
                    img.classification = $vision_classification,
                    img.is_meme = $is_meme,
                    img.provider = $provider
                MERGE (p)-[:HAS_IMAGE]->(img)
                RETURN img.sha256 as sha256
                """
                
                await session.run(
                    query,
                    post_id=post_id,
                    sha256=sha256,
                    s3_key=s3_key,
                    mime_type=mime_type,
                    vision_classification=vision_classification,
                    is_meme=is_meme,
                    provider=provider
                )
                
                # Создание узлов Label и связей HAS_LABEL
                if labels:
                    await self.create_label_nodes(post_id, sha256, labels)
                
                logger.debug("ImageContent node created",
                           post_id=post_id,
                           sha256=sha256,
                           labels_count=len(labels) if labels else 0,
                           trace_id=trace_id)
                return True
                
        except Exception as e:
            logger.error("Error creating image content node",
                        post_id=post_id,
                        sha256=sha256,
                        error=str(e),
                        trace_id=trace_id)
            return False
    
    async def create_label_nodes(
        self,
        post_id: str,
        image_sha256: str,
        labels: List[str]
    ) -> bool:
        """
        Создание узлов Label и связей (:Image)-[:HAS_LABEL]->(:Label).
        """
        try:
            if not self._driver or not labels:
                return True
            
            await self._ping()
            
            async with self._driver.session() as session:
                for label in labels:
                    if not label or not str(label).strip():
                        continue
                    
                    label_normalized = str(label).strip().lower()
                    
                    query = """
                    MATCH (img:Image {sha256: $sha256})
                    MERGE (l:Label {name: $label_name})
                    MERGE (img)-[:HAS_LABEL]->(l)
                    RETURN l.name as label_name
                    """
                    
                    await session.run(
                        query,
                        sha256=image_sha256,
                        label_name=label_normalized
                    )
                
                logger.debug("Label nodes created",
                           post_id=post_id,
                           sha256=image_sha256,
                           labels_count=len(labels))
                return True
                
        except Exception as e:
            logger.error("Error creating label nodes",
                        post_id=post_id,
                        sha256=image_sha256,
                        error=str(e))
            return False
    
    async def create_webpage_node(
        self,
        post_id: str,
        url: str,
        s3_html_key: Optional[str] = None,
        url_hash: Optional[str] = None,
        content_sha256: Optional[str] = None
    ) -> bool:
        """
        Создание узла WebPage и связи (:Post)-[:REFERS_TO]->(:WebPage).
        
        Context7: Создаёт узлы (:WebPage {url, s3_html_key, ...}) и связи с Post.
        """
        try:
            if not self._driver:
                return False
            
            await self._ping()
            
            async with self._driver.session() as session:
                # Используем url_hash или url в качестве уникального идентификатора
                webpage_id = url_hash or url
                
                query = """
                MATCH (p:Post {post_id: $post_id})
                MERGE (wp:WebPage {url: $url})
                SET wp.s3_html_key = $s3_html_key,
                    wp.url_hash = $url_hash,
                    wp.content_sha256 = $content_sha256
                MERGE (p)-[:REFERS_TO]->(wp)
                RETURN wp.url as url
                """
                
                await session.run(
                    query,
                    post_id=post_id,
                    url=url,
                    s3_html_key=s3_html_key,
                    url_hash=url_hash,
                    content_sha256=content_sha256
                )
                
                logger.debug("WebPage node created",
                           post_id=post_id,
                           url=url,
                           s3_html_key=s3_html_key)
                return True
                
        except Exception as e:
            logger.error("Error creating webpage node",
                        post_id=post_id,
                        url=url,
                        error=str(e))
            return False
    
    async def cleanup_orphan_tags(self) -> int:
        """
        [C7-ID: WORKER-ORPHAN-002] - Очистка висячих тегов без связей.
        
        Удаляет теги, которые не связаны ни с какими постами.
        """
        try:
            if not self._driver:
                return 0
            
            await self._ping()
            
            async with self._driver.session() as session:
                # Поиск и удаление висячих тегов
                query = """
                MATCH (t:Tag)
                WHERE NOT (t)<-[:TAGGED_AS]-(:Post)
                DELETE t
                RETURN count(t) as deleted_count
                """
                
                result = await session.run(query)
                record = await result.single()
                
                deleted_count = record["deleted_count"] if record else 0
                
                if deleted_count > 0:
                    logger.info("Orphan tags cleaned up", deleted_count=deleted_count)
                
                return deleted_count
                
        except Exception as e:
            logger.error("Error cleaning up orphan tags", error=str(e))
            return 0
    
    async def cleanup_expired_posts(self) -> int:
        """
        Очистка expired постов по expires_at property.
        
        [C7-ID: WORKER-INDEXING-002] - Сквозной TTL в Neo4j
        """
        try:
            if not self._driver:
                return 0
            
            await self._ping()
            
            current_time = datetime.now(timezone.utc).isoformat()
            
            async with self._driver.session() as session:
                # Поиск и удаление expired постов
                query = """
                MATCH (p:Post)
                WHERE p.expires_at < $current_time
                DETACH DELETE p
                RETURN count(p) as deleted_count
                """
                
                result = await session.run(query, current_time=current_time)
                record = await result.single()
                
                deleted_count = record["deleted_count"] if record else 0
                
                if deleted_count > 0:
                    logger.info("Expired posts cleaned up", deleted_count=deleted_count)
                
                return deleted_count
                
        except Exception as e:
            logger.error("Error cleaning up expired posts", error=str(e))
            return 0
    
    async def get_post_stats(self) -> Dict[str, Any]:
        """Получение статистики постов в графе."""
        try:
            if not self._driver:
                return {}
            
            await self._ping()
            
            async with self._driver.session() as session:
                # Статистика узлов
                stats_query = """
                MATCH (p:Post)
                OPTIONAL MATCH (p)-[:TAGGED_AS]->(t:Tag)
                RETURN 
                    count(DISTINCT p) as posts_count,
                    count(DISTINCT t) as tags_count,
                    count(DISTINCT p.user_id) as users_count,
                    count(DISTINCT p.channel_id) as channels_count
                """
                
                result = await session.run(stats_query)
                record = await result.single()
                
                if record:
                    return {
                        'posts_count': record['posts_count'],
                        'tags_count': record['tags_count'],
                        'users_count': record['users_count'],
                        'channels_count': record['channels_count']
                    }
                
                return {}
                
        except Exception as e:
            logger.error("Error getting post stats", error=str(e))
            return {}
    
    async def health_check(self) -> bool:
        """Health check для Neo4j."""
        try:
            if not self._driver:
                return False
            
            await self._ping()
            return True
            
        except Exception as e:
            logger.error("Neo4j health check failed", error=str(e))
            return False
    
    async def get_stats(self) -> Dict[str, Any]:
        """Получение общей статистики Neo4j."""
        try:
            if not self._driver:
                return {'connected': False}
            
            await self._ping()
            
            post_stats = await self.get_post_stats()
            
            return {
                'connected': True,
                'driver_initialized': self._driver is not None,
                'current_loop': str(self._current_loop) if self._current_loop else None,
                **post_stats
            }
            
        except Exception as e:
            logger.error("Error getting Neo4j stats", error=str(e))
            return {
                'connected': False,
                'error': str(e)
            }
    
    async def close(self):
        """Закрытие подключения к Neo4j."""
        try:
            if self._driver:
                await self._driver.close()
                self._driver = None
                self._current_loop = None
                logger.info("Neo4jClient connection closed")
        except Exception as e:
            logger.error("Error closing Neo4j connection", error=str(e))
