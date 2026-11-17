"""
Simple Airflow plugin example.

Defines:
- HelloOperator: a tiny custom operator that logs a greeting.
- ExamplePlugin: registers the operator with Airflow's plugin system.

After adding/changing plugins, restart Airflow so it reloads them.
"""

from airflow.models.baseoperator import BaseOperator
from airflow.plugins_manager import AirflowPlugin


class HelloOperator(BaseOperator):
    """A very simple custom operator for demo purposes."""

    def __init__(self, name: str = "World", **kwargs):
        super().__init__(**kwargs)
        self.name = name

    def execute(self, context):
        message = f"Hello, {self.name}! This message comes from a custom plugin operator."
        self.log.info(message)
        print(message)
        return message


class ExamplePlugin(AirflowPlugin):
    name = "example_plugin"
    operators = [HelloOperator]
    # You can also register hooks, macros, flask blueprints, etc.
