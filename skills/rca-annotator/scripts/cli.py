#!/usr/bin/env python3
"""
RCA-Annotator CLI - Command-line interface for jumpbox synchronization.

This script provides commands to download analysis files from jumpbox
and upload annotation_draft.json back to jumpbox.

Usage:
    python scripts/cli.py download --job-id <job_id>
    python scripts/cli.py upload --job-id <job_id>
"""

import argparse
import sys

from jumpbox_io import download_from_jumpbox, upload_to_jumpbox


def cmd_download(args: argparse.Namespace) -> int:
    """
    Download analysis files from jumpbox.

    Args:
        args: Parsed command-line arguments

    Returns:
        0 on success, 1 on failure
    """
    print(f"Downloading analysis files for job {args.job_id}...")

    if download_from_jumpbox(args.job_id):
        print(f"\n Analysis files ready at .analysis/{args.job_id}/")
        return 0
    else:
        print("\n Failed to download analysis files")
        print("  Check JUMPBOX_URI environment variable and SSH configuration")
        return 1


def cmd_upload(args: argparse.Namespace) -> int:
    """
    Upload annotation_draft.json to jumpbox.

    Args:
        args: Parsed command-line arguments

    Returns:
        0 on success, 1 on failure
    """
    print(f"Uploading annotation for job {args.job_id}...")

    if upload_to_jumpbox(args.job_id):
        print("\n Annotation uploaded successfully")
        return 0
    else:
        print("\n Failed to upload annotation")
        return 1


def main():
    """Main entry point for CLI."""
    parser = argparse.ArgumentParser(
        description="RCA-Annotator Jumpbox Synchronization CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Download analysis files from jumpbox
  python scripts/cli.py download --job-id 1234567

  # Upload annotation to jumpbox
  python scripts/cli.py upload --job-id 1234567

Environment Variables:
  JUMPBOX_URI    SSH connection string (format: "user@host -p port")
                 If not set, uses local .analysis/ directory only
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Download command
    download_parser = subparsers.add_parser(
        "download",
        help="Download analysis files from jumpbox",
    )
    download_parser.add_argument(
        "--job-id",
        required=True,
        help="Job ID to download analysis files for",
    )

    # Upload command
    upload_parser = subparsers.add_parser(
        "upload",
        help="Upload annotation_draft.json to jumpbox",
    )
    upload_parser.add_argument(
        "--job-id",
        required=True,
        help="Job ID to upload annotation for",
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    # Execute command
    if args.command == "download":
        return cmd_download(args)
    elif args.command == "upload":
        return cmd_upload(args)
    else:
        print(f"Unknown command: {args.command}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
