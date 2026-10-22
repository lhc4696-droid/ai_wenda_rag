"""不依赖 Agent 的单路密集向量 RAG，限定当前登录用户和当前会话。"""

import asyncio
from dataclasses import dataclass
from http import HTTPStatus
import logging
from uuid import NAMESPACE_URL, uuid5, uuid4

import aiohttp

from app.conversations.store import HistoryError
from app.rag.chunks import split_text
from app.chat.accounting import track_call
from app.chat.store import ModelCallStore


logger = logging.getLogger(__name__)


def condition(key, value):
    return {"key": key, "match": {"value": value}}


class RagError(HistoryError):
    def __init__(self, message, code="RAG_UNAVAILABLE", status=503):
        super().__init__(message, code, status)


@dataclass(frozen=True)
class EmbeddingBatch:
    vectors: list[list[float]]
    input_tokens: int | None = None


class DashScopeEmbedding:
    def __init__(self, settings):
        self.settings = settings

    async def embed(self, texts, text_type):
        if not self.settings.dashscope_api_key:
            raise RagError("尚未配置百炼 API Key，无法使用资料检索", "RAG_EMBEDDING_NOT_CONFIGURED")

        def call():
            from dashscope import TextEmbedding
            return TextEmbedding.call(
                model=self.settings.embedding_model,
                input=texts,
                text_type=text_type,
                dimension=self.settings.embedding_dimension,
                api_key=self.settings.dashscope_api_key,
                timeout=30,
            )

        try:
            response = await asyncio.wait_for(asyncio.to_thread(call), 30)
            if response.status_code != HTTPStatus.OK:
                logger.warning("rag_embedding_rejected status=%s", response.status_code)
                raise RagError("向量模型调用失败，请核对模型名称、地域及账号权限", "RAG_EMBEDDING_FAILED")
            items = response.output["embeddings"]
            vectors = [None] * len(texts)
            for item in items:
                index, vector = item["text_index"], item["embedding"]
                if not isinstance(index, int) or not 0 <= index < len(texts) or len(vector) != self.settings.embedding_dimension:
                    raise ValueError("invalid embedding shape")
                vectors[index] = vector
            if any(vector is None for vector in vectors):
                raise ValueError("incomplete embeddings")
            usage = response.usage
            tokens = usage.get("total_tokens") if isinstance(usage, dict) else None
            if type(tokens) is not int or not 0 <= tokens <= 1_000_000_000:
                tokens = None
            return EmbeddingBatch(vectors, tokens)
        except RagError:
            raise
        except Exception as exc:
            logger.warning("rag_embedding_error category=%s", type(exc).__name__)
            raise RagError("向量模型暂时不可用，请稍后重试", "RAG_EMBEDDING_FAILED") from None


class QdrantStore:
    def __init__(self, settings):
        self.settings = settings
        self.root = f"/collections/{settings.qdrant_collection}"

    async def request(self, method, path, data=None, missing_ok=False):
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12), trust_env=False) as session:
                async with session.request(method, self.settings.qdrant_url + path, json=data) as response:
                    if missing_ok and response.status == 404:
                        return None
                    if response.status >= 400:
                        logger.warning("rag_qdrant_rejected status=%s", response.status)
                        raise RagError("向量数据库请求失败，请检查 Qdrant 配置和运行状态", "RAG_QDRANT_FAILED")
                    body = await response.json()
                    if body.get("status") != "ok":
                        raise RagError("向量数据库未确认操作成功", "RAG_QDRANT_FAILED")
                    return body.get("result")
        except RagError:
            raise
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            logger.warning("rag_qdrant_error category=%s", type(exc).__name__)
            raise RagError("无法连接本地 Qdrant，请确认 Docker 和容器已启动", "RAG_QDRANT_UNAVAILABLE") from None

    async def ensure_collection(self):
        info = await self.request("GET", self.root, missing_ok=True)
        if info is None:
            try:
                await self.request("PUT", self.root, {"vectors": {"size": self.settings.embedding_dimension, "distance": "Cosine"}})
            except RagError:
                # 并发的第一条检索可能已创建集合；重新读取后再校验。
                if await self.request("GET", self.root, missing_ok=True) is None:
                    raise
            info = await self.request("GET", self.root)
        vectors = info.get("config", {}).get("params", {}).get("vectors", {})
        if vectors.get("size") != self.settings.embedding_dimension:
            raise RagError("Qdrant 集合维度与 EMBEDDING_DIMENSION 不一致，请更换集合名并重建索引", "RAG_DIMENSION_MISMATCH")
        if vectors.get("distance") != "Cosine":
            raise RagError("Qdrant 集合的距离算法不是 Cosine，请更换集合名并重建索引", "RAG_DISTANCE_MISMATCH")

    async def count(self, owner, conversation, attachment):
        result = await self.request("POST", self.root + "/points/count", {
            "filter": {"must": [condition("user_id", owner), condition("conversation_id", conversation), condition("attachment_id", attachment),
                                  condition("embedding_model", self.settings.embedding_model)]},
            "exact": True,
        })
        return result["count"]

    async def delete_attachment(self, owner, attachment):
        if await self.request("GET", self.root, missing_ok=True) is None:
            return
        await self.request("POST", self.root + "/points/delete?wait=true", {
            "filter": {"must": [condition("user_id", owner), condition("attachment_id", attachment)]},
        })

    async def upsert(self, owner, conversation, attachment, filename, chunks, vectors):
        points = []
        for index, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True)):
            points.append({
                "id": str(uuid5(NAMESPACE_URL, f"{self.settings.qdrant_collection}:{attachment}:{index}")),
                "vector": vector,
                "payload": {"user_id": owner, "conversation_id": conversation, "attachment_id": attachment,
                            "embedding_model": self.settings.embedding_model,
                            "filename": filename, "chunk_index": index, "page": chunk["page"],
                            "heading": chunk["heading"], "text": chunk["text"]},
            })
        for start in range(0, len(points), 10):
            await self.request("PUT", self.root + "/points?wait=true", {"points": points[start:start + 10]})

    async def search(self, owner, conversation, attachment_ids, vector):
        result = await self.request("POST", self.root + "/points/query", {
            "query": vector,
            "filter": {"must": [condition("user_id", owner), condition("conversation_id", conversation),
                                  condition("embedding_model", self.settings.embedding_model),
                                  {"key": "attachment_id", "match": {"any": attachment_ids}}]},
            "limit": 5,
            "with_payload": True,
        })
        return result.get("points", [])


class RagService:
    def __init__(self, auth, settings, embedding=None, vectors=None):
        self.auth, self.settings = auth, settings
        self.embedding = embedding or DashScopeEmbedding(settings)
        self.vectors = vectors or QdrantStore(settings)
        self.calls = ModelCallStore(auth)

    async def _embed(self, user_id, texts, text_type):
        # Each actual batch consumes the server's embedding allowance, including
        # failed calls. Cache hits consume none; the browser cannot choose source.
        async with track_call(self.calls, uuid4().hex, user_id, sum(map(len, texts)),
                              self.settings, self.settings.embedding_model, "embedding", 60) as recorded:
            result = await self.embedding.embed(texts, text_type)
            recorded.input_tokens = result.input_tokens
            return result.vectors

    def documents(self, user_id, conversation_id):
        with self.auth.connect() as db:
            rows = db.execute("""SELECT id, filename, extracted_text FROM attachments
                WHERE user_id = ? AND conversation_id = ? AND kind = 'document' AND status = 'ready'
                ORDER BY created_at DESC, id DESC""", (user_id, conversation_id)).fetchall()
        return [dict(row) for row in rows]

    def has_documents(self, user_id, conversation_id, attachment_ids):
        with self.auth.connect() as db:
            if db.execute("SELECT 1 FROM conversations WHERE id = ? AND user_id = ?", (conversation_id, user_id)).fetchone() is None:
                raise HistoryError("会话不存在或无法访问", "CONVERSATION_NOT_FOUND", 404)
        if self.documents(user_id, conversation_id):
            return True
        if not attachment_ids:
            return False
        with self.auth.connect() as db:
            for identifier in attachment_ids:
                row = db.execute("""SELECT 1 FROM attachments WHERE id = ? AND user_id = ? AND kind = 'document'
                    AND status = 'ready' AND (conversation_id IS NULL OR conversation_id = ?)""",
                    (identifier.hex, user_id, conversation_id)).fetchone()
                if row:
                    return True
        return False

    async def retrieve(self, user_id, conversation_id, question):
        documents = await asyncio.to_thread(self.documents, user_id, conversation_id)
        if not documents:
            raise RagError("当前会话没有可检索的文字文档，请先上传文件", "RAG_NO_DOCUMENTS", 409)
        if len(documents) > 10:
            raise RagError("当前会话的文档超过第一版的 10 份检索上限，请在新会话中缩小资料范围", "RAG_TOO_MANY_DOCUMENTS", 422)
        try:
            async with asyncio.timeout(75):
                await self.vectors.ensure_collection()
                for document in documents:
                    chunks = split_text(document["extracted_text"])
                    if not chunks:
                        continue
                    count = await self.vectors.count(user_id, conversation_id, document["id"])
                    if count != len(chunks):
                        await self.vectors.delete_attachment(user_id, document["id"])
                        vectors = []
                        for start in range(0, len(chunks), 10):
                            vectors.extend(await self._embed(user_id, [chunk["text"] for chunk in chunks[start:start + 10]], "document"))
                        await self.vectors.upsert(user_id, conversation_id, document["id"], document["filename"], chunks, vectors)
                query = (await self._embed(user_id, [question[:3000]], "query"))[0]
                hits = await self.vectors.search(user_id, conversation_id, [item["id"] for item in documents], query)
        except TimeoutError:
            raise RagError("资料索引或检索超时，请稍后重试", "RAG_TIMEOUT", 504) from None
        allowed = {item["id"] for item in documents}
        sources = []
        for hit in hits:
            payload = hit.get("payload") or {}
            if payload.get("attachment_id") not in allowed or payload.get("user_id") != user_id or payload.get("conversation_id") != conversation_id:
                continue
            text = payload.get("text")
            if not isinstance(text, str) or not text:
                continue
            sources.append({"attachment_id": payload["attachment_id"], "filename": payload.get("filename", "文档"),
                            "page": payload.get("page"), "snippet": text[:350], "text": text,
                            "score": round(float(hit.get("score", 0)), 4)})
        return sources
