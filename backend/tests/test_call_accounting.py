import asyncio
import threading

import pytest

from app.chat.accounting import track_call
from tests.test_model_quotas import meter


@pytest.mark.parametrize('phase', ['reserve', 'finish'])
def test_repeated_cancellation_drains_accounting_write(meter, monkeypatch, phase):
    store, owner, settings = meter
    entered, release = threading.Event(), threading.Event()
    original = getattr(store, phase)
    def delayed(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return original(*args, **kwargs)
    monkeypatch.setattr(store, phase, delayed)
    async def scenario():
        async def run():
            async with track_call(store, 'double-cancel', owner, 1, settings, 'model', 'custom'):
                pass
        task = asyncio.create_task(run())
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(scenario())
    with store.auth.connect() as db:
        row = db.execute("SELECT state FROM model_calls WHERE request_id='double-cancel'").fetchone()
    assert row is not None
    assert row['state'] == ('cancelled' if phase == 'reserve' else 'succeeded')
