"""Pure, reusable helpers for the JobServe DAG.

Task callables live in ``dags.tasks`` and DAG wiring lives in ``dags.factory``.
The modules here carry no Airflow decorators, so they can be unit tested
without a running scheduler. Every helper is defined at module level and all
output goes through a module logger (never ``print``).
"""
