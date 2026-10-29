"""Explicit local reindex command; dry-run by default, never run on migration.

python -m app.services.retrieval_index --document-id UUID --apply
builds only the lexical projection without an embedding/model request.
Dense rebuild requires both --dense and --allow-paid-provider.
"""

import argparse
import json
import sys
import uuid


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--document-id", type=uuid.UUID, action="append", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dense", action="store_true")
    parser.add_argument("--allow-paid-provider", action="store_true")
    args = parser.parse_args()
    if args.dense and not args.allow_paid_provider:
        parser.error(
            "Dense reindex can call an embedding provider; pass --allow-paid-provider explicitly"
        )
    if len(args.document_id) > 100:
        parser.error("At most 100 explicitly selected documents may be reindexed")
    if not args.apply:
        sys.stdout.write(
            json.dumps(
                {
                    "dry_run": True,
                    "document_ids": list(map(str, args.document_id)),
                    "mode": "dense_and_lexical" if args.dense else "lexical_only",
                }
            )
        )
        return
    from sqlmodel import Session

    from app.core.db import engine
    from app.models import KnowledgeDocument
    from app.services.document_processor import ingest_document

    results = []
    for identity in dict.fromkeys(args.document_id):
        with Session(engine) as session:
            document = session.get(KnowledgeDocument, identity)
            if document is None:
                raise ValueError(f"Document does not exist: {identity}")
        ingest_document(identity, lexical_only=not args.dense)
        with Session(engine) as session:
            document = session.get(KnowledgeDocument, identity)
            results.append(
                {
                    "document_id": str(identity),
                    "status": document.status if document else "deleted",
                }
            )
    sys.stdout.write(json.dumps({"dry_run": False, "results": results}) + "\n")


if __name__ == "__main__":
    main()
