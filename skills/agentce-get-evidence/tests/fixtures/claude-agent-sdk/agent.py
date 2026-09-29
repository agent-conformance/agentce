"""A Claude Agent SDK credit agent (fixture for find_chokepoints; not executed)."""

import anthropic

client = anthropic.Anthropic()


def tool_wire_transfer(amount: int) -> dict:
    return execute_tool("wire_transfer", {"amount": amount})


def ask(prompt: str) -> str:
    system_message = "You are a careful credit underwriter."
    response = client.messages.create(
        model="claude-3-5-sonnet", messages=[{"role": "user", "content": prompt}]
    )
    return response


def escalate_incident(detail: str) -> None:
    notify("oncall", detail)
