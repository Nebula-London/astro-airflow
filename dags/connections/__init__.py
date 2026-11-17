"""Secret/connection access for the external services used by the DAG."""
from dags.connections.jobserve_secrets import read_search_urls

__all__ = ["read_search_urls"]
