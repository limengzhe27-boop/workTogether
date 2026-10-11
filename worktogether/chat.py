"""Deterministic queue pump: no model calls while idle, no automatic retry.

One worker per task. Inside a worker a whole round is issued at once and the
agents run concurrently: each role has its own CLI session, so a parallel
broadcast is safe as long as the platform serializes file writes, which
core._pick_writer does.
"""
import threading
import json
import logging
from concurrent.futures import ThreadPoolExecutor


class ChatQueue:
    def __init__(self, engine, codex, antigravity):
        self.engine = engine
        self.providers = {"codex": codex, "antigravity": antigravity}
        self.stopped = threading.Event()
        self._workers = {}
        self._worker_lock = threading.Lock()
        self._cursor = 0
        self.max_parallel_tasks = 4
        self.max_parallel_agents = 4

    def get_provider(self, role):
        if role in self.providers:
            try:
                from .agents import AgentRegistry
                cfg = AgentRegistry(self.engine).get(role)
                if cfg:
                    self.providers[role].system_prompt = getattr(cfg, "system_prompt", "")
            except Exception:
                pass
            return self.providers[role]
        from .agents import AgentRegistry
        # Custom configuration may change between messages; never keep a stale adapter.
        return AgentRegistry(self.engine).get_runtime(role)

    def _run_round(self, task_id):
        """Issue the current round and run its agents concurrently.

        A dispatch that was issued but never run is deliberately NOT picked up
        here: an ambiguous launch must be retried explicitly, never replayed.
        """
        dispatches = self.engine.chat_dispatch_round(task_id)
        if not dispatches:
            return 0
        if len(dispatches) == 1:
            dispatch = dispatches[0]
            self.get_provider(dispatch["role"]).run(task_id, dispatch["id"], 300)
            return 1

        def run(dispatch):
            try:
                self.get_provider(dispatch["role"]).run(task_id, dispatch["id"], 300)
            except Exception:
                # The durable dispatch stays unresolved for an explicit retry.
                logging.exception("Parallel chat agent stopped; inspect persistent state")

        with ThreadPoolExecutor(max_workers=min(self.max_parallel_agents, len(dispatches))) as pool:
            list(pool.map(run, dispatches))
        return len(dispatches)

    def once(self):
        with self.engine.transaction() as db:
            tasks = [r[0] for r in db.execute("SELECT DISTINCT task_id FROM messages")]
        for task in tasks:
            if self.stopped.is_set():
                return
            self._run_round(task)

    def start(self):
        def loop():
            while not self.stopped.wait(1):
                try:
                    self.background_once()
                except Exception:
                    # Durable dispatch/run records remain unresolved; never replay.
                    logging.exception("Chat queue stopped this polling pass")
        threading.Thread(target=loop, daemon=True, name="TaskChatQueue").start()

    def background_once(self):
        """Bounded cross-task concurrency; one worker per task, no model polling."""
        with self._worker_lock:
            if self.stopped.is_set():
                return 0
            self._workers = {key: worker for key, worker in self._workers.items() if worker.is_alive()}
            capacity = self.max_parallel_tasks - len(self._workers)
            if capacity <= 0:
                return 0
            with self.engine.transaction() as db:
                pending = list(dict.fromkeys(row["task_id"] for row in db.execute(
                    "SELECT task_id,body FROM messages ORDER BY rowid")
                    if json.loads(row["body"])["status"] == "QUEUED"))
            if not pending:
                return 0
            offset = self._cursor % len(pending)
            ordered = pending[offset:] + pending[:offset]
            self._cursor = (offset + capacity) % len(pending)
            started = 0
            for task_id in ordered:
                if task_id in self._workers:
                    continue
                worker = threading.Thread(target=self._work_task, args=(task_id,),
                                          daemon=True, name="Chat-" + task_id)
                self._workers[task_id] = worker
                try:
                    worker.start()
                except Exception:
                    self._workers.pop(task_id, None)
                    raise
                started += 1
                if started == capacity:
                    break
            return started

    def _work_task(self, task_id):
        try:
            if self.stopped.is_set():
                return
            self._run_round(task_id)
        except Exception:
            # Leave the durable dispatch unresolved. Do not silently retry an
            # ambiguous launch merely because this worker exited.
            logging.exception("Task chat worker stopped; inspect persistent state")
