#!/usr/bin/env python
"""
Bootstrap script for initial CRM data.

Creates the default Department.
Idempotent — safe to run multiple times.
"""
import logging

logger = logging.getLogger(__name__)

DEPARTMENT_NAME = "LinkedIn Outreach"


def setup_crm():
    from django.contrib.sites.models import Site
    from common.models import Department

    # Ensure default Site exists
    Site.objects.get_or_create(id=1, defaults={"domain": "localhost", "name": "localhost"})

    # Create Department
    dept, created = Department.objects.get_or_create(name=DEPARTMENT_NAME)
    if created:
        logger.info("Created department: %s", DEPARTMENT_NAME)
    else:
        logger.debug("Department already exists: %s", DEPARTMENT_NAME)

    logger.debug("CRM setup complete.")
