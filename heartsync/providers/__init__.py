"""External-service adapters (SMS, email, payments, storage, AI).

Each adapter has a development implementation that is clearly labelled and
refuses to run in production, plus a real-provider implementation that needs
credentials from the environment.
"""
