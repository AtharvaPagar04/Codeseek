"""Smoke test for the local Qdrant ingestion output."""

from qdrant_client import QdrantClient

from rag_ingestion.config import COLLECTION_NAME, EMBEDDING_DIM, QDRANT_HOST, QDRANT_PORT


def main() -> None:
    client = QdrantClient(QDRANT_HOST, port=QDRANT_PORT)

    info = client.get_collection(COLLECTION_NAME)
    print(f"Collection: {COLLECTION_NAME}")
    print(f"Points: {info.points_count}")

    results = client.search(
        collection_name=COLLECTION_NAME,
        query_vector=[0.0] * EMBEDDING_DIM,
        limit=3,
    )

    for result in results:
        payload = result.payload or {}
        print(
            payload.get("symbol_name", ""),
            payload.get("relative_path", ""),
        )


if __name__ == "__main__":
    main()
