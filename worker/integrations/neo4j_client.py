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
    
    async def create_album_node(
        self,
        album_id: int,
        grouped_id: int,
        channel_id: str,
        tenant_id: str,
        album_kind: Optional[str] = None,
        items_count: int = 0,
        caption_text: Optional[str] = None,
        posted_at: Optional[str] = None
    ) -> bool:
        """
        Создание узла альбома в Neo4j.
        
        Context7: Создаёт узел (:Album {album_id, grouped_id, ...}) и связи:
        - (:Channel)-[:HAS_ALBUM]->(:Album)
        """
        try:
            if not self._driver:
                return False
            
            await self._ping()
            
            async with self._driver.session() as session:
                query = """
                MATCH (c:Channel {channel_id: $channel_id})
                MERGE (alb:Album {album_id: $album_id})
                SET alb.grouped_id = $grouped_id,
                    alb.tenant_id = $tenant_id,
                    alb.album_kind = $album_kind,
                    alb.items_count = $items_count,
                    alb.caption_text = $caption_text,
                    alb.posted_at = $posted_at
                MERGE (c)-[:HAS_ALBUM]->(alb)
                RETURN alb.album_id as album_id
                """
                
                result = await session.run(
                    query,
                    album_id=str(album_id),
                    grouped_id=grouped_id,
                    channel_id=channel_id,
                    tenant_id=tenant_id,
                    album_kind=album_kind,
                    items_count=items_count,
                    caption_text=caption_text,
                    posted_at=posted_at
                )
                
                record = await result.single()
                if record and record["album_id"] == str(album_id):
                    logger.debug("Album node created/updated successfully", album_id=album_id)
                    return True
                else:
                    logger.error("Failed to create album node", album_id=album_id)
                    return False
                    
        except Exception as e:
            logger.error("Error creating album node",
                        album_id=album_id,
                        error=str(e))
            return False
    
    async def create_album_item_relationships(
        self,
        album_id: int,
        post_id: str,
        position: Optional[int] = None
    ) -> bool:
        """
        Создание связи между альбомом и постом (элементом альбома).
        
        Context7: Создаёт связь (:Album)-[:CONTAINS {position}]->(:Post)
        """
        try:
            if not self._driver:
                return False
            
            await self._ping()
            
            async with self._driver.session() as session:
                query = """
                MATCH (alb:Album {album_id: $album_id})
                MATCH (p:Post {post_id: $post_id})
                MERGE (alb)-[r:CONTAINS]->(p)
                SET r.position = $position
                RETURN alb.album_id as album_id, p.post_id as post_id
                """
                
                result = await session.run(
                    query,
                    album_id=str(album_id),
                    post_id=post_id,
                    position=position
                )
                
                record = await result.single()
                if record:
                    logger.debug(
                        "Album-item relationship created",
                        album_id=album_id,
                        post_id=post_id,
                        position=position
                    )
                    return True
                else:
                    logger.warning(
                        "Failed to create album-item relationship",
                        album_id=album_id,
                        post_id=post_id
                    )
                    return False
                    
        except Exception as e:
            logger.error(
                "Error creating album-item relationship",
                album_id=album_id,
                post_id=post_id,
                error=str(e)
            )
            return False
    
    async def create_album_node_and_relationships(
        self,
        album_id: int,
        post_id: str,
        channel_id: str,
        tenant_id: str,
        position: Optional[int] = None
    ) -> bool:
        """
        Создание узла альбома и связи с постом (удобный метод для indexing_task).
        
        Context7: Создаёт узел альбома (если ещё не существует) и связь CONTAINS.
        Получает метаданные альбома из БД.
        """
        try:
            if not self._driver:
                return False
            
            # Получаем метаданные альбома из БД
            from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
            from sqlalchemy import text
            import os
            
            db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@supabase-db:5432/postgres")
            if db_url.startswith("postgresql://"):
                db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
            
            engine = create_async_engine(db_url)
            async_session = async_sessionmaker(engine, expire_on_commit=False)
            
            async with async_session() as session:
                # Получаем метаданные альбома
                result = await session.execute(
                    text("""
                        SELECT 
                            mg.grouped_id,
                            mg.album_kind,
                            mg.items_count,
                            mg.caption_text,
                            mg.posted_at,
                            mgi.position
                        FROM media_groups mg
                        JOIN media_group_items mgi ON mg.id = mgi.group_id
                        WHERE mg.id = :album_id
                        AND mgi.post_id = :post_id
                        LIMIT 1
                    """),
                    {"album_id": album_id, "post_id": post_id}
                )
                row = result.fetchone()
                
                if row:
                    grouped_id = row[0]
                    album_kind = row[1]
                    items_count = row[2] if row[2] else 0
                    caption_text = row[3]
                    posted_at = row[4].isoformat() if row[4] else None
                    position = row[5] if row[5] is not None else position
                    
                    await engine.dispose()
                    
                    # Создаём узел альбома
                    album_created = await self.create_album_node(
                        album_id=album_id,
                        grouped_id=grouped_id,
                        channel_id=channel_id,
                        tenant_id=tenant_id,
                        album_kind=album_kind,
                        items_count=items_count,
                        caption_text=caption_text,
                        posted_at=posted_at
                    )
                    
                    if album_created:
                        # Создаём связь с постом
                        return await self.create_album_item_relationships(
                            album_id=album_id,
                            post_id=post_id,
                            position=position
                        )
                else:
                    await engine.dispose()
                    logger.debug(
                        "Album metadata not found in DB",
                        album_id=album_id,
                        post_id=post_id
                    )
                    return False
                    
        except Exception as e:
            logger.error(
                "Error creating album node and relationships",
                album_id=album_id,
                post_id=post_id,
                error=str(e)
            )
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
    
    async def find_albums_by_channel(self, channel_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Поиск альбомов по каналу.
        
        Context7: Типовой запрос для работы с альбомами в Neo4j.
        """
        try:
            if not self._driver:
                return []
            
            await self._ping()
            
            async with self._driver.session() as session:
                query = """
                MATCH (c:Channel {channel_id: $channel_id})-[:HAS_ALBUM]->(alb:Album)
                OPTIONAL MATCH (alb)-[:CONTAINS]->(p:Post)
                WITH alb, count(p) as items_count
                RETURN alb.album_id as album_id,
                       alb.grouped_id as grouped_id,
                       alb.album_kind as album_kind,
                       alb.items_count as expected_items,
                       items_count as actual_items,
                       alb.caption_text as caption_text,
                       alb.posted_at as posted_at
                ORDER BY alb.posted_at DESC
                LIMIT $limit
                """
                
                result = await session.run(query, channel_id=channel_id, limit=limit)
                albums = []
                async for record in result:
                    albums.append({
                        'album_id': record['album_id'],
                        'grouped_id': record['grouped_id'],
                        'album_kind': record['album_kind'],
                        'expected_items': record['expected_items'],
                        'actual_items': record['actual_items'],
                        'caption_text': record['caption_text'],
                        'posted_at': record['posted_at']
                    })
                
                return albums
                
        except Exception as e:
            logger.error("Error finding albums by channel", channel_id=channel_id, error=str(e))
            return []
    
    async def find_albums_by_tags(self, tag_names: List[str], limit: int = 10) -> List[Dict[str, Any]]:
        """
        Поиск альбомов по тегам постов.
        
        Context7: Поиск альбомов, содержащих посты с указанными тегами.
        """
        try:
            if not self._driver or not tag_names:
                return []
            
            await self._ping()
            
            async with self._driver.session() as session:
                query = """
                MATCH (t:Tag)
                WHERE t.name IN $tag_names
                MATCH (t)<-[:TAGGED_AS]-(p:Post)<-[:CONTAINS]-(alb:Album)
                WITH alb, count(DISTINCT t.name) as matching_tags
                WHERE matching_tags >= size($tag_names)
                RETURN alb.album_id as album_id,
                       alb.grouped_id as grouped_id,
                       alb.album_kind as album_kind,
                       alb.items_count as items_count,
                       matching_tags,
                       alb.caption_text as caption_text
                ORDER BY matching_tags DESC, alb.posted_at DESC
                LIMIT $limit
                """
                
                result = await session.run(query, tag_names=[t.lower() for t in tag_names], limit=limit)
                albums = []
                async for record in result:
                    albums.append({
                        'album_id': record['album_id'],
                        'grouped_id': record['grouped_id'],
                        'album_kind': record['album_kind'],
                        'items_count': record['items_count'],
                        'matching_tags': record['matching_tags'],
                        'caption_text': record['caption_text']
                    })
                
                return albums
                
        except Exception as e:
            logger.error("Error finding albums by tags", tag_names=tag_names, error=str(e))
            return []
    
    async def get_album_posts(self, album_id: int, ordered: bool = True) -> List[Dict[str, Any]]:
        """
        Получение постов альбома с сохранением порядка.
        
        Context7: Возвращает все посты альбома в порядке position.
        """
        try:
            if not self._driver:
                return []
            
            await self._ping()
            
            async with self._driver.session() as session:
                query = """
                MATCH (alb:Album {album_id: $album_id})-[r:CONTAINS]->(p:Post)
                RETURN p.post_id as post_id,
                       p.user_id as user_id,
                       p.channel_id as channel_id,
                       r.position as position
                ORDER BY r.position ASC
                """
                
                result = await session.run(query, album_id=str(album_id))
                posts = []
                async for record in result:
                    posts.append({
                        'post_id': record['post_id'],
                        'user_id': record['user_id'],
                        'channel_id': record['channel_id'],
                        'position': record['position']
                    })
                
                return posts
                
        except Exception as e:
            logger.error("Error getting album posts", album_id=album_id, error=str(e))
            return []
    
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
