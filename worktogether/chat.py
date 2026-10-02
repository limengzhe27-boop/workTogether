"""Deterministic queue pump: no model calls while idle, no automatic retry."""
import threading
import json
import logging


class ChatQueue:
    def __init__(self, engine, codex, antigravity):
        self.engine = engine
        self.providers = {"codex": codex, "antigravity": antigravity}
        self.stopped = threading.Event()
        self._workers = {}
        self._worker_lock = threading.Lock()
        self._cursor = 0
        self.max_parallel_tasks = 4

    def get_provider(self, role):
        if role in self.providers:
            return self.providers[role]
        from .agents import AgentRegistry
        # Custom configuration may change between messages; never keep a stale adapter.
        return AgentRegistry(self.engine).get_runtime(role)

    def once(self):
        with self.engine.transaction() as db:
            tasks = [r[0] for r in db.execute("SELECT DISTINCT task_id FROM messages")]
        for task in tasks:
            if self.stopped.is_set():
                return
            d = self.engine.chat_dispatch(task)
            if d:
                provider = self.get_provider(d["role"])
                provider.run(task, d["id"], 300)

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
            dispatch = self.engine.chat_dispatch(task_id)
            if dispatch:
                self.get_provider(dispatch["role"]).run(task_id, dispatch["id"], 300)
        except Exception:
            # Leave the durable dispatch unresolved. Do not silently retry an
            # ambiguous launch merely because this worker exited.
            logging.exception("Task chat worker stopped; inspect persistent state")
