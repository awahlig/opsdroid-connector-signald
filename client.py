import logging
import asyncio
import pprint
import json
import uuid
import os


logger = logging.getLogger(__name__)


class SignaldError(Exception):
    def __init__(self, payload):
        super().__init__()
        self.payload = payload
        self.error_type = payload["error_type"]
        try:
            self.message = payload["error"]["message"]
        except KeyError:
            self.message = ""

    def __str__(self):
        return f"{self.error_type}: {self.message}"


class SignaldClient:
    def __init__(self, socket_path=None):
        self.socket_path = socket_path
        self.requests = {}
        self.listen_task = None
        self.receive_queue = asyncio.Queue()

    async def connect(self):
        if self.is_connected():
            raise AssertionError("already connected")

        if self.socket_path:
            paths = [self.socket_path]
        else:
            paths = [os.path.expandvars("$XDG_RUNTIME_DIR/signald/signald.sock"),
                     "/var/run/signald/signald.sock"]

        for i, path in enumerate(paths, 1):
            try:
                self.reader, self.writer = await asyncio.open_unix_connection(path)
                break
            except OSError:
                if i == len(paths):
                    raise
                logger.warning("failed to open socket %s", path)

        self.listen_task = asyncio.create_task(self.listen())

    async def disconnect(self):
        if self.listen_task:
            self.listen_task.cancel()
            self.listen_task = None
        if self.writer:
            self.writer.close()
            await self.writer.wait_closed()
            self.reader = self.writer = None

    def is_connected(self):
        return (self.listen_task and not self.listen_task.done())

    async def listen(self):
        while True:
            try:
                line = await self.reader.readuntil(b"\n")
            except asyncio.IncompleteReadError:
                break

            payload = json.loads(line.decode("utf-8"))
            self.handle_payload(payload)

    def handle_payload(self, payload):
        try:
            future = self.requests[payload["id"]]
        except KeyError:
            self.receive_queue.put_nowait(payload)
        else:
            if "error_type" in payload:
                future.set_exception(SignaldError(payload))
            else:
                future.set_result(payload)

    async def get_next_notification(self):
        return await self.receive_queue.get()

    async def send(self, payload):
        for name in ("type", "version"):
            if name not in payload:
                raise AssertionError(f"\"{name}\" missing from payload")
        data = json.dumps(payload).encode("utf-8") + b"\n"
        self.writer.write(data)
        await self.writer.drain()

    async def request(self, payload):
        id_ = payload.get("id")
        if not id_ or id_ in self.requests:
            id_ = str(uuid.uuid4())
            payload = {**payload, "id": id_}
        future = self.requests[id_] = asyncio.Future()
        try:
            await self.send(payload)
            return await future
        finally:
            del self.requests[id_]
