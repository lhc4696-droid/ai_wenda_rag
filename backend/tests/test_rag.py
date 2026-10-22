import asyncio
from dataclasses import replace
from uuid import uuid4
import pytest

from app.main import get_llm_provider
from app.rag.chunks import split_text
from app.rag.service import EmbeddingBatch


class FakeEmbedding:
    def __init__(self):
        self.calls = []

    async def embed(self, texts, text_type):
        self.calls.append((list(texts), text_type))
        return EmbeddingBatch([[0.1] * 1024 for _ in texts])


class FakeVectors:
    def __init__(self):
        self.items = {}

    async def ensure_collection(self):
        return None

    async def count(self, owner, conversation, attachment):
        return len([item for item in self.items.values() if item['user_id'] == owner and item['conversation_id'] == conversation and item['attachment_id'] == attachment])

    async def delete_attachment(self, owner, attachment):
        self.items = {key: item for key, item in self.items.items() if not (item['user_id'] == owner and item['attachment_id'] == attachment)}

    async def upsert(self, owner, conversation, attachment, filename, chunks, vectors):
        for index, chunk in enumerate(chunks):
            self.items[(attachment, index)] = {"user_id": owner, "conversation_id": conversation,
                "attachment_id": attachment, "filename": filename, "page": chunk['page'], "text": chunk['text']}

    async def search(self, owner, conversation, attachment_ids, vector):
        return [{"score": 0.9, "payload": item} for item in self.items.values()
                if item['user_id'] == owner and item['conversation_id'] == conversation and item['attachment_id'] in attachment_ids][:5]


class Model:
    def __init__(self):
        self.calls = []

    async def chat(self, messages):
        self.calls.append(messages)
        return "这是根据资料得到的回答 [1]。"


def question(content, sequence=0, **extra):
    return {"content": content, "client_message_id": uuid4().hex, "expected_last_sequence": sequence, **extra}


def test_chunking_keeps_page_and_section_boundary():
    chunks = split_text("[第 1 页]\n# 背景\n旧内容。\n# 正文\n新内容。\n[第 2 页]\n第二页内容。", size=30, overlap=5)
    assert [item['page'] for item in chunks] == [1, 1, 2]
    assert '旧内容' in chunks[0]['text'] and '新内容' not in chunks[0]['text']
    assert '新内容' in chunks[1]['text'] and '旧内容' not in chunks[1]['text']
    assert '第二页内容' in chunks[2]['text']
    assert split_text('[第 1 页]\n（本页没有可提取文字，未进行图片识别）') == []
    assert split_text('背景：这是有用正文。')[0]['text'] == '背景：这是有用正文。'


def test_rag_documents_and_plain_chat_are_separate(logged_in, app):
    settings = replace(app.state.settings, rag=replace(app.state.settings.rag, enabled=True), chat=replace(app.state.settings.chat, timeout=5))
    app.state.conversation_service.settings = settings
    app.state.conversation_service.chat.settings = settings
    app.state.rag.settings = settings
    app.state.attachments.settings = settings
    embedding, vectors, model = FakeEmbedding(), FakeVectors(), Model()
    app.state.rag.embedding = embedding
    app.state.rag.vectors = vectors
    app.dependency_overrides[get_llm_provider] = lambda: model

    upload = logged_in.post('/api/attachments', files={"file": ('notes.txt', '课程目标是理解向量检索。'.encode(), 'text/plain')})
    assert upload.status_code == 201 and upload.json()['status'] == 'ready'
    attachment = upload.json()['id']
    conversation = logged_in.post('/api/conversations', json={}).json()['id']
    endpoint = f'/api/conversations/{conversation}/messages'
    first = question('课程目标是什么？', attachment_ids=[attachment], source_mode='documents')
    response = logged_in.post(endpoint, json=first)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['assistant_message']['sources'][0]['attachment_id'] == attachment
    assert '课程目标' in model.calls[-1][-1].content
    assert len(embedding.calls) == 2
    assert logged_in.post(endpoint, json=first).json()['replayed'] is True
    assert len(embedding.calls) == 2
    assert logged_in.post(endpoint, json={**first, 'source_mode': 'chat'}).status_code == 409

    next_response = logged_in.post(endpoint, json=question('帮我写首诗', 2, source_mode='chat'))
    assert next_response.status_code == 200, next_response.text
    assert '课程目标是理解向量检索' not in model.calls[-1][-1].content
    assert len(embedding.calls) == 2
    assert logged_in.get(endpoint).json()['messages'][1]['sources'][0]['attachment_id'] == attachment


def test_documents_mode_without_file_does_not_save_question(logged_in, app):
    app.state.conversation_service.settings = replace(app.state.settings, rag=replace(app.state.settings.rag, enabled=True))
    conversation = logged_in.post('/api/conversations', json={}).json()['id']
    endpoint = f'/api/conversations/{conversation}/messages'
    result = logged_in.post(endpoint, json=question('根据文档回答', source_mode='documents'))
    assert result.status_code == 409 and result.headers['x-error-code'] == 'RAG_NO_DOCUMENTS'
    assert logged_in.get(endpoint).json()['messages'] == []


def metered_rag(app, logged_in, *, custom=False, embedding_limit=100):
    settings = replace(app.state.settings,
                       chat=replace(app.state.settings.chat, requests_per_day=1),
                       rag=replace(app.state.settings.rag, enabled=True, requests_per_day=embedding_limit))
    app.state.settings = settings
    app.state.conversation_service.settings = settings
    app.state.conversation_service.chat.settings = settings
    app.state.rag.settings = settings
    app.state.attachments.settings = settings
    embedding, model = FakeEmbedding(), Model()
    model.billing_source = 'custom' if custom else 'default'
    app.state.rag.embedding, app.state.rag.vectors = embedding, FakeVectors()
    app.dependency_overrides[get_llm_provider] = lambda: model
    owner = logged_in.get('/api/auth/me').json()['user']['id']
    attachment = logged_in.post('/api/attachments', files={'file': ('notes.txt', b'course material', 'text/plain')}).json()['id']
    conversation = logged_in.post('/api/conversations', json={}).json()['id']
    data = question('课程是什么', attachment_ids=[attachment], source_mode='documents')
    return owner, conversation, data, embedding, model


@pytest.mark.parametrize('stream', [False, True])
def test_default_exhaustion_rejects_documents_before_any_embedding(app, logged_in, stream):
    owner, conversation, data, embedding, model = metered_rag(app, logged_in)
    app.state.model_calls.reserve('used', owner, 1, app.state.settings)
    app.state.model_calls.finish('used', 'succeeded', 1)
    suffix = '/stream' if stream else ''
    result = logged_in.post(f'/api/conversations/{conversation}/messages{suffix}', json=data)
    assert 'CHAT_DAILY_LIMIT' in (result.headers.get('x-error-code', '') + result.text)
    assert embedding.calls == []
    assert model.calls == []


def test_personal_rag_uses_separate_server_embedding_allowance(app, logged_in):
    owner, conversation, data, embedding, model = metered_rag(app, logged_in, custom=True)
    app.state.model_calls.reserve('used', owner, 1, app.state.settings)
    app.state.model_calls.finish('used', 'succeeded', 1)
    result = logged_in.post(f'/api/conversations/{conversation}/messages', json=data)
    assert result.status_code == 200, result.text
    usage = logged_in.get('/api/chat/usage').json()
    assert usage['requests'] == 1 and usage['custom']['requests'] == 1
    assert usage['embedding']['requests'] == usage['embedding']['succeeded'] == 2
    assert len(embedding.calls) == 2 and len(model.calls) == 1


def test_embedding_exhaustion_blocks_next_paid_batch_and_releases_chat_slot(app, logged_in):
    _, conversation, data, embedding, model = metered_rag(app, logged_in, custom=True, embedding_limit=1)
    result = logged_in.post(f'/api/conversations/{conversation}/messages', json=data)
    assert result.status_code == 429, result.text
    assert result.headers['x-error-code'] == 'RAG_DAILY_LIMIT'
    assert len(embedding.calls) == 1 and model.calls == []
    with app.state.auth_store.connect() as db:
        assert db.execute("SELECT count(*) FROM model_calls WHERE state='running'").fetchone()[0] == 0


def test_cancel_during_embedding_releases_parent_and_child_leases(app, logged_in):
    from app.conversations.schemas import SendMessage
    owner, conversation, data, _, model = metered_rag(app, logged_in, custom=True)
    async def scenario():
        entered = asyncio.Event()
        class WaitingEmbedding:
            async def embed(self, *args):
                entered.set()
                await asyncio.Event().wait()
        app.state.rag.embedding = WaitingEmbedding()
        task = asyncio.create_task(app.state.conversation_service.send(
            owner, conversation, SendMessage(**data), 'cancel-rag', model))
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(scenario())
    with app.state.auth_store.connect() as db:
        rows = db.execute('SELECT source,state FROM model_calls ORDER BY source').fetchall()
    assert [tuple(row) for row in rows] == [('custom', 'cancelled'), ('embedding', 'cancelled')]


def test_server_embedding_reports_known_tokens_from_real_adapter(app, logged_in, monkeypatch):
    from dashscope.api_entities.dashscope_response import DashScopeAPIResponse
    from app.rag.service import DashScopeEmbedding
    _, conversation, data, _, _ = metered_rag(app, logged_in, custom=True)
    settings = replace(app.state.settings, dashscope_api_key='offline-fake-key')
    def response(**kwargs):
        assert kwargs['timeout'] == 30
        return DashScopeAPIResponse(status_code=200, request_id='offline', output={
            'embeddings': [{'text_index': i, 'embedding': [0.1] * 1024} for i, _ in enumerate(kwargs['input'])],
        }, usage={'total_tokens': 7})
    monkeypatch.setattr('dashscope.TextEmbedding.call', response)
    app.state.rag.embedding = DashScopeEmbedding(settings)
    result = logged_in.post(f'/api/conversations/{conversation}/messages', json=data)
    assert result.status_code == 200, result.text
    usage = logged_in.get('/api/chat/usage').json()['embedding']
    assert usage['requests'] == 2 and usage['input_tokens'] == 14
