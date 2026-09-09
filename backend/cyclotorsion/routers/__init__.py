"""Per-domain FastAPI routers, composed onto the app in ``cyclotorsion.app``.

Split out of a single 485-line ``app.py`` (staff review Minor) to keep each
domain's diff surface small: clinical detection, aggregate stats, admin/RBAC,
and infra endpoints no longer share one file.

Each router reads shared runtime state (``_DETECTOR``, ``_WRITER``,
``_ANALYTICS_WORKER``) from ``cyclotorsion.app`` **at call time** via
``import cyclotorsion.app as app_state`` — never via
``from cyclotorsion.app import _WRITER``, which would bind a stale reference
and silently break test monkeypatching of ``app_mod._WRITER`` etc.
"""
