"""Deployment unit 4 of 4 — `workers`.

Small Lambda functions: SES ingestion notification, trigger wakeups, outbox
sweeping and dispatch, and the document-analysis completion callback. Their
logic lives in the control plane and is tested there; these are thin handlers.
"""
