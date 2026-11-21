"""Importing this package registers every route/flow with core.router."""
from . import (  # noqa: F401
    home, accounts, cf_ui, domains, analytics,
    ec2_ui, ec2_tools, link, ops, security, search_bulk, jobs,
)
