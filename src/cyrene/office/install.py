"""Prepare and optionally trust the local HTTPS certificate for Office."""

from __future__ import annotations

import argparse

from cyrene.office.gateway import OfficeGatewayFiles
from cyrene.office.installation import install_powerpoint_addin, trust_certificate


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trust", action="store_true", help="Add the generated localhost certificate to the current user's trust store.")
    args = parser.parse_args()
    files = OfficeGatewayFiles()
    files.ensure()
    if args.trust:
        trust_certificate(files)
    install_powerpoint_addin(files, trust=False)
    print(f"Certificate: {files.cert_path}")
    print(f"PowerPoint manifest: {files.manifest_path}")
    print(f"Gateway URL: {files.base_url}")


if __name__ == "__main__":
    main()
