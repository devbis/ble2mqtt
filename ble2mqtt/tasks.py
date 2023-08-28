import asyncio as aio
import logging
import typing as ty

_LOGGER = logging.getLogger(__name__)


async def run_tasks_and_cancel_on_first_return(*tasks: aio.Future,
                                               return_when=aio.FIRST_COMPLETED,
                                               ignore_futures=(),
                                               ) -> ty.Sequence[aio.Future]:
    async def cancel_tasks(_tasks) -> ty.List[aio.Task]:
        # cancel first, then await. Because other tasks can raise exceptions
        # while switching tasks
        canceled = []
        for t in _tasks:
            if t in ignore_futures:
                continue
            if not t.done():
                t.cancel()
                canceled.append(t)
        tasks_raise_exceptions = []
        for t in canceled:
            try:
                await t
            except aio.CancelledError:
                pass
            except Exception:
                _LOGGER.exception(
                    f'Unexpected exception while cancelling tasks! {t}',
                )
                tasks_raise_exceptions.append(t)
        return tasks_raise_exceptions

    assert all(isinstance(t, aio.Future) for t in tasks)
    try:
        # NB: pending tasks can still raise exception or finish
        # while tasks are switching
        done, pending = await aio.wait(tasks, return_when=return_when)
    except aio.CancelledError:
        await cancel_tasks(tasks)
        # it could happen that tasks raised exception and canceling wait task
        # abandons tasks with exception
        for t in tasks:
            if not t.done() or t.cancelled():
                continue
            try:
                t.result()
            # no CancelledError expected
            except Exception:
                _LOGGER.exception(
                    f'Task raises exception while cancelling parent coroutine '
                    f'that waits for it {t}')
        raise

    # while switching tasks for await other pending tasks can raise an exception
    # we need to append more tasks to the result if so
    await cancel_tasks(pending)

    task_remains = [t for t in pending if not t.cancelled()]
    return [*done, *task_remains]


async def handle_returned_tasks(*tasks: aio.Future):
    raised = [t for t in tasks if t.done() and t.exception()]
    returned_normally = set(tasks) - set(raised)

    results = []

    if raised:
        task_for_raise = raised.pop()
        for t in raised:
            try:
                await t
            except aio.CancelledError:
                raise
            except Exception:
                _LOGGER.exception('Task raised an error')
        await task_for_raise
    for t in returned_normally:
        results.append(await t)
    return results
