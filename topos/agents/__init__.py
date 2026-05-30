"""Topos agents — thin Python helpers that wrap claude-CLI invocations for
specific framework purposes (spec generation, planning, etc.). Distinct from
the L1 AgentBackend protocol: that's the generic "invoke a coding agent on
a task" interface; this module is for one-shot, structured-output calls.
"""
