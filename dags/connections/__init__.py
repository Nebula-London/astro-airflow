"""Secret/connection access for the external services used by the DAG."""
from dags.connections.jobserve_secrets import read_search_urls
from dags.connections.portal_secrets import read_portal_urls

__all__ = ["read_portal_urls", "read_search_urls"]
