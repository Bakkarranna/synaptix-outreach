# Bug: Daemon hangs indefinitely on stuck browser operations

## Problem

The daemon can hang forever if a task handler blocks. Task handlers have no timeout, so if a browser operation hangs (e.g., waiting for a DOM element that never appears), the entire daemon blocks and stops processing all tasks.

The hang occurs in:
- `linkedin/tasks/check_pending.py:42` calls `get_connection_status()`
- `linkedin/actions/status.py:27-28` calls `search_profile()` or `session.wait()`
- DOM operations like `find_top_card()` or `locator().count()` block indefinitely

Once a handler hangs, the task stays in `RUNNING` status and the daemon main loop (daemon.py:194-233) is blocked at line 222 (`handler(task, session, qualifiers)`) forever. No new tasks are processed.

## DB State When Hung

Running this query shows stuck tasks:

```python
from linkedin.models import Task
stuck = Task.objects.filter(status__in=['pending', 'running']).order_by('created_at')
print(f"Stuck tasks: {stuck.count()}")
for t in stuck[:20]:
    print(f"id={t.id} task_type={t.task_type} status={t.status} scheduled={t.scheduled_at}")
```

Example output from actual hang:
```
Stuck tasks: 58

id=3 task_type=check_pending status=pending scheduled=2026-03-18 22:21:55.129450+00:00
id=5 task_type=check_pending status=pending scheduled=2026-03-18 22:40:05.477191+00:00
...
```

All 58 tasks are `check_pending` type with `scheduled_at` in the past, indicating they were created but never processed because the daemon was blocked on an earlier task handler.

## Stack Trace at Exit

When daemon was interrupted with Ctrl+C:

```
^CTraceback (most recent call last):
  File "/home/eracle/git/linkedin/manage.py", line 95, in <module>
    _run_daemon()
  File "/home/eracle/git/linkedin/manage.py", line 82, in _run_daemon
    run_daemon(session)
  File "/home/eracle/git/linkedin/linkedin/daemon.py", line 197, in run_daemon
    time.sleep(cfg["worker_poll_seconds"])
KeyboardInterrupt
```

Shows daemon was waiting in the polling sleep (line 197), which means the main loop was stuck somewhere else and never reached this point normally. The 58 pending tasks never got picked up by `_pop_next_task()` because the daemon was blocked earlier.
