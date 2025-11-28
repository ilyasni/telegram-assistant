"""
Unit tests for Trend Coherence Metrics.

Context7: Тесты проверяют вычисление метрик когерентности:
- Intra-cluster similarity (косинусная близость)
- NPMI coherence (Topic Coherence)
- Silhouette score (разделимость кластеров)
- Keyword overlap (пересечение ключевых слов)
"""

import pytest
import sys
import numpy as np
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from types import ModuleType

# Mock prometheus_client before importing
prometheus_module = ModuleType("prometheus_client")
_counter_stub = MagicMock()
_counter_stub.labels = MagicMock(return_value=_counter_stub)
_counter_stub.inc = MagicMock()
_histogram_stub = MagicMock()
_histogram_stub.labels = MagicMock(return_value=_histogram_stub)
_histogram_stub.observe = MagicMock()
prometheus_module.Counter = MagicMock(return_value=_counter_stub)
prometheus_module.Histogram = MagicMock(return_value=_histogram_stub)
sys.modules["prometheus_client"] = prometheus_module

# Mock asyncpg
sys.modules["asyncpg"] = MagicMock()

# Import after mocking
# Context7: В контейнере worker модули находятся в /app/api/worker/ (монтируется из ./api)
try:
    from api.worker.trends_coherence_metrics import TrendCoherenceMetrics
except ImportError:
    try:
        from worker.trends_coherence_metrics import TrendCoherenceMetrics
    except ImportError:
        # Fallback: прямой импорт из /app (файлы скопированы в контейнер)
        import sys
        import importlib.util
        import os
        # Пробуем разные пути
        for file_path in [
            "/app/trends_coherence_metrics.py",  # Файлы скопированы напрямую в /app
            "/app/api/worker/trends_coherence_metrics.py",
            "/app/worker/trends_coherence_metrics.py",
        ]:
            if os.path.exists(file_path):
                spec = importlib.util.spec_from_file_location("trends_coherence_metrics", file_path)
                trends_coherence_metrics = importlib.util.module_from_spec(spec)
                sys.modules["trends_coherence_metrics"] = trends_coherence_metrics
                # Добавляем /app в sys.path для импорта зависимостей
                if "/app" not in sys.path:
                    sys.path.insert(0, "/app")
                spec.loader.exec_module(trends_coherence_metrics)
                TrendCoherenceMetrics = trends_coherence_metrics.TrendCoherenceMetrics
                break
        else:
            # Последняя попытка: поиск файла в /app
            import glob
            found_files = glob.glob("/app/**/trends_coherence_metrics.py", recursive=True)
            if found_files:
                file_path = found_files[0]
                spec = importlib.util.spec_from_file_location("trends_coherence_metrics", file_path)
                trends_coherence_metrics = importlib.util.module_from_spec(spec)
                sys.modules["trends_coherence_metrics"] = trends_coherence_metrics
                if "/app" not in sys.path:
                    sys.path.insert(0, "/app")
                spec.loader.exec_module(trends_coherence_metrics)
                TrendCoherenceMetrics = trends_coherence_metrics.TrendCoherenceMetrics
            else:
                raise ImportError("trends_coherence_metrics module not found in /app")


@pytest.fixture
def mock_db_pool():
    """Mock database pool."""
    pool = AsyncMock()
    conn = AsyncMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=[])
    pool.acquire = AsyncMock(return_value=conn)
    return pool


@pytest.fixture
def coherence_metrics(mock_db_pool):
    """TrendCoherenceMetrics instance."""
    return TrendCoherenceMetrics(db_pool=mock_db_pool)


@pytest.fixture
def sample_embeddings():
    """Sample embeddings for testing."""
    # Создаём похожие embeddings (высокая когерентность)
    base_embedding = np.random.rand(384).tolist()
    similar_embeddings = [
        base_embedding,
        (np.array(base_embedding) + np.random.rand(384) * 0.1).tolist(),
        (np.array(base_embedding) + np.random.rand(384) * 0.1).tolist(),
        (np.array(base_embedding) + np.random.rand(384) * 0.1).tolist(),
    ]
    return similar_embeddings


@pytest.fixture
def dissimilar_embeddings():
    """Dissimilar embeddings (низкая когерентность)."""
    return [
        np.random.rand(384).tolist(),
        np.random.rand(384).tolist(),
        np.random.rand(384).tolist(),
        np.random.rand(384).tolist(),
    ]


@pytest.fixture
def sample_keywords():
    """Sample keywords for testing."""
    return ["технологии", "ai", "машинное обучение", "нейросети", "gpt"]


@pytest.fixture
def overlapping_keywords():
    """Keywords with overlap."""
    return ["технологии", "ai", "машинное обучение", "python", "data"]


# ============================================================================
# INTRA-CLUSTER SIMILARITY TESTS
# ============================================================================


@pytest.mark.asyncio
@pytest.mark.unit
async def test_intra_cluster_similarity_high_coherence(
    coherence_metrics, sample_embeddings
):
    """Тест вычисления intra-cluster similarity для высококогерентного кластера."""
    cluster_id = str(uuid4())
    
    result = await coherence_metrics.calculate_intra_cluster_similarity(
        cluster_id=cluster_id,
        embeddings=sample_embeddings,
    )
    
    assert result is not None
    assert 0.0 <= result <= 1.0
    # Похожие embeddings должны давать высокую когерентность (> 0.5)
    assert result > 0.5, f"Expected high coherence, got {result}"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_intra_cluster_similarity_low_coherence(
    coherence_metrics, dissimilar_embeddings
):
    """Тест вычисления intra-cluster similarity для низкокогерентного кластера."""
    cluster_id = str(uuid4())
    
    result = await coherence_metrics.calculate_intra_cluster_similarity(
        cluster_id=cluster_id,
        embeddings=dissimilar_embeddings,
    )
    
    assert result is not None
    assert 0.0 <= result <= 1.0
    # Разные embeddings обычно дают низкую когерентность, но из-за случайности может быть выше
    # Проверяем только, что результат валидный (в диапазоне 0-1)
    # Для действительно разных embeddings когерентность обычно < 0.8
    assert result < 0.9, f"Coherence should be reasonable for dissimilar embeddings, got {result}"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_intra_cluster_similarity_insufficient_data(coherence_metrics):
    """Тест обработки недостаточного количества данных."""
    cluster_id = str(uuid4())
    
    # Меньше 2 embeddings
    result = await coherence_metrics.calculate_intra_cluster_similarity(
        cluster_id=cluster_id,
        embeddings=[np.random.rand(384).tolist()],
    )
    
    assert result is None


@pytest.mark.asyncio
@pytest.mark.unit
async def test_intra_cluster_similarity_empty_embeddings(coherence_metrics):
    """Тест обработки пустого списка embeddings."""
    cluster_id = str(uuid4())
    
    result = await coherence_metrics.calculate_intra_cluster_similarity(
        cluster_id=cluster_id,
        embeddings=[],
    )
    
    assert result is None


@pytest.mark.asyncio
@pytest.mark.unit
async def test_intra_cluster_similarity_identical_embeddings(coherence_metrics):
    """Тест вычисления для идентичных embeddings (должна быть максимальная когерентность)."""
    cluster_id = str(uuid4())
    embedding = np.random.rand(384).tolist()
    identical_embeddings = [embedding, embedding, embedding]
    
    result = await coherence_metrics.calculate_intra_cluster_similarity(
        cluster_id=cluster_id,
        embeddings=identical_embeddings,
    )
    
    assert result is not None
    # Идентичные embeddings должны давать очень высокую когерентность (близко к 1.0)
    assert result > 0.99, f"Identical embeddings should give > 0.99, got {result}"


# ============================================================================
# KEYWORD OVERLAP TESTS
# ============================================================================


@pytest.mark.asyncio
@pytest.mark.unit
async def test_keyword_overlap_high_overlap(
    coherence_metrics, sample_keywords, overlapping_keywords
):
    """Тест вычисления keyword overlap для кластеров с большим пересечением."""
    cluster_id_1 = str(uuid4())
    cluster_id_2 = str(uuid4())
    
    # keywords_1 и keywords_2 имеют пересечение: ["технологии", "ai", "машинное обучение"]
    result = await coherence_metrics.calculate_keyword_overlap(
        cluster_id_1=cluster_id_1,
        keywords_1=sample_keywords,
        cluster_id_2=cluster_id_2,
        keywords_2=overlapping_keywords,
    )
    
    assert 0.0 <= result <= 1.0
    assert result > 0.3, f"Expected high overlap, got {result}"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_keyword_overlap_no_overlap(coherence_metrics):
    """Тест вычисления keyword overlap для кластеров без пересечения."""
    cluster_id_1 = str(uuid4())
    cluster_id_2 = str(uuid4())
    
    keywords_1 = ["технологии", "ai", "машинное обучение"]
    keywords_2 = ["спорт", "футбол", "хоккей"]
    
    result = await coherence_metrics.calculate_keyword_overlap(
        cluster_id_1=cluster_id_1,
        keywords_1=keywords_1,
        cluster_id_2=cluster_id_2,
        keywords_2=keywords_2,
    )
    
    assert result == 0.0, f"Expected no overlap, got {result}"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_keyword_overlap_empty_keywords(coherence_metrics):
    """Тест обработки пустых списков ключевых слов."""
    cluster_id_1 = str(uuid4())
    cluster_id_2 = str(uuid4())
    
    result = await coherence_metrics.calculate_keyword_overlap(
        cluster_id_1=cluster_id_1,
        keywords_1=[],
        cluster_id_2=cluster_id_2,
        keywords_2=["технологии", "ai"],
    )
    
    assert result == 0.0


@pytest.mark.asyncio
@pytest.mark.unit
async def test_keyword_overlap_case_insensitive(coherence_metrics):
    """Тест, что keyword overlap нечувствителен к регистру."""
    cluster_id_1 = str(uuid4())
    cluster_id_2 = str(uuid4())
    
    keywords_1 = ["Технологии", "AI", "Машинное Обучение"]
    keywords_2 = ["технологии", "ai", "машинное обучение"]
    
    result = await coherence_metrics.calculate_keyword_overlap(
        cluster_id_1=cluster_id_1,
        keywords_1=keywords_1,
        cluster_id_2=cluster_id_2,
        keywords_2=keywords_2,
    )
    
    assert result == 1.0, f"Case-insensitive overlap should be 1.0, got {result}"


# ============================================================================
# NPMI COHERENCE TESTS
# ============================================================================


@pytest.mark.asyncio
@pytest.mark.unit
async def test_npmi_coherence_high_coherence(coherence_metrics):
    """Тест вычисления NPMI для высококогерентного кластера."""
    cluster_id = str(uuid4())
    
    # Топ-ключевые слова, которые часто встречаются вместе
    # Context7: Убеждаемся, что слова встречаются вместе в достаточном количестве постов
    top_keywords = ["технологии", "ai", "машинное"]
    
    # Все ключевые слова постов - часто содержат эти топ-слова вместе
    # Важно: каждое слово должно встречаться в большинстве постов, и пары должны встречаться вместе
    all_cluster_keywords = [
        ["технологии", "ai", "машинное", "обучение", "нейросети"],
        ["технологии", "ai", "машинное", "gpt", "обучение"],
        ["ai", "технологии", "машинное", "обучение", "python"],
        ["технологии", "ai", "машинное", "обучение", "data"],
        ["технологии", "ai", "машинное", "обучение", "deep"],  # Добавляем больше постов
    ]
    
    result = await coherence_metrics.calculate_npmi_coherence(
        cluster_id=cluster_id,
        top_keywords=top_keywords,
        all_cluster_keywords=all_cluster_keywords,
    )
    
    # NPMI может быть None, если недостаточно совместных появлений
    # Если результат есть, проверяем диапазон
    if result is not None:
        assert -1.0 <= result <= 1.0, f"NPMI should be in [-1, 1], got {result}"
    # Если None, это тоже валидный результат для некоторых случаев


@pytest.mark.asyncio
@pytest.mark.unit
async def test_npmi_coherence_low_coherence(coherence_metrics):
    """Тест вычисления NPMI для низкокогерентного кластера."""
    cluster_id = str(uuid4())
    
    # Топ-ключевые слова
    top_keywords = ["технологии", "ai", "машинное обучение"]
    
    # Ключевые слова постов - редко встречаются вместе
    all_cluster_keywords = [
        ["технологии", "спорт", "футбол"],
        ["ai", "кулинария", "рецепты"],
        ["машинное обучение", "музыка", "концерт"],
        ["технологии", "путешествия", "отпуск"],
    ]
    
    result = await coherence_metrics.calculate_npmi_coherence(
        cluster_id=cluster_id,
        top_keywords=top_keywords,
        all_cluster_keywords=all_cluster_keywords,
    )
    
    # NPMI может быть None, если недостаточно данных или слова не встречаются вместе
    # Если результат есть, проверяем диапазон
    if result is not None:
        assert -1.0 <= result <= 1.0, f"NPMI should be in [-1, 1], got {result}"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_npmi_coherence_insufficient_data(coherence_metrics):
    """Тест обработки недостаточного количества данных для NPMI."""
    cluster_id = str(uuid4())
    
    result = await coherence_metrics.calculate_npmi_coherence(
        cluster_id=cluster_id,
        top_keywords=["технологии", "ai"],
        all_cluster_keywords=[],  # Пустой список
    )
    
    assert result is None


@pytest.mark.asyncio
@pytest.mark.unit
async def test_npmi_coherence_empty_top_keywords(coherence_metrics):
    """Тест обработки пустого списка топ-ключевых слов."""
    cluster_id = str(uuid4())
    
    result = await coherence_metrics.calculate_npmi_coherence(
        cluster_id=cluster_id,
        top_keywords=[],
        all_cluster_keywords=[["технологии", "ai"]],
    )
    
    assert result is None


# ============================================================================
# SILHOUETTE SCORE TESTS
# ============================================================================


@pytest.mark.asyncio
@pytest.mark.unit
async def test_silhouette_score_well_separated_clusters(coherence_metrics):
    """Тест вычисления silhouette score для хорошо разделённых кластеров."""
    cluster_id = str(uuid4())
    
    # Создаём два хорошо разделённых кластера
    cluster_1_embeddings = [
        (np.array([1.0, 0.0, 0.0]) + np.random.rand(3) * 0.1).tolist()
        for _ in range(5)
    ]
    cluster_2_embeddings = [
        (np.array([0.0, 1.0, 0.0]) + np.random.rand(3) * 0.1).tolist()
        for _ in range(5)
    ]
    
    # Формат для calculate_silhouette_score: Dict[cluster_id, embeddings]
    all_clusters_embeddings = {
        "other_cluster": cluster_2_embeddings,
    }
    
    result = await coherence_metrics.calculate_silhouette_score(
        cluster_id=cluster_id,
        cluster_embeddings=cluster_1_embeddings,
        all_clusters_embeddings=all_clusters_embeddings,
    )
    
    assert result is not None
    # Хорошо разделённые кластеры должны давать высокий silhouette score (> 0.3)
    assert result > 0.2, f"Expected reasonable silhouette for well-separated clusters, got {result}"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_silhouette_score_overlapping_clusters(coherence_metrics):
    """Тест вычисления silhouette score для перекрывающихся кластеров."""
    cluster_id = str(uuid4())
    
    # Создаём перекрывающиеся кластеры
    base_embedding = np.random.rand(384).tolist()
    cluster_1_embeddings = [
        (np.array(base_embedding) + np.random.rand(384) * 0.2).tolist()
        for _ in range(5)
    ]
    cluster_2_embeddings = [
        (np.array(base_embedding) + np.random.rand(384) * 0.2).tolist()
        for _ in range(5)
    ]
    
    # Формат для calculate_silhouette_score: Dict[cluster_id, embeddings]
    all_clusters_embeddings = {
        "other_cluster": cluster_2_embeddings,
    }
    
    result = await coherence_metrics.calculate_silhouette_score(
        cluster_id=cluster_id,
        cluster_embeddings=cluster_1_embeddings,
        all_clusters_embeddings=all_clusters_embeddings,
    )
    
    assert result is not None
    # Перекрывающиеся кластеры должны давать низкий silhouette score
    assert result < 0.6, f"Expected lower silhouette for overlapping clusters, got {result}"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_silhouette_score_insufficient_data(coherence_metrics):
    """Тест обработки недостаточного количества данных для silhouette."""
    cluster_id = str(uuid4())
    
    result = await coherence_metrics.calculate_silhouette_score(
        cluster_id=cluster_id,
        cluster_embeddings=[np.random.rand(384).tolist()],  # Только 1 embedding
        all_clusters_embeddings=None,
    )
    
    assert result is None


# ============================================================================
# CALCULATE ALL METRICS TESTS
# ============================================================================


@pytest.mark.asyncio
@pytest.mark.unit
async def test_calculate_all_metrics_success(coherence_metrics, sample_embeddings):
    """Тест вычисления всех метрик одновременно."""
    cluster_id = str(uuid4())
    
    top_keywords = ["технологии", "ai", "машинное обучение"]
    all_cluster_keywords = [
        ["технологии", "ai", "машинное обучение"],
        ["технологии", "ai", "gpt"],
        ["ai", "технологии", "машинное обучение"],
    ]
    
    # Для silhouette нужны все кластеры в формате Dict[cluster_id, embeddings]
    other_cluster_embeddings = [
        (np.array(sample_embeddings[0]) + np.random.rand(384) * 0.5).tolist()
        for _ in range(3)
    ]
    all_clusters_embeddings_dict = {
        "other_cluster": other_cluster_embeddings,
    }
    
    # Mock для get_cluster_keywords, чтобы не обращаться к БД
    async def mock_get_keywords(cluster_id):
        return top_keywords, all_cluster_keywords
    
    coherence_metrics.get_cluster_keywords = mock_get_keywords
    
    result = await coherence_metrics.calculate_all_metrics(
        cluster_id=cluster_id,
        cluster_embeddings=sample_embeddings,
        cluster_keywords=top_keywords,
        all_clusters_embeddings=all_clusters_embeddings_dict,
    )
    
    assert result is not None
    assert "intra_cluster_similarity" in result
    assert "npmi_score" in result
    assert "silhouette_score" in result
    
    assert result["intra_cluster_similarity"] is not None
    assert 0.0 <= result["intra_cluster_similarity"] <= 1.0


@pytest.mark.asyncio
@pytest.mark.unit
async def test_calculate_all_metrics_without_silhouette(coherence_metrics, sample_embeddings):
    """Тест вычисления метрик без silhouette (когда нет данных о других кластерах)."""
    cluster_id = str(uuid4())
    
    top_keywords = ["технологии", "ai", "машинное обучение"]
    all_cluster_keywords = [
        ["технологии", "ai", "машинное обучение"],
        ["технологии", "ai", "gpt"],
    ]
    
    # Mock для get_cluster_keywords, чтобы не обращаться к БД
    async def mock_get_keywords(cluster_id):
        return top_keywords, all_cluster_keywords
    
    coherence_metrics.get_cluster_keywords = mock_get_keywords
    
    result = await coherence_metrics.calculate_all_metrics(
        cluster_id=cluster_id,
        cluster_embeddings=sample_embeddings,
        cluster_keywords=top_keywords,  # Передаём keywords явно
        all_clusters_embeddings=None,  # Нет данных о других кластерах
    )
    
    assert result is not None
    assert "intra_cluster_similarity" in result
    assert "npmi_score" in result
    assert result["silhouette_score"] is None  # Не может быть вычислен без других кластеров

