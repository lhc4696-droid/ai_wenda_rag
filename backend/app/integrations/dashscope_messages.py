"""Translate internal text histories to DashScope multimodal messages."""

from copy import deepcopy


def multimodal_messages(messages):
    """Keep tool results as strings and preserve tool-call IDs and arguments.

    Copy the history because the SDK may normalize messages in place. Stored
    conversation messages must remain usable by other providers.
    """
    payload = deepcopy(messages)
    for message in payload:
        if message.get("role") == "tool":
            continue
        content = message.get("content")
        if isinstance(content, str):
            message["content"] = [{"text": content}] if content else []
        elif content is None:
            message["content"] = []
    return payload
