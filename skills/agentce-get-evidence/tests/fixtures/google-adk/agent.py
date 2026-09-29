"""A Google ADK credit agent (fixture for find_chokepoints; not executed)."""

from google.adk.agents import Agent
from google.adk.tools import FunctionTool


def check(user: str, obj: str) -> bool:
    # Authorization is evaluated server-side by OPA (an enforcement point).
    return opa_check(user, "read", obj)


def summarise(context: str) -> str:
    return generate_content(context)


def classify_applicant(features: dict) -> str:
    return "prime"


agent = Agent(name="credit", tools=[FunctionTool(check)])
