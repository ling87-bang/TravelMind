"""Test-wide environment defaults."""

import os


# Tests should not send traces to an external LangSmith project.
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"
