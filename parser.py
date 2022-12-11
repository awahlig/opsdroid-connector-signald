import logging
import binascii
from dataclasses import dataclass, asdict
from typing import Any

import opsdroid.events

import aiosignald.generated as api


logger = logging.getLogger(__name__)


@dataclass
class EventData:
    event_class = opsdroid.events.Event
    mark_read = False

    user_id: str = None
    user: str = None
    target: str = None
    raw_event: Any = None
    raw_parses: Any = None
    event_id: str = None
    linked_event: opsdroid.events.Event = None

    def into_event(self, **kwargs):
        return self.event_class(**asdict(self), **kwargs)

    @classmethod
    def from_data(cls, data):
        return cls(**asdict(data))


@dataclass
class MessageData(EventData):
    event_class = opsdroid.events.Message
    mark_read = True

    text: str = ""


@dataclass
class TypingData(EventData):
    event_class = opsdroid.events.Typing

    trigger: bool = True
    timeout: float = None

    def into_event(self, **kwargs):
        return self.event_class(**asdict(self), **kwargs)


@dataclass
class ReactionData(EventData):
    event_class = opsdroid.events.Reaction

    emoji: str = ""


@dataclass
class FileEventData(EventData):
    event_class = opsdroid.events.File
    mark_read = True

    file_bytes: bytes = None
    url: str = None
    url_headers: dict = None
    name: str = None
    mimetype: str = None


@dataclass
class ImageEventData(FileEventData):
    event_class = opsdroid.events.Image


@dataclass
class VideoEventData(FileEventData):
    event_class = opsdroid.events.Video


class EventParser:
    def __init__(self, whitelist=None, aliases=None):
        self.whitelist = whitelist
        self.aliases = (aliases or {})
        self.data = EventData()

    def parse(self, data):
        if isinstance(data, api.IncomingMessagev1):
            yield from self.parse_message(data)

    def parse_message(self, envelope):
        logger.debug("parse envelope %s", envelope)

        self.data = data = EventData(
            user_id=envelope.source.number,
            raw_event=envelope,
            event_id=envelope.timestamp,
        )

        if not data.user_id:
            logger.warning("message with no user id")
            return

        if self.whitelist and data.user_id not in self.whitelist:
            logger.warning("user %r not whitelisted", data.user_id)
            return

        # Default target, changed later if a group ID is found.
        self.set_target(data.user_id)
        data.user = data.target

        if envelope.data_message:
            yield from self.parse_data_message(envelope.data_message)
        if envelope.typing_message:
            yield from self.parse_typing_message(envelope.typing_message)

    def parse_data_message(self, data_message):
        if data_message.group:
            self.set_group_target(data_message.group.groupId)
        elif data_message.groupV2:
            self.set_group_target(data_message.groupV2.id)

        if data_message.reaction:
            yield from self.parse_reaction(data_message.reaction)
        elif data_message.body:
            yield from self.parse_text(data_message.body)

        for attachment in data_message.attachments:
            yield from self.parse_attachment(attachment)

    def parse_reaction(self, reaction):
        linked = EventData(user_id=reaction.targetAuthor.number,
                           target=self.data.target,
                           event_id=reaction.targetSentTimestamp)

        data = ReactionData.from_data(self.data)
        data.emoji = ("" if reaction.remove else reaction.emoji)
        data.linked_event = linked.into_event()

        logger.info("received reaction %r from %s", data.emoji, data.target)
        yield data

    def parse_text(self, text):
        data = MessageData.from_data(self.data)
        data.text = text

        logger.info("received text %r from %s", data.text, data.target)
        yield data

    def parse_attachment(self, attachment):
        data = FileEventData.from_data(self.data)
        data.name = attachment.customFilename
        data.mimetype = attachment.contentType

        with open(attachment.storedFilename, "rb") as fobj:
            data.file_bytes = fobj.read()

        mimetype = (data.mimetype or "")
        if mimetype.startswith("image/"):
            data = ImageEventData.from_data(data)
        elif mimetype.startswith("video/"):
            data = VideoEventData.from_data(data)

        logger.info("received file %r from %s", data.name, data.target)
        yield data

    def parse_typing_message(self, typing_message):
        if typing_message.group_id:
            self.set_group_target(typing_message.group_id)

        data = TypingData.from_data(self.data)
        data.trigger = (typing_message.action == "STARTED")
        data.timeout = 15

        logger.info("received typing %s from %s", data.trigger, data.target)
        yield data

    def set_target(self, target):
        # Use an alias if available.
        self.data.target = self.aliases.get(target, target)

    def set_group_target(self, group_id):
        group_id = binascii.b2a_base64(group_id.encode("ascii")).decode("ascii")
        self.set_target("group.{}".format(group_id.strip()))
