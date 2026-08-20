"""
upload_documents.py

Uploads a local directory of source documents to the pattern's Blob Storage
`raw` container, which the AI Search indexer (see post_deploy_search.py) picks
up on its next run.

Corpus-neutral: file types come from the `corpus.sourceFileExtensions` list in
demo-ids.local.json (default: .pdf). The Document Intelligence Layout skill
also reads Office formats (.docx, .pptx, .xlsx) and images (.png, .jpeg,
.tiff), so add those extensions when your corpus needs them.

Usage:
    python upload_documents.py --ids-file ../demo-ids.local.json --source-dir <path-to-documents>
"""
import argparse
import json
import sys
from pathlib import Path

from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient

DEFAULT_SOURCE_EXTENSIONS = [".pdf"]


def load_ids(ids_file: str) -> dict:
    with open(ids_file, "r", encoding="utf-8") as f:
        return json.load(f)


def source_extensions(ids: dict) -> list[str]:
    """Extensions to upload, from the optional `corpus` block. Normalized to
    lowercase with a leading dot so callers can write '.pdf' or 'pdf'."""
    corpus = ids.get("corpus") or {}
    raw = corpus.get("sourceFileExtensions") or DEFAULT_SOURCE_EXTENSIONS
    return [e.lower() if e.startswith(".") else f".{e.lower()}" for e in raw]


def upload_documents(blob_endpoint: str, container: str, source_dir: Path, extensions: list[str]) -> int:
    credential = DefaultAzureCredential()
    service_client = BlobServiceClient(account_url=blob_endpoint, credential=credential)
    container_client = service_client.get_container_client(container)

    docs = sorted(p for p in source_dir.iterdir() if p.is_file() and p.suffix.lower() in extensions)
    if not docs:
        print(f"No files matching {extensions} found in {source_dir}", file=sys.stderr)
        return 0

    uploaded = 0
    for doc_path in docs:
        blob_name = doc_path.name
        print(f"Uploading {blob_name}...")
        with open(doc_path, "rb") as data:
            container_client.upload_blob(name=blob_name, data=data, overwrite=True)
        uploaded += 1

    return uploaded


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ids-file", required=True, help="Path to demo-ids.local.json")
    parser.add_argument("--source-dir", required=True, help="Directory of source documents to upload")
    args = parser.parse_args()

    ids = load_ids(args.ids_file)
    source_dir = Path(args.source_dir)
    if not source_dir.is_dir():
        print(f"Source directory does not exist: {source_dir}", file=sys.stderr)
        sys.exit(1)

    container = ids.get("rawContainer", "raw")
    blob_endpoint = ids["blobEndpoint"]
    extensions = source_extensions(ids)

    count = upload_documents(blob_endpoint, container, source_dir, extensions)
    print(f"Uploaded {count} document(s) to {blob_endpoint}{container}")
    print("Next: python post_deploy_search.py --ids-file <ids-file> --create-index --create-skillset --create-indexer --run-indexer")


if __name__ == "__main__":
    main()
