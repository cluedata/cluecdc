import asyncio
import contextlib
import signal

from app.core.database import engine
from app.core.logging import configure_logging
from app.worker_health import HEARTBEAT
from app.workers.worker import worker_loop


async def main() -> None:
    configure_logging("cluecdc-worker")
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for event in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(event, stop.set)

    async def heartbeat():
        while True:
            HEARTBEAT.touch()
            await asyncio.sleep(5)

    task = asyncio.create_task(worker_loop())
    health = asyncio.create_task(heartbeat())
    stopping = asyncio.create_task(stop.wait())
    try:
        done, _ = await asyncio.wait((task, health, stopping), return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            await task
            raise RuntimeError("Worker loop stopped unexpectedly")
        if health in done:
            await health
            raise RuntimeError("Worker heartbeat stopped unexpectedly")
    finally:
        task.cancel()
        health.cancel()
        stopping.cancel()
        await asyncio.gather(task, health, stopping, return_exceptions=True)
        HEARTBEAT.unlink(missing_ok=True)
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
