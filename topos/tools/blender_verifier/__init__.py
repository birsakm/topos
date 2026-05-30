"""Blender-backed buildability verification — host-side @tool + Blender wrapper.

The ``blender_`` prefix marks this subpackage as Blender-specific. Future
non-Blender verify backends (e.g. ``threejs_verify/``, ``openscad_verify/``)
would live as sibling subpackages with the same shape.

For each named part, runs Blender in background, imports ``parts/<lower>.py``,
calls ``build_<lower>()``, asserts a non-None MESH object — no rendering.

Files:
- ``tool.py``    — host-side ``verify_parts`` @tool
- ``wrapper.py`` — Blender-side script

Used both as a framework gate before render_parts AND as a self-check
the coding agent can invoke via Bash (``topos verify-parts <Name>``).
"""

from . import tool  # noqa: F401  (registers verify_parts)
