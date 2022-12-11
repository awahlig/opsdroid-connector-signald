import os
import logging
import asyncio
import binascii

from aiosignald import SignaldAPI
from aiosignald.util import locals_to_request
import aiosignald.generated as api

import opsdroid.events
from opsdroid.connector import Connector, register_event

from .parser import EventParser


logger = logging.getLogger(__name__)


class Signald(SignaldAPI):
    @classmethod
    async def open(cls, socket_path):
        loop = asyncio.get_running_loop()
        _, signald = await loop.create_unix_connection(cls, path=socket_path)
        return signald

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.is_closed():
            data = await self.receive_queue.get()
            if data is not None:
                return data
        raise StopAsyncIteration

    def connection_made(self, transport):
        super().connection_made(transport)
        self.receive_queue = asyncio.Queue()

    def connection_lost(self, exc):
        super().connection_lost(exc)
        self.receive_queue.put_nowait(None)
        self.receive_queue = None

    def is_closed(self):
        return self.transport is None

    def close(self):
        if not self.is_closed():
            self.transport.close()

    def handle_version(self, data, payload):
        logger.info("signald version %s", payload["data"]["version"])

    def handle_ListenerState(self, data, payload):
        pass

    def handle_WebSocketConnectionState(self, data, payload):
        if data.socket == "IDENTIFIED":
            logger.info("signald service is %s", data.state)

    def handle_IncomingMessage(self, data, payload):
        self.receive_queue.put_nowait(data)


class ConnectorSignald(Connector):
    """A connector for the Signal chat service."""

    def __init__(self, config, *args, **kwargs):
        """Create the connector."""

        super().__init__(config, *args, **kwargs)

        # Parse the connector configuration.
        try:
            self.socket_path = config["socket-path"]
            self.bot_number = config["bot-number"]
            self.outgoing_path = config.get("outgoing-path", "")
            self.rooms = config.get("rooms", {})
            self.whitelist = frozenset(self.rooms.get(v, v) for v in
                                       config.get("whitelisted-numbers", []))
        except KeyError as error:
            logger.error("required setting %r not found", error.args[0])
            raise

        self.aliases = {v: k for k, v in self.rooms.items()}

    def lookup_target(self, target):
        """Convert room alias into Signal phone number or group ID.
        This is called by constrain_rooms decorator.
        """

        return self.rooms.get(target, target)

    def target_to_recipient(self, target):
        target = self.lookup_target(target)
        if target.startswith("group."):
            group_id = binascii.a2b_base64(target[6:]).decode("ascii")
            return dict(recipientGroupId=group_id)
        return dict(recipientAddress=api.JsonAddressv1(number=target))

    async def connect(self):
        """Connect to the chat service.
        """

        self.signald = await Signald.open(self.socket_path)

    async def disconnect(self):
        """Disconnect from the chat service."""

        self.signald.close()
        self.signald = None

    async def listen(self):
        """Listen for and parse new messages."""

        await self.signald.subscribe(self.bot_number)

        async for data in self.signald:
            parser = EventParser(self.whitelist, self.aliases)
            marked_read = False

            for event_data in parser.parse(data):
                logger.debug(event_data)

                if event_data.mark_read and not marked_read:
                    await self.mark_read(event_data)
                    marked_read = True

                event = event_data.into_event(connector=self)
                await self.opsdroid.parse(event)

    async def mark_read(self, event):
        logger.info("mark %s as read to %s", event.event_id, event.target)
        response = await self.signald.mark_read(account=self.bot_number,
                                                timestamps=[event.event_id],
                                                to=event.user_id)
        logger.debug("response %s", response)

    async def send_message(self, event, **kwargs):
        logger.debug("send message %s to %s", kwargs, event.target)

        recipient = self.target_to_recipient(event.target)
        response = await self.signald.send(username=self.bot_number,
                                           **recipient, **kwargs)
        logger.debug("response %s", response)

    @register_event(opsdroid.events.Message)
    async def send_text(self, event):
        """Send a text message."""

        logger.info("send text %r to %s", event.text, event.target)
        await self.send_message(event, messageBody=event.text)

    @register_event(opsdroid.events.File, include_subclasses=True)
    async def send_file(self, event):
        """Send a file/image/video message."""

        filename = os.path.join(self.outgoing_path, "attachment")
        file_bytes = await event.get_file_bytes()
        mimetype = await event.get_mimetype()

        with open(filename, "wb") as file:
            file.write(file_bytes)

        attachment = api.JsonAttachmentv0(
            filename=filename,
            customFilename=event.name,
            contentType=mimetype,
        )

        logger.info("send file %r to %s", event.name, event.target)
        await self.send_message(event, attachments=[attachment])
        os.unlink(filename)

    @register_event(opsdroid.events.Typing)
    async def send_typing(self, event):
        """Set or remove the typing indicator."""

        recipient = self.target_to_recipient(event.target)
        address = recipient.get("recipientAddress")
        group = recipient.get("recipientGroupId")

        logger.info("send typing %s to %s", event.trigger, event.target)
        response = await self.signald.typing(account=self.bot_number, address=address,
                                             group=group, typing=event.trigger)
        logger.debug("response %s", response)

    @register_event(opsdroid.events.Reaction)
    async def send_reaction(self, event):
        """Send a reaction to a message."""

        reaction = signald.JsonReactionv1(
            emoji=event.emoji,
            remove=not event.emoji,
            targetAuthor=event.linked_event.user_id,
            targetSentTimestamp=event.linked_event.event_id,
        )

        recipient = self.target_to_recipient(event.target)

        logger.info("send reaction %r to %s", event.emoji, event.target)
        response = await self.signald.react(username=self.bot_number,
                                            reaction=reaction, **recipient)
        logger.debug("response %s", response)
