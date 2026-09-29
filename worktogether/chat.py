"""Deterministic queue pump: no model calls while idle, no automatic retry."""
import threading


class ChatQueue:
    def __init__(self, engine, codex, antigravity):
        self.engine = engine
        self.providers = {"codex": codex, "antigravity": antigravity}
        self.stopped = threading.Event()

    def once(self):
        with self.engine.transaction() as db:
            tasks = [r[0] for r in db.execute("SELECT DISTINCT task_id FROM messages")]
        for task in tasks:
            if self.stopped.is_set():
                return
            d = self.engine.chat_dispatch(task)
            if d:
                self.providers[d["role"]].run(task, d["id"], 300)

    def start(self):
        def loop():
            while not self.stopped.wait(1):
                try:
                    self.once()
                except Exception:
                    # Durable dispatch/run records remain unresolved; never replay.
                    import logging
                    logging.exception("Chat queue stopped this polling pass")
        threading.Thread(target=loop, daemon=True, name="TaskChatQueue").start()
