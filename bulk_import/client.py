#!/usr/bin/env python3
"""Desktop client for bulk bookmark import to tjai.

Reads bookmarks from JSON or Dynalist zip, chunks them, and POSTs to server.

Usage:
    # From JSON file
    python client.py bookmarks.json --source-tag dynalist

    # From Dynalist zip (auto-parses Links.txt and ToDo.txt)
    python client.py dynalist-backup.zip --source-tag dynalist

    # Dry run (parse only, don't import)
    python client.py bookmarks.json --dry-run

Environment:
    TJAI_API_KEY - API key for server auth (or use --api-key)
    TJAI_SERVER  - Server URL (default: https://etaverse.com/tjai)
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


DEFAULT_SERVER = "https://etaverse.com/tjai"
DEFAULT_CHUNK_SIZE = 100


def load_bookmarks(filepath):
    """Load bookmarks from JSON file or Dynalist zip."""
    path = Path(filepath)

    if path.suffix == '.zip':
        # Dynalist zip file - use parser
        from bulk_import.parsers.dynalist import parse_dynalist_zip
        return parse_dynalist_zip(str(path))
    elif path.suffix == '.json':
        # JSON file in standard format
        with open(path, 'r') as f:
            return json.load(f)
    else:
        raise ValueError(f"Unsupported file type: {path.suffix}. Use .json or .zip")


def chunk_list(items, chunk_size):
    """Split list into chunks of given size."""
    for i in range(0, len(items), chunk_size):
        yield items[i:i + chunk_size]


def post_chunk(server_url, api_key, items, source_tag=None, skip_existing=True):
    """POST a chunk of bookmarks to the server.

    Returns dict with imported, skipped, errors, auto_tags.
    """
    url = f"{server_url}/api/bulk-import"

    payload = {
        "items": items,
        "skip_existing": skip_existing,
    }
    if source_tag:
        payload["source_tag"] = source_tag

    data = json.dumps(payload).encode('utf-8')

    req = Request(url, data=data, method='POST')
    req.add_header('Content-Type', 'application/json')
    req.add_header('Authorization', f'Bearer {api_key}')

    try:
        with urlopen(req, timeout=60) as response:
            return json.loads(response.read().decode('utf-8'))
    except HTTPError as e:
        error_body = e.read().decode('utf-8')
        try:
            return json.loads(error_body)
        except json.JSONDecodeError:
            return {"error": f"HTTP {e.code}: {error_body}"}
    except URLError as e:
        return {"error": f"Connection error: {e.reason}"}


def import_bookmarks(filepath, server_url, api_key, source_tag=None,
                     chunk_size=DEFAULT_CHUNK_SIZE, skip_existing=True,
                     dry_run=False):
    """Import bookmarks from file to server.

    Returns summary dict.
    """
    print(f"Loading bookmarks from {filepath}...")
    bookmarks = load_bookmarks(filepath)
    print(f"Loaded {len(bookmarks)} bookmarks")

    if dry_run:
        print("\n=== DRY RUN - No changes will be made ===")
        # Show sample
        print("\nSample entries:")
        for i, bm in enumerate(bookmarks[:5]):
            print(f"  {i+1}. {bm.get('content', '')[:70]}...")
            if bm.get('tags'):
                print(f"      tags: {bm['tags']}")
        if len(bookmarks) > 5:
            print(f"  ... and {len(bookmarks) - 5} more")

        # Tag distribution
        tag_counts = {}
        for bm in bookmarks:
            for tag in bm.get('tags', []):
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
        if tag_counts:
            print("\nTag distribution (top 10):")
            for tag, count in sorted(tag_counts.items(), key=lambda x: -x[1])[:10]:
                print(f"  {tag}: {count}")

        return {
            "total": len(bookmarks),
            "dry_run": True,
        }

    # Chunk and upload
    chunks = list(chunk_list(bookmarks, chunk_size))
    total_chunks = len(chunks)

    summary = {
        "imported": 0,
        "skipped": 0,
        "errors": [],
        "auto_tags": {},
        "failed_chunks": 0,
    }

    print(f"\nUploading in {total_chunks} chunks of {chunk_size}...")
    start_time = time.time()

    for i, chunk in enumerate(chunks):
        chunk_num = i + 1
        progress = (chunk_num / total_chunks) * 100

        print(f"\r[{progress:5.1f}%] Chunk {chunk_num}/{total_chunks}...", end='', flush=True)

        result = post_chunk(server_url, api_key, chunk, source_tag, skip_existing)

        if "error" in result:
            summary["errors"].append(f"Chunk {chunk_num}: {result['error']}")
            summary["failed_chunks"] += 1
        else:
            summary["imported"] += result.get("imported", 0)
            summary["skipped"] += result.get("skipped", 0)
            for err in result.get("errors", []):
                summary["errors"].append(f"Chunk {chunk_num}: {err}")
            for tag, count in result.get("auto_tags", {}).items():
                summary["auto_tags"][tag] = summary["auto_tags"].get(tag, 0) + count

    elapsed = time.time() - start_time
    print(f"\r[100.0%] Complete in {elapsed:.1f}s" + " " * 20)

    return summary


def main():
    parser = argparse.ArgumentParser(
        description="Bulk import bookmarks to tjai",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument("file", help="JSON file or Dynalist zip to import")
    parser.add_argument("--source-tag", help="Tag to add to all imported entries")
    parser.add_argument("--api-key", help="API key (or set TJAI_API_KEY env var)")
    parser.add_argument("--server", default=DEFAULT_SERVER,
                        help=f"Server URL (default: {DEFAULT_SERVER})")
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE,
                        help=f"Entries per request (default: {DEFAULT_CHUNK_SIZE})")
    parser.add_argument("--no-skip-existing", action="store_true",
                        help="Import even if URL already exists")
    parser.add_argument("--dry-run", action="store_true",
                        help="Parse and show stats, don't import")

    args = parser.parse_args()

    # Get API key
    api_key = args.api_key or os.environ.get('TJAI_API_KEY')
    if not api_key and not args.dry_run:
        print("Error: API key required. Use --api-key or set TJAI_API_KEY")
        sys.exit(1)

    # Check file exists
    if not Path(args.file).exists():
        print(f"Error: File not found: {args.file}")
        sys.exit(1)

    # Override server from env if set
    server = os.environ.get('TJAI_SERVER', args.server)

    try:
        summary = import_bookmarks(
            args.file,
            server,
            api_key,
            source_tag=args.source_tag,
            chunk_size=args.chunk_size,
            skip_existing=not args.no_skip_existing,
            dry_run=args.dry_run,
        )
    except Exception as e:
        print(f"\nError: {e}")
        sys.exit(1)

    # Print summary
    print("\n=== Import Summary ===")
    if summary.get("dry_run"):
        print(f"Total bookmarks: {summary['total']}")
        print("(Dry run - no changes made)")
    else:
        print(f"Imported: {summary['imported']}")
        print(f"Skipped (duplicates): {summary['skipped']}")
        if summary.get("failed_chunks"):
            print(f"Failed chunks: {summary['failed_chunks']}")
        if summary.get("auto_tags"):
            print("\nAuto-tags applied:")
            for tag, count in sorted(summary["auto_tags"].items(), key=lambda x: -x[1]):
                print(f"  {tag}: {count}")
        if summary.get("errors"):
            print(f"\nErrors ({len(summary['errors'])}):")
            for err in summary["errors"][:10]:
                print(f"  - {err}")
            if len(summary["errors"]) > 10:
                print(f"  ... and {len(summary['errors']) - 10} more")


if __name__ == "__main__":
    main()
