from functools import lru_cache

from app.core.config import get_settings


@lru_cache(maxsize=2)
def _model(name: str):
    from fastembed import TextEmbedding

    settings = get_settings()
    return TextEmbedding(
        model_name=name,
        cache_dir=str(settings.model_cache_dir),
        threads=settings.embedding_threads,
    )


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    model = _model(get_settings().embedding_model)
    vectors = model.embed(texts, batch_size=get_settings().embedding_batch_size)
    result = [vector.tolist() for vector in vectors]
    if any(len(vector) != 512 for vector in result):
        raise ValueError("Embedding 维度不是 512；更换模型需迁移并重建索引")
    return result
