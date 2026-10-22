"""User-run integration regressions; all external model calls are replaced."""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock
from uuid import uuid4

from cryptography.fernet import Fernet
import pytest

from app.coding import compatible_model
from app.coding.local_schemas import LocalConversationCreate, LocalCreate
from app.coding.local_service import LOCAL_PROMPT
from app.integrations import compatible
from app.main import get_llm_provider
from app.model_settings.store import ModelSettingsStore


@pytest.fixture
def personal_models(app, logged_in):
    settings = replace(app.state.settings, model_config_secret=Fernet.generate_key().decode())
    store = ModelSettingsStore(app.state.auth_store, settings)
    app.state.model_settings = store
    app.state.local_coding.model_settings = store
    app.state.attachments.model_settings = store
    owner = logged_in.get('/api/auth/me').json()['user']['id']
    store.update(owner, {
        'enabled': True, 'base_url': 'https://models.example.com/v1',
        'api_key': 'fake-test-key-never-use-live', 'chat_model': 'personal-chat',
        'coding_model': 'personal-code', 'vision_model': '',
    })
    return store, owner


def completion(content):
    return {'choices': [{'message': {'role': 'assistant', 'content': content}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 5}}


def test_chat_routes_use_personal_model_and_record_actual_model(personal_models, logged_in, app, monkeypatch):
    _, owner = personal_models
    app.dependency_overrides.pop(get_llm_provider)
    sender = AsyncMock(return_value=completion('自定义回答'))
    monkeypatch.setattr(compatible, 'post_completion', sender)
    embedding_model = app.state.rag.settings.embedding_model
    legacy = logged_in.post('/api/chat', json={'messages': [{'role': 'user', 'content': '你好'}]})
    assert legacy.status_code == 200, legacy.text
    assert legacy.json()['model'] == 'personal-chat'
    identifier = logged_in.post('/api/conversations', json={'id': uuid4().hex}).json()['id']
    response = logged_in.post(f'/api/conversations/{identifier}/messages', json={
        'content': '继续聊天', 'client_message_id': uuid4().hex,
        'expected_last_sequence': 0, 'source_mode': 'chat',
    })
    assert response.status_code == 200, response.text
    assert response.json()['assistant_message']['model'] == 'personal-chat'
    for call in sender.call_args_list:
        endpoint, key, payload, _ = call.args
        assert endpoint == 'https://models.example.com/v1'
        assert key == 'fake-test-key-never-use-live'
        assert payload['model'] == 'personal-chat'
    with app.state.auth_store.connect() as db:
        assert {row[0] for row in db.execute('SELECT model FROM model_calls WHERE user_id = ?', (owner,))} == {'personal-chat'}
    assert app.state.rag.settings.embedding_model == embedding_model
    assert app.state.settings.dashscope_api_key == ''


def test_coding_worker_uses_own_provider_without_mutating_default(personal_models, app, monkeypatch):
    store, owner = personal_models
    service = app.state.local_coding
    default_model = service.model
    sender = AsyncMock(return_value=completion('已阅读需求，本轮不需要修改文件。'))
    monkeypatch.setattr(compatible_model, 'post_completion', sender)
    conversation = service.store.create_conversation(owner, LocalConversationCreate(
        client_conversation_id=uuid4(), directory_name='example',
    ))
    task = service.store.create(owner, LocalCreate(
        instruction='解释项目结构', directory_name='example', client_task_id=uuid4(),
        conversation_id=conversation['id'], grant_token='test-grant-' + uuid4().hex,
        paths=['main.py'],
    ), app.state.settings, LOCAL_PROMPT)
    asyncio.run(service._run(owner, task['id']))
    assert service.store.get(owner, task['id'])['status'] == 'completed'
    assert sender.call_args.args[2]['model'] == 'personal-code'
    assert service.model is default_model
    assert service.model_for(owner + 1000) is default_model
    store.update(owner, {'enabled': False})
    assert service.model_for(owner) is default_model


def test_task_creation_allows_personal_key_with_no_server_key(personal_models, logged_in, app, monkeypatch):
    assert not app.state.settings.dashscope_api_key
    monkeypatch.setattr(app.state.local_coding, 'launch', lambda *args: None)
    conversation = logged_in.post('/api/coding/local/conversations', json={
        'client_conversation_id': uuid4().hex, 'directory_name': 'example',
    })
    assert conversation.status_code == 201, conversation.text
    response = logged_in.post('/api/coding/local/tasks', json={
        'instruction': '解释代码', 'directory_name': 'example',
        'client_task_id': uuid4().hex, 'conversation_id': conversation.json()['id'],
        'grant_token': 'test-grant-' + uuid4().hex, 'paths': ['main.py'],
    })
    assert response.status_code == 202, response.text
    assert 'fake-test-key' not in response.text
    assert logged_in.patch('/api/model-settings', json={'enabled': False}).status_code == 409


def test_capabilities_follow_account_and_default_recovery(personal_models, logged_in, app):
    store, owner = personal_models
    attachments = logged_in.get('/api/attachments/capabilities').json()
    assert attachments['chat_model'] == 'personal-chat'
    assert attachments['images_enabled'] is False
    assert attachments['model_source'] == 'custom'
    assert logged_in.get('/api/coding/local/capabilities').json()['model'] == 'personal-code'
    store.update(owner, {'vision_model': 'personal-vision'})
    assert logged_in.get('/api/attachments/capabilities').json()['vision_model'] == 'personal-vision'
    store.update(owner, {'enabled': False})
    defaults = logged_in.get('/api/attachments/capabilities').json()
    assert defaults['chat_model'] == app.state.settings.qwen_model
    assert defaults['model_source'] == 'default'


@pytest.mark.parametrize('route', ['legacy', 'history', 'stream'])
def test_personal_chat_works_after_default_allowance_is_exhausted(personal_models, logged_in, app, monkeypatch, route):
    _, owner = personal_models
    settings = replace(app.state.settings, chat=replace(app.state.settings.chat, requests_per_day=1, global_requests_per_day=1))
    app.state.settings = settings
    app.state.conversation_service.chat.settings = settings
    app.state.model_calls.reserve('used-default', owner, 1, settings)
    app.state.model_calls.finish('used-default', 'succeeded', 1)
    app.dependency_overrides.pop(get_llm_provider)
    monkeypatch.setattr(compatible, 'post_completion', AsyncMock(return_value=completion('个人模型回答')))
    async def stream(*args, **kwargs):
        yield {'choices': [{'delta': {'content': '个人模型回答'}, 'finish_reason': 'stop'}]}
    monkeypatch.setattr('app.integrations.compatible_transport.post_completion_stream', stream)
    if route == 'legacy':
        result = logged_in.post('/api/chat', json={'messages': [{'role': 'user', 'content': '问题'}]})
        assert result.status_code == 200, result.text
    else:
        conversation = logged_in.post('/api/conversations', json={}).json()['id']
        suffix = '/stream' if route == 'stream' else ''
        result = logged_in.post(f'/api/conversations/{conversation}/messages{suffix}', json={
            'content': '问题', 'client_message_id': uuid4().hex, 'expected_last_sequence': 0, 'source_mode': 'chat',
        })
        assert result.status_code == 200, result.text
        assert '个人模型回答' in result.text
    usage = logged_in.get('/api/chat/usage').json()
    assert usage['requests'] == 1
    assert usage['custom']['requests'] == 1


def test_personal_coding_does_not_spend_default_allowance(personal_models, app, monkeypatch):
    _, owner = personal_models
    service = app.state.local_coding
    service.settings = replace(app.state.settings, chat=replace(app.state.settings.chat, requests_per_day=1))
    app.state.model_calls.reserve('used-default', owner, 1, service.settings)
    app.state.model_calls.finish('used-default', 'succeeded', 1)
    monkeypatch.setattr(compatible_model, 'post_completion', AsyncMock(return_value=completion('任务完成')))
    reply = asyncio.run(service._model_call(owner, [{'role': 'user', 'content': '说明代码'}]))
    assert reply.content == '任务完成'
    usage = app.state.model_calls.usage(owner, service.settings)
    assert usage['requests'] == 1 and usage['custom']['requests'] == 1
