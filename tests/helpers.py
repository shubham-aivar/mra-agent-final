from itertools import count

from mra.ports import ChatReply, LlmReply

_ids = count(1)


def use(name, **args):
    return {"toolUse": {"toolUseId": f"u{next(_ids)}", "name": name, "input": args}}


def turn(*blocks, tokens=(100, 50)):
    return ChatReply(list(blocks), "tool_use", *tokens)


def end(text="done"):
    return ChatReply([{"text": text}], "end_turn", 10, 5)


class Script:
    """A scripted model for the agent loop: each call to chat() returns the next prepared turn."""
    name, model_id = "script", "script"

    def __init__(self, steps):
        self.steps, self.calls, self.seen = list(steps), 0, []

    def chat(self, system, messages, tools, max_tokens):
        self.seen.append([m for m in messages])
        r = self.steps[min(self.calls, len(self.steps) - 1)]
        self.calls += 1
        if isinstance(r, Exception):
            raise r
        return r

    def complete(self, system, user, max_tokens):
        return LlmReply("{}")


NOTE = ("The CIO said moving to the cloud is the top priority for the next two years. The data centre lease ends in June 2027, so the "
        "first migration wave must finish before the lease ends.\nThere is no cloud team yet and nobody has been hired for it.\n"
        "Business continuity planning has not been adjusted for the cloud and restores have never been tested.\n"
        "Cost management and tagging are agreed in principle but cost reporting has not been communicated.")
