"""A CrewAI credit crew (fixture for find_chokepoints; not executed)."""

from crewai import Agent, Crew, Task
from crewai.tools import tool


@tool("bureau")
def bureau(ssn: str) -> dict:
    return {"score": 700}


def underwrite(application: dict) -> dict:
    memory.write("applications", application)
    docs = similarity_search(application["notes"])
    return {"recommendation": "approve"}


underwriter = Agent(role="underwriter", goal="decide credit", backstory="careful")
crew = Crew(agents=[underwriter], tasks=[Task(description="underwrite")])
