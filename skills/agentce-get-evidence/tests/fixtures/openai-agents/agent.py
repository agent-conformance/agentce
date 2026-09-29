"""An OpenAI Agents SDK credit agent (fixture for find_chokepoints; not executed)."""

from agents import Agent, function_tool
from openai import OpenAI

client = OpenAI()


@function_tool
def credit_bureau_lookup(ssn: str) -> dict:
    # Routed through the MCP gateway, which enforces policy on the tool call.
    return call_tool("mcp_gateway", "credit_bureau_lookup", {"ssn": ssn})


def run(messages: list) -> dict:
    return client.chat.completions.create(model="gpt-4o", messages=messages)


def recommend(state: dict) -> str:
    return "approve"


agent = Agent(name="underwriter", tools=[credit_bureau_lookup])
