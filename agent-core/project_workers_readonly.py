"""Two explicitly bound read-only project summaries; no scheduler or tool execution.

The caller owns the trusted project roots, session authorization and confirmation UI.
Confirmations are invoked synchronously on the calling thread in input order; each
worker waits for its own actual confirmed-client preview and its own answer.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from queue import Empty, Queue
from threading import Event
from typing import Callable
import os

from llm_client import LLMClient
from project_worker_readonly import prepare_project_summary, execute_project_summary


@dataclass(frozen=True, slots=True)
class ProjectWorkerSpec:
    project_id: str
    project_root: Path
    files: tuple[str, ...]
    session_id: str = field(repr=False)
    client: LLMClient = field(repr=False, compare=False)
    host_binding_check: Callable[[], bool] = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class ProjectWorkerOutcome:
    project_id: str
    files: tuple[str, ...]
    status: str
    summary: str = field(default='', repr=False)
    reason: str = ''
    is_acceptance: bool = False


def run_two_project_summaries(specs: list[ProjectWorkerSpec],
                              confirm_fn: Callable[[str, str, bool], str], *,
                              cancel_event: Event | None = None) -> tuple[ProjectWorkerOutcome, ...]:
    """Run two explicitly authorized scopes. No retries and no implicit approval.

    confirm_fn(project_id, exact_preview, False) runs on this calling thread. The
    host must show the complete preview and return exact 'y' only on approval.
    A refusal/exception/cancellation stops further confirmations; a previously
    approved request may already be in flight. An incomplete outcome does not
    establish that its request was not sent. Blocking confirmation is host-owned.
    Binding checks run on worker threads as well as the caller and must be thread-safe.
    The distinct session IDs are trusted host bindings, not session objects verified here.
    """
    if (type(specs) is not list or len(specs) != 2 or
            any(type(s) is not ProjectWorkerSpec for s in specs) or not callable(confirm_fn)):
        raise ValueError('two explicit project specifications and confirmation callback required')
    specs = tuple(specs)  # Freeze the host-provided ordering across callbacks.
    failures = tuple(ProjectWorkerOutcome(s.project_id, s.files, 'incomplete', reason='not_completed') for s in specs)
    try:
        if (any(type(s.project_id) is not str or type(s.session_id) is not str or
                not isinstance(s.project_root, Path) or type(s.files) is not tuple or
                not callable(s.host_binding_check) for s in specs)
                or specs[0].project_id == specs[1].project_id
                or specs[0].session_id == specs[1].session_id
                or specs[0].client is specs[1].client
                or os.path.normcase(os.path.normpath(str(specs[0].project_root))) ==
                   os.path.normcase(os.path.normpath(str(specs[1].project_root)))):
            return failures
        if cancel_event is not None and (not isinstance(cancel_event, Event) or cancel_event.is_set()):
            return failures
    except Exception:
        return failures

    stopped = Event()
    def cancelled() -> bool:
        return stopped.is_set() or (cancel_event is not None and cancel_event.is_set())
    def binding(spec: ProjectWorkerSpec) -> bool:
        try:
            return (cancel_event is None or not cancel_event.is_set()) and spec.host_binding_check() is True
        except Exception:
            return False

    prepared = []
    for spec in specs:
        prepared.append(prepare_project_summary(spec.project_id, spec.project_root, list(spec.files),
                                                spec.session_id, spec.client, lambda s=spec: binding(s)))
    # Compare secure Windows root identities, not just lexical path spelling.
    ready = [p for p in prepared if p.status == 'ready']
    if len(ready) == 2:
        try:
            if (ready[0]._reader is ready[1]._reader or ready[0]._reader._root_identity is None
                    or ready[0]._reader._root_identity == ready[1]._reader._root_identity):
                return failures
        except Exception:
            return failures

    queues = [Queue(maxsize=1), Queue(maxsize=1)]
    def worker(index: int) -> ProjectWorkerOutcome:
        spec, prep = specs[index], prepared[index]
        if prep.status != 'ready':
            return failures[index]
        def confirm(preview: str, allow: bool) -> str:
            if cancelled(): return 'n'
            answered = Event()
            slot = {'answer': 'n'}
            queues[index].put((preview, allow, answered, slot))
            answered.wait()
            return slot['answer']
        result = execute_project_summary(prep, spec.client, confirm, lambda: binding(spec))
        if (result.status == 'ok' and result.project_id == spec.project_id
                and result.files == spec.files and result.is_acceptance is False):
            return ProjectWorkerOutcome(spec.project_id, spec.files, 'ok', result.summary)
        return failures[index]

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix='readonly-project') as pool:
        futures = [pool.submit(worker, i) for i in range(2)]
        for index, future in enumerate(futures):
            while not future.done():
                try:
                    preview, allow, answered, slot = queues[index].get(timeout=0.05)
                except Empty:
                    continue
                try:
                    if (not cancelled() and allow is False and preview == prepared[index].preview_json
                            and binding(specs[index])):
                        answer = confirm_fn(specs[index].project_id, preview, False)
                        if type(answer) is str and answer == 'y' and binding(specs[index]):
                            slot['answer'] = 'y'
                        else:
                            stopped.set()
                    else:
                        stopped.set()
                except BaseException:
                    # Unblock both workers even if host confirmation is interrupted.
                    stopped.set()
                finally:
                    answered.set()
                break
        outcomes = []
        for i, future in enumerate(futures):
            try:
                outcomes.append(future.result())
            except Exception:
                outcomes.append(failures[i])
        return tuple(outcomes)
