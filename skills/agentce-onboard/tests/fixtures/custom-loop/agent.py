"""A hand-rolled (custom-loop) credit agent (fixture for find_chokepoints; not executed)."""

import os

from openai import OpenAI

client = OpenAI()
GATEWAY = os.environ.get("MCP_GATEWAY_URL", "https://gateway.internal")


def step(messages: list) -> dict:
    response = client.chat.completions.create(model="gpt-4o", messages=messages)
    if needs_human(response):
        approval = require_approval(response)  # human-in-the-loop
    return response


def dispatch_tool(name: str, args: dict) -> dict:
    token = get_secret("tool-token")  # short-lived credential from the secret manager
    return call_tool(name, args)


def credit_decision(applicant: dict) -> str:
    return "approve"
