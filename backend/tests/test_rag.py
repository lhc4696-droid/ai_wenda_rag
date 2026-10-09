import asyncio
from dataclasses import replace
from uuid import uuid4

from app.main import get_llm_provider
from app.rag.chunks import split_text


class FakeEmbedding:
    def __init__(self):
        self.calls = []

    async def embed(self, texts, text_type):
        self.calls.append((list(texts), text_type))
        return [[0.1] * 1024 for _ in texts]


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
    settings = replace(app.state.settings, rag_enabled=True, chat_timeout=5)
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
    app.state.conversation_service.settings = replace(app.state.settings, rag_enabled=True)
    conversation = logged_in.post('/api/conversations', json={}).json()['id']
    endpoint = f'/api/conversations/{conversation}/messages'
    result = logged_in.post(endpoint, json=question('根据文档回答', source_mode='documents'))
    assert result.status_code == 409 and result.headers['x-error-code'] == 'RAG_NO_DOCUMENTS'
    assert logged_in.get(endpoint).json()['messages'] == []
