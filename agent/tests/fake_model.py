"""A scripted stand-in for BedrockModel, so agent tests never call Bedrock.

Each model turn pops the next step from the script: a list of tool calls
(`[("tool_name", {...input...}), ...]`), a final text answer (a str), or a
function that runs first (for example, to revoke the session) and returns one
of those. Every
prompt the agent sends is kept in `seen` so tests can check what the model saw.
"""

from __future__ import annotations

import json
from typing import Any

from strands.models.model import Model


class ScriptedModel(Model):
    def __init__(self, script: list[Any]):
        self.script = list(script)
        self.seen: list[list[dict]] = []
        self.config: dict[str, Any] = {"model_id": "scripted"}

    def update_config(self, **model_config: Any) -> None:
        self.config.update(model_config)

    def get_config(self) -> dict[str, Any]:
        return self.config

    async def structured_output(self, output_model, prompt, system_prompt=None, **kwargs):
        raise NotImplementedError
        yield  # pragma: no cover

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        self.seen.append(json.loads(json.dumps(messages, default=str)))
        step = self.script.pop(0) if self.script else "No further actions."
        if callable(step):
            step = step()
        yield {"messageStart": {"role": "assistant"}}
        if isinstance(step, str):
            yield {"contentBlockStart": {"start": {}}}
            yield {"contentBlockDelta": {"delta": {"text": step}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "end_turn"}}
            return
        for i, (name, tool_input) in enumerate(step):
            tool_use_id = f"t{len(self.seen)}-{i}"
            yield {"contentBlockStart": {"start": {"toolUse": {"toolUseId": tool_use_id, "name": name}}}}
            yield {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(tool_input)}}}}
            yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "tool_use"}}
