"""Shared coding lifecycle regressions; real temporary stores, no models or Docker."""
import asyncio
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest

from app.coding.local_schemas import LocalConversationCreate, LocalCreate
from app.coding.local_service import LOCAL_PROMPT, LOCAL_TOOLS, LocalCodingService


def test_local_service_constructs_without_a_legacy_sandbox(app, monkeypatch):
    def unavailable(*args, **kwargs):
        raise AssertionError("Local coding must not construct the legacy sandbox")

    monkeypatch.setattr('app.coding.service.DockerSandbox', unavailable)
    service = LocalCodingService(app.state.local_coding.store, app.state.model_calls, app.state.settings)
    assert service.model.tool_definitions == LOCAL_TOOLS
    assert service.model_for(1) is service.model


def test_current_app_starts_with_legacy_execution_modules_unavailable(tmp_path):
    # A new interpreter prevents imports from other test modules hiding a dependency.
    script = r'''
import importlib.abc
from pathlib import Path
import sys
from unittest.mock import patch

class NoLegacyExecution(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {'app.coding.service', 'app.coding.sandbox', 'app.coding.prompts'}:
            raise ImportError('Retired execution module unavailable: ' + fullname)
sys.meta_path.insert(0, NoLegacyExecution())

from app.config import Settings
from app.config_groups import EmailCodeConfig
settings = Settings(dashscope_api_key='', qwen_model='offline', qwen_vision_model='',
    qwen_coding_model='', system_prompt='test', cors_origins=('http://localhost:5173',),
    auth_db_path=Path(sys.argv[1]), email_code=EmailCodeConfig(secret='offline-test-secret'))
with patch('app.config.get_settings', return_value=settings):
    from app.main import create_app

from fastapi.testclient import TestClient
with TestClient(create_app(settings)) as client:
    assert client.get('/api/health').status_code == 200
    assert client.get('/api/coding/local/capabilities').status_code == 401
    assert client.get('/api/coding/projects').status_code == 404
'''
    result = subprocess.run([sys.executable, '-X', 'utf8', '-c', script, str(tmp_path / 'isolated.sqlite3')],
                            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
                            encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('action', ['cancel', 'shutdown'])
@pytest.mark.parametrize('source', ['default', 'custom'])
def test_local_lifecycle_keeps_task_and_metering_consistent(logged_in, app, action, source):
    service = app.state.local_coding
    owner = logged_in.get('/api/auth/me').json()['user']['id']
    conversation = service.store.create_conversation(owner, LocalConversationCreate(
        client_conversation_id=uuid4(), directory_name='offline-project'))
    task = service.store.create(owner, LocalCreate(
        instruction='Read the project', directory_name='offline-project', client_task_id=uuid4(),
        conversation_id=conversation['id'], grant_token=uuid4().hex + uuid4().hex, paths=['main.py']),
        app.state.settings, LOCAL_PROMPT)

    async def scenario():
        entered = asyncio.Event()

        class OfflineModel:
            name = 'offline-lifecycle'
            billing_source = source

            async def call(self, messages):
                entered.set()
                await asyncio.Event().wait()

        service.model = OfflineModel()
        service.launch(owner, task['id'])
        worker = service.tasks[task['id']]
        try:
            await asyncio.wait_for(entered.wait(), 3)
            # Repeated acceptance must not launch another paid call for the same turn.
            service.launch(owner, task['id'])
            if action == 'cancel':
                await service.cancel(owner, task['id'])
            else:
                await service.shutdown()
            await asyncio.wait_for(worker, 3)
        finally:
            await service.shutdown()
            await asyncio.sleep(0)
        assert service.tasks == {}

    asyncio.run(scenario())
    assert service.store.get(owner, task['id'])['status'] == ('cancelled' if action == 'cancel' else 'interrupted')
    with app.state.auth_store.connect() as db:
        rows = db.execute('SELECT source, state FROM model_calls WHERE user_id = ?', (owner,)).fetchall()
    assert [(row['source'], row['state']) for row in rows] == [(source, 'cancelled')]
    usage = app.state.model_calls.usage(owner, app.state.settings)
    assert usage['requests'] == (1 if source == 'default' else 0)
    assert usage['custom']['requests'] == (1 if source == 'custom' else 0)
