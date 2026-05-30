"""Optional, env-gated plugin modules.

Plugins are imported by the runner but never load their heavy dependencies
unless the corresponding environment variables are set. This keeps the
default topos install free of optional deps (e.g. supabase) and zero-impact
on runs that don't enable them.
"""
