"""Blender-backed render tools — host-side @tool registrations + Blender-side wrapper.

The ``blender_`` prefix marks this subpackage as Blender-specific. Future
non-Blender render backends (e.g. ``threejs_render/``, ``unity_render/``)
would live as sibling subpackages with the same ``tool.py`` + ``wrapper.py``
shape but different runtimes.

Files:
- ``tool.py``    — host-side @tool functions (render_multiview / render_part).
                   Run in host venv.
- ``wrapper.py`` — Blender-side script invoked as
                   ``blender --background --python wrapper.py``. Runs in
                   Blender's bundled Python; no ``topos`` imports allowed.

Importing this subpackage triggers the ``@tool`` decorators on tool.py.
"""

from . import tool  # noqa: F401  (registers render_multiview / render_part)
