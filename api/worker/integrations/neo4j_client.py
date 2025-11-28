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
        # Context7: Исправлен дефолтный пароль (должен совпадать с docker-compose.yml)
        env_uri = os.getenv("NEO4J_URI") or os.getenv("NEO4J_URL")
        self.uri = uri or env_uri or "neo4j://neo4j:7687"
        self.username = username or os.getenv("NEO4J_USER", "neo4j")
        self.password = password or os.getenv("NEO4J_PASSWORD", "neo4j123")
        
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
        indexed_at: str = None,
        content: Optional[str] = None,
        telegram_message_id: Optional[int] = None,
        tg_channel_id: Optional[int] = None,
        posted_at: Optional[str] = None,
        channel_title: Optional[str] = None
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
                # Context7: ensure_ascii=False для корректного сохранения кириллицы и специальных символов
                import json
                enrichment_json = json.dumps(enrichment_data, ensure_ascii=False, default=str) if enrichment_data else None
                
                # Context7: Обработка null user_id - используем 'system' как fallback
                # Neo4j не поддерживает null в ключевых свойствах для MERGE
                effective_user_id = user_id if user_id else 'system'
                
                query = """
                MERGE (p:Post {post_id: $post_id})
                SET p.user_id = $effective_user_id,
                    p.tenant_id = $tenant_id,
                    p.channel_id = $channel_id,
                    p.expires_at = $expires_at,
                    p.indexed_at = $indexed_at,
                    p.enrichment_data = $enrichment_data,
                    p.content = coalesce($content, p.content),
                    p.telegram_message_id = coalesce($telegram_message_id, p.telegram_message_id),
                    p.tg_channel_id = coalesce($tg_channel_id, p.tg_channel_id),
                    p.posted_at = coalesce($posted_at, p.posted_at),
                    p.channel_title = coalesce($channel_title, p.channel_title, '')
                MERGE (u:User {user_id: $effective_user_id})
                SET u.tenant_id = $tenant_id
                MERGE (c:Channel {channel_id: $channel_id})
                SET c.tenant_id = $tenant_id
                MERGE (u)-[:OWNS]->(p)
                MERGE (c)-[:HAS_POST]->(p)
                RETURN p.post_id as post_id
                """
                
                trimmed_content = None
                if content:
                    trimmed_content = content[:2048]
                
                result = await session.run(
                    query,
                    post_id=post_id,
                    effective_user_id=effective_user_id,
                    tenant_id=tenant_id,
                    channel_id=channel_id,
                    expires_at=expires_at,
                    indexed_at=indexed_at or datetime.now(timezone.utc).isoformat(),
                    enrichment_data=enrichment_json,
                    content=trimmed_content,
                    telegram_message_id=telegram_message_id,
                    tg_channel_id=tg_channel_id,
                    posted_at=posted_at,
                    channel_title=channel_title
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
        """
        Создание связей с тегами и Topic узлов.
        
        Context7: Создаёт:
        - Tag узлы с связью TAGGED_AS
        - Topic узлы из тегов (нормализованных) с связью HAS_TOPIC
        - RELATED_TO связи между Topic узлами (на основе схожести тегов)
        """
        try:
            if not self._driver or not tags:
                return True
            
            await self._ping()
            
            async with self._driver.session() as session:
                topic_names = []
                
                for tag in tags:
                    tag_name = tag.get('name', '')
                    tag_category = tag.get('category', 'other')
                    confidence = tag.get('confidence', 0.0)
                    
                    if not tag_name:
                        continue
                    
                    # Context7: Создаём Tag узел
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
                    
                    # Context7: Нормализуем тег для Topic (lowercase, убираем лишние символы)
                    topic_name = str(tag_name).strip().lower()
                    if topic_name and len(topic_name) > 2:  # Минимальная длина темы
                        topic_names.append(topic_name)
                
                # Context7: Создаём Topic узлы и связи HAS_TOPIC
                if topic_names:
                    for topic_name in set(topic_names):  # Уникальные темы
                        topic_query = """
                        MATCH (p:Post {post_id: $post_id})
                        MERGE (topic:Topic {name: $topic_name})
                        ON CREATE SET topic.created_at = datetime()
                        MERGE (p)-[:HAS_TOPIC]->(topic)
                        RETURN topic.name as topic_name
                        """
                        
                        await session.run(
                            topic_query,
                            post_id=post_id,
                            topic_name=topic_name
                        )
                    
                    # Context7: Создаём RELATED_TO связи между Topic узлами из одного поста
                    # (темы из одного поста считаются связанными)
                    # Используем set(topic_names) для получения уникальных тем
                    unique_topics = set(topic_names)
                    if len(unique_topics) > 1:
                        related_topics_query = """
                        MATCH (p:Post {post_id: $post_id})-[:HAS_TOPIC]->(t1:Topic)
                        MATCH (p)-[:HAS_TOPIC]->(t2:Topic)
                        WHERE t1.name < t2.name
                        MERGE (t1)-[r:RELATED_TO]-(t2)
                        ON CREATE SET r.similarity = 0.5, r.weight = 1
                        ON MATCH SET r.weight = r.weight + 1, r.similarity = 0.5 + (r.weight * 0.1)
                        RETURN count(r) as relationships_created
                        """
                        
                        await session.run(
                            related_topics_query,
                            post_id=post_id
                        )
                
                logger.debug("Tag and Topic relationships created", 
                           post_id=post_id,
                           tags_count=len(tags),
                           topics_count=len(set(topic_names)) if topic_names else 0)
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
    
    async def create_forward_relationship(
        self,
        post_id: str,
        forward_from_peer_id: Optional[Dict[str, Any]] = None,
        forward_from_chat_id: Optional[int] = None,
        forward_from_message_id: Optional[int] = None,
        forward_date: Optional[str] = None,
        forward_from_name: Optional[str] = None
    ) -> bool:
        """
        Создание связи FORWARDED_FROM между постами (Context7 P2: Graph-RAG).
        
        Создаёт:
        - Узел (:ForwardSource) для источника форварда
        - Связь (:Post)-[:FORWARDED_FROM]->(:ForwardSource)
        - Если известно, связь (:Post)-[:FORWARDED_FROM]->(:Post) для исходного поста
        
        Args:
            post_id: ID поста, который содержит форвард
            forward_from_peer_id: Peer ID источника (JSON: user_id/channel_id/chat_id)
            forward_from_chat_id: Chat ID источника (упрощённый доступ)
            forward_from_message_id: Message ID исходного сообщения
            forward_date: Дата оригинального сообщения
            forward_from_name: Имя автора оригинального сообщения
        """
        try:
            if not self._driver:
                return False
            
            await self._ping()
            
            async with self._driver.session() as session:
                # Context7 P2: Создание узла ForwardSource для источника форварда
                # Используем комбинацию peer_id + message_id как уникальный идентификатор
                source_id = None
                source_type = None
                
                if forward_from_peer_id:
                    if 'user_id' in forward_from_peer_id:
                        source_id = str(forward_from_peer_id['user_id'])
                        source_type = 'user'
                    elif 'channel_id' in forward_from_peer_id:
                        source_id = str(forward_from_peer_id['channel_id'])
                        source_type = 'channel'
                    elif 'chat_id' in forward_from_peer_id:
                        source_id = str(forward_from_peer_id['chat_id'])
                        source_type = 'chat'
                
                if not source_id:
                    # Нет информации о источнике - пропускаем
                    logger.debug("No forward source information", post_id=post_id)
                    return False
                
                # Создаём узел ForwardSource
                source_query = """
                MERGE (fs:ForwardSource {source_id: $source_id, source_type: $source_type})
                SET fs.name = $forward_from_name,
                    fs.forward_date = $forward_date,
                    fs.updated_at = datetime()
                RETURN fs.source_id as source_id
                """
                
                await session.run(
                    source_query,
                    source_id=source_id,
                    source_type=source_type,
                    forward_from_name=forward_from_name,
                    forward_date=forward_date
                )
                
                # Создаём связь FORWARDED_FROM к ForwardSource
                forward_query = """
                MATCH (p:Post {post_id: $post_id})
                MATCH (fs:ForwardSource {source_id: $source_id, source_type: $source_type})
                MERGE (p)-[r:FORWARDED_FROM]->(fs)
                SET r.forward_from_message_id = $forward_from_message_id,
                    r.forward_date = $forward_date,
                    r.updated_at = datetime()
                RETURN p.post_id as post_id, fs.source_id as source_id
                """
                
                result = await session.run(
                    forward_query,
                    post_id=post_id,
                    source_id=source_id,
                    source_type=source_type,
                    forward_from_message_id=forward_from_message_id,
                    forward_date=forward_date
                )
                
                record = await result.single()
                if record:
                    logger.debug("Forward relationship created",
                               post_id=post_id,
                               source_id=source_id,
                               source_type=source_type)
                    
                    # Context7 P2: Если известен channel_id и message_id, пытаемся найти исходный пост
                    if forward_from_chat_id and forward_from_message_id:
                        # Пытаемся найти исходный пост в Neo4j (если он уже проиндексирован)
                        original_post_query = """
                        MATCH (orig_p:Post)
                        WHERE orig_p.channel_id = $channel_id 
                          AND orig_p.telegram_message_id = $message_id
                        MATCH (p:Post {post_id: $post_id})
                        MERGE (p)-[r2:FORWARDED_FROM_POST]->(orig_p)
                        SET r2.forward_date = $forward_date,
                            r2.updated_at = datetime()
                        RETURN orig_p.post_id as original_post_id
                        """
                        
                        await session.run(
                            original_post_query,
                            post_id=post_id,
                            channel_id=str(forward_from_chat_id),
                            message_id=forward_from_message_id,
                            forward_date=forward_date
                        )
                    
                    return True
                else:
                    logger.warning("Failed to create forward relationship",
                                 post_id=post_id,
                                 source_id=source_id)
                    return False
                    
        except Exception as e:
            logger.error("Error creating forward relationship",
                        post_id=post_id,
                        error=str(e))
            return False
    
    async def create_reply_relationship(
        self,
        post_id: str,
        reply_to_message_id: Optional[int] = None,
        reply_to_chat_id: Optional[int] = None,
        thread_id: Optional[int] = None
    ) -> bool:
        """
        Создание связи REPLIES_TO между постами (Context7 P2: Graph-RAG).
        
        Создаёт:
        - Связь (:Post)-[:REPLIES_TO {thread_id}]->(:Post) для исходного поста
        - Если thread_id указан, создаёт связь к треду
        
        Args:
            post_id: ID поста-ответа
            reply_to_message_id: Message ID поста, на который отвечают
            reply_to_chat_id: Chat ID канала/чата исходного поста (может быть числом или UUID строкой)
            thread_id: ID треда (для каналов с комментариями)
        """
        try:
            if not self._driver or not reply_to_message_id:
                return False
            
            await self._ping()
            
            async with self._driver.session() as session:
                # Context7 P2: Улучшенный поиск исходного поста
                # Поддерживаем поиск по разным форматам channel_id:
                # 1. По channel_id (UUID строка)
                # 2. По tg_channel_id (число, если есть)
                # 3. По комбинации обоих
                
                # Нормализуем reply_to_chat_id для поиска
                channel_id_str = str(reply_to_chat_id) if reply_to_chat_id else None
                
                # Context7 P2: Расширенный поиск исходного поста
                # Ищем по channel_id (UUID) или tg_channel_id (число) + telegram_message_id
                reply_query = """
                MATCH (p:Post {post_id: $post_id})
                MATCH (orig_p:Post)
                WHERE orig_p.telegram_message_id = $message_id
                  AND (
                    orig_p.channel_id = $channel_id_str
                    OR orig_p.tg_channel_id = $chat_id_num
                  )
                MERGE (p)-[r:REPLIES_TO]->(orig_p)
                SET r.thread_id = $thread_id,
                    r.updated_at = datetime()
                RETURN orig_p.post_id as original_post_id
                """
                
                result = await session.run(
                    reply_query,
                    post_id=post_id,
                    channel_id_str=channel_id_str,
                    chat_id_num=reply_to_chat_id,
                    message_id=reply_to_message_id,
                    thread_id=thread_id
                )
                
                record = await result.single()
                if record:
                    logger.debug("Reply relationship created",
                               post_id=post_id,
                               original_post_id=record['original_post_id'],
                               thread_id=thread_id)
                    return True
                else:
                    # Исходный пост ещё не проиндексирован
                    # Context7 P2: Попытка найти по channel_id из текущего поста
                    # (если reply_to_chat_id совпадает с channel_id текущего поста)
                    fallback_query = """
                    MATCH (p:Post {post_id: $post_id})
                    MATCH (orig_p:Post)
                    WHERE orig_p.telegram_message_id = $message_id
                      AND orig_p.channel_id = p.channel_id
                    MERGE (p)-[r:REPLIES_TO]->(orig_p)
                    SET r.thread_id = $thread_id,
                        r.updated_at = datetime()
                    RETURN orig_p.post_id as original_post_id
                    """
                    
                    fallback_result = await session.run(
                        fallback_query,
                        post_id=post_id,
                        message_id=reply_to_message_id,
                        thread_id=thread_id
                    )
                    
                    fallback_record = await fallback_result.single()
                    if fallback_record:
                        logger.debug("Reply relationship created (fallback by same channel)",
                                   post_id=post_id,
                                   original_post_id=fallback_record['original_post_id'],
                                   thread_id=thread_id)
                        return True
                    
                    # Исходный пост не найден - логируем для последующего backfilling
                    logger.debug("Original post not found in graph",
                               post_id=post_id,
                               reply_to_message_id=reply_to_message_id,
                               reply_to_chat_id=reply_to_chat_id)
                    # Связь будет создана позже при индексации исходного поста
                    # Для этого можно использовать отдельный процесс backfilling
                    return True
                    
        except Exception as e:
            logger.error("Error creating reply relationship",
                        post_id=post_id,
                        error=str(e))
            return False
    
    async def create_author_relationship(
        self,
        post_id: str,
        author_peer_id: Optional[Dict[str, Any]] = None,
        author_name: Optional[str] = None,
        author_type: Optional[str] = None  # 'user', 'channel', 'chat'
    ) -> bool:
        """
        Создание связи AUTHOR_OF между автором и постом (Context7 P2: Graph-RAG).
        
        Создаёт:
        - Узел (:Author) для автора (если не существует)
        - Связь (:Author)-[:AUTHOR_OF]->(:Post)
        
        Args:
            post_id: ID поста
            author_peer_id: Peer ID автора (JSON: user_id/channel_id/chat_id)
            author_name: Имя автора
            author_type: Тип автора ('user', 'channel', 'chat')
        """
        try:
            if not self._driver:
                return False
            
            await self._ping()
            
            async with self._driver.session() as session:
                # Определяем author_id и author_type
                author_id = None
                if author_peer_id:
                    if 'user_id' in author_peer_id:
                        author_id = str(author_peer_id['user_id'])
                        author_type = author_type or 'user'
                    elif 'channel_id' in author_peer_id:
                        author_id = str(author_peer_id['channel_id'])
                        author_type = author_type or 'channel'
                    elif 'chat_id' in author_peer_id:
                        author_id = str(author_peer_id['chat_id'])
                        author_type = author_type or 'chat'
                
                if not author_id:
                    # Нет информации об авторе - пропускаем
                    logger.debug("No author information", post_id=post_id)
                    return False
                
                # Context7 P2: Создаём узел Author (или используем существующий User/Channel)
                # Используем единый тип Author для унификации
                author_query = """
                MERGE (a:Author {author_id: $author_id, author_type: $author_type})
                SET a.name = coalesce($author_name, a.name),
                    a.updated_at = datetime()
                RETURN a.author_id as author_id
                """
                
                await session.run(
                    author_query,
                    author_id=author_id,
                    author_type=author_type,
                    author_name=author_name
                )
                
                # Создаём связь AUTHOR_OF
                author_rel_query = """
                MATCH (p:Post {post_id: $post_id})
                MATCH (a:Author {author_id: $author_id, author_type: $author_type})
                MERGE (a)-[r:AUTHOR_OF]->(p)
                SET r.updated_at = datetime()
                RETURN p.post_id as post_id, a.author_id as author_id
                """
                
                result = await session.run(
                    author_rel_query,
                    post_id=post_id,
                    author_id=author_id,
                    author_type=author_type
                )
                
                record = await result.single()
                if record:
                    logger.debug("Author relationship created",
                               post_id=post_id,
                               author_id=author_id,
                               author_type=author_type)
                    return True
                else:
                    logger.warning("Failed to create author relationship",
                                 post_id=post_id,
                                 author_id=author_id)
                    return False
                    
        except Exception as e:
            logger.error("Error creating author relationship",
                        post_id=post_id,
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
    
    # ============================================================================
    # P3: PERSONA AND DIALOGUE NODES (Context7 P3: Sideloading)
    # ============================================================================
    
    async def create_persona_node(
        self,
        user_id: str,
        tenant_id: str,
        telegram_id: int,
        persona_name: Optional[str] = None,
        persona_metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Создание узла :Persona для пользователя (Context7 P3).
        
        Создаёт:
        - Узел (:Persona {user_id, tenant_id, telegram_id, ...})
        - Связь (:User)-[:HAS_PERSONA]->(:Persona)
        
        Args:
            user_id: UUID пользователя
            tenant_id: UUID tenant
            telegram_id: Telegram ID пользователя
            persona_name: Имя persona (опционально)
            persona_metadata: Дополнительные метаданные (опционально)
        """
        try:
            if not self._driver:
                return False
            
            await self._ping()
            
            async with self._driver.session() as session:
                import json
                metadata_json = json.dumps(persona_metadata, ensure_ascii=False, default=str) if persona_metadata else None
                
                query = """
                MERGE (u:User {user_id: $user_id})
                SET u.tenant_id = $tenant_id
                MERGE (p:Persona {user_id: $user_id, tenant_id: $tenant_id})
                SET p.telegram_id = $telegram_id,
                    p.name = coalesce($persona_name, p.name),
                    p.metadata = $persona_metadata,
                    p.updated_at = datetime()
                MERGE (u)-[:HAS_PERSONA]->(p)
                RETURN p.user_id as user_id
                """
                
                result = await session.run(
                    query,
                    user_id=user_id,
                    tenant_id=tenant_id,
                    telegram_id=telegram_id,
                    persona_name=persona_name,
                    persona_metadata=metadata_json
                )
                
                record = await result.single()
                if record:
                    logger.debug("Persona node created/updated",
                               user_id=user_id,
                               tenant_id=tenant_id)
                    return True
                else:
                    logger.warning("Failed to create persona node",
                                 user_id=user_id)
                    return False
                    
        except Exception as e:
            logger.error("Error creating persona node",
                        user_id=user_id,
                        error=str(e))
            return False
    
    async def create_dialogue_node(
        self,
        user_id: str,
        tenant_id: str,
        dialogue_id: str,
        dialogue_type: str,  # 'dm' or 'group'
        peer_id: int,
        peer_name: Optional[str] = None,
        dialogue_metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Создание узла :Dialogue для диалога (Context7 P3).
        
        Создаёт:
        - Узел (:Dialogue {dialogue_id, user_id, tenant_id, dialogue_type, ...})
        - Связь (:Persona)-[:HAS_DIALOGUE]->(:Dialogue)
        
        Args:
            user_id: UUID пользователя
            tenant_id: UUID tenant
            dialogue_id: Уникальный ID диалога (например, peer_id или UUID)
            dialogue_type: Тип диалога ('dm' или 'group')
            peer_id: Telegram ID собеседника/группы
            peer_name: Имя собеседника/группы (опционально)
            dialogue_metadata: Дополнительные метаданные (опционально)
        """
        try:
            if not self._driver:
                return False
            
            await self._ping()
            
            async with self._driver.session() as session:
                import json
                metadata_json = json.dumps(dialogue_metadata, ensure_ascii=False, default=str) if dialogue_metadata else None
                
                query = """
                MATCH (pers:Persona {user_id: $user_id, tenant_id: $tenant_id})
                MERGE (d:Dialogue {dialogue_id: $dialogue_id, user_id: $user_id, tenant_id: $tenant_id})
                SET d.dialogue_type = $dialogue_type,
                    d.peer_id = $peer_id,
                    d.peer_name = coalesce($peer_name, d.peer_name),
                    d.metadata = $dialogue_metadata,
                    d.updated_at = datetime()
                MERGE (pers)-[:HAS_DIALOGUE]->(d)
                RETURN d.dialogue_id as dialogue_id
                """
                
                result = await session.run(
                    query,
                    user_id=user_id,
                    tenant_id=tenant_id,
                    dialogue_id=dialogue_id,
                    dialogue_type=dialogue_type,
                    peer_id=peer_id,
                    peer_name=peer_name,
                    dialogue_metadata=metadata_json
                )
                
                record = await result.single()
                if record:
                    logger.debug("Dialogue node created/updated",
                               dialogue_id=dialogue_id,
                               dialogue_type=dialogue_type)
                    return True
                else:
                    logger.warning("Failed to create dialogue node",
                                 dialogue_id=dialogue_id)
                    return False
                    
        except Exception as e:
            logger.error("Error creating dialogue node",
                        dialogue_id=dialogue_id,
                        error=str(e))
            return False
    
    async def create_persona_message_relationship(
        self,
        post_id: str,
        user_id: str,
        tenant_id: str,
        dialogue_id: str,
        dialogue_type: str,
        peer_id: int
    ) -> bool:
        """
        Создание связей между persona message и dialogue (Context7 P3).
        
        Создаёт:
        - Связь (:Post)-[:IN_DIALOGUE]->(:Dialogue)
        - Связь (:Persona)-[:SENT_MESSAGE]->(:Post) (если отправитель - пользователь)
        
        Args:
            post_id: ID поста (сообщения) в PostgreSQL
            user_id: UUID пользователя
            tenant_id: UUID tenant
            dialogue_id: ID диалога
            dialogue_type: Тип диалога ('dm' или 'group')
            peer_id: Telegram ID собеседника/группы
        """
        try:
            if not self._driver:
                return False
            
            await self._ping()
            
            async with self._driver.session() as session:
                # Создаём связь между Post и Dialogue
                query = """
                MATCH (p:Post {post_id: $post_id})
                MATCH (d:Dialogue {dialogue_id: $dialogue_id, user_id: $user_id, tenant_id: $tenant_id})
                MERGE (p)-[r:IN_DIALOGUE]->(d)
                SET r.updated_at = datetime()
                RETURN p.post_id as post_id, d.dialogue_id as dialogue_id
                """
                
                result = await session.run(
                    query,
                    post_id=post_id,
                    user_id=user_id,
                    tenant_id=tenant_id,
                    dialogue_id=dialogue_id
                )
                
                record = await result.single()
                if record:
                    # Создаём связь между Persona и Post (если нужно)
                    # Можно определить, является ли пользователь отправителем, по автору поста
                    persona_query = """
                    MATCH (pers:Persona {user_id: $user_id, tenant_id: $tenant_id})
                    MATCH (p:Post {post_id: $post_id})
                    MERGE (pers)-[r:SENT_MESSAGE]->(p)
                    SET r.updated_at = datetime()
                    RETURN pers.user_id as user_id
                    """
                    
                    await session.run(
                        persona_query,
                        user_id=user_id,
                        tenant_id=tenant_id,
                        post_id=post_id
                    )
                    
                    logger.debug("Persona message relationship created",
                               post_id=post_id,
                               dialogue_id=dialogue_id)
                    return True
                else:
                    logger.warning("Failed to create persona message relationship",
                                 post_id=post_id,
                                 dialogue_id=dialogue_id)
                    return False
                    
        except Exception as e:
            logger.error("Error creating persona message relationship",
                        post_id=post_id,
                        dialogue_id=dialogue_id,
                        error=str(e))
            return False
    
    async def create_ocr_entities(
        self,
        post_id: str,
        entities: List[Dict[str, Any]],
        ocr_context: Optional[str] = None
    ) -> bool:
        """
        Создание Entity узлов из извлеченных сущностей OCR.
        
        Context7: Создает Entity nodes с типами ORG, PRODUCT, PERSON, LOC
        и связи (:Post)-[:MENTIONS {source: "ocr"}]->(:Entity)
        
        Args:
            post_id: ID поста
            entities: Список сущностей [{"text": "...", "type": "ORG|PRODUCT|PERSON|LOC", "confidence": 0.0-1.0}]
            ocr_context: Контекст из OCR текста (опционально)
        
        Returns:
            True если успешно, False в случае ошибки
        """
        try:
            if not self._driver or not entities:
                return True
            
            await self._ping()
            
            async with self._driver.session() as session:
                # Context7: Батч-операция через UNWIND для производительности
                query = """
                MATCH (p:Post {post_id: $post_id})
                UNWIND $entities as entity
                MERGE (e:Entity {name: entity.text, type: entity.type})
                SET e.confidence = entity.confidence,
                    e.source = "ocr",
                    e.updated_at = datetime()
                MERGE (p)-[r:MENTIONS {
                    source: "ocr",
                    confidence: entity.confidence,
                    context: $ocr_context
                }]->(e)
                ON CREATE SET r.created_at = datetime()
                ON MATCH SET r.updated_at = datetime()
                RETURN count(e) as entities_created
                """
                
                result = await session.run(
                    query,
                    post_id=post_id,
                    entities=entities,
                    ocr_context=ocr_context[:500] if ocr_context else None  # Ограничиваем длину контекста
                )
                
                record = await result.single()
                if record:
                    entities_count = record.get("entities_created", 0)
                    logger.debug(
                        "OCR entities created",
                        post_id=post_id,
                        entities_count=entities_count,
                        total_entities=len(entities)
                    )
                    return True
                else:
                    logger.warning("Failed to create OCR entities", post_id=post_id)
                    return False
                    
        except Exception as e:
            logger.error(
                "Error creating OCR entities",
                post_id=post_id,
                error=str(e)
            )
            return False
    
    async def link_post_to_ocr_entities(
        self,
        post_id: str,
        entities: List[Dict[str, Any]],
        ocr_text: Optional[str] = None
    ) -> bool:
        """
        Создание связей между Post и Entity узлами из OCR.
        
        Context7: Алиас для create_ocr_entities для обратной совместимости.
        
        Args:
            post_id: ID поста
            entities: Список сущностей
            ocr_text: OCR текст для контекста (опционально)
        
        Returns:
            True если успешно, False в случае ошибки
        """
        return await self.create_ocr_entities(
            post_id=post_id,
            entities=entities,
            ocr_context=ocr_text[:500] if ocr_text else None  # Ограничиваем длину контекста
        )
    
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
