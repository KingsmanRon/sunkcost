"""The worker service (ARCHITECTURE.md §4 C7): a job runner and a scheduler in one process.

The worker never calls the API and the API never calls the worker; they share only Postgres
(the ``jobs`` table). Only the worker holds the bundle signing key.
"""
