"""A LangGraph credit-underwriting agent (fixture for find_chokepoints; not executed)."""

import os

from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph

api_key = os.environ["OPENAI_API_KEY"]
llm = ChatOpenAI(model="gpt-4o")
memory = MemorySaver()


def call_model(state: dict) -> dict:
    system_prompt = "You are a credit underwriter."
    return {"messages": llm.invoke(state["messages"])}


def credit_decision(state: dict) -> dict:
    if not authorize(state["user"], "disburse"):
        raise PolicyError("refused: not authorized")
    return {"decision": "approve"}


graph = StateGraph(dict)
graph.add_node("model", call_model)
