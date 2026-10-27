#!/usr/bin/env python3
"""Stream AES-256-GCM backup encryption. Requires cryptography (API image includes it)."""
import argparse
import os
from pathlib import Path
import tempfile

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

MAGIC = b'XMBACKUP1'
CHUNK = 1024 * 1024


def transform(mode, source, destination, key_path):
    key = bytes.fromhex(key_path.read_text().strip())
    if len(key) != 32:
        raise ValueError('The key must be 32 random bytes encoded as 64 hex characters.')
    if destination.exists():
        raise ValueError('Destination already exists; choose a new output path.')
    fd, temporary = tempfile.mkstemp(prefix='.xm-crypt-', dir=destination.parent)
    try:
        with source.open('rb') as reader, os.fdopen(fd, 'wb') as writer:
            if mode == 'encrypt':
                nonce = os.urandom(12)
                header = MAGIC + nonce
                worker = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
                worker.authenticate_additional_data(header)
                writer.write(header)
                while chunk := reader.read(CHUNK):
                    writer.write(worker.update(chunk))
                writer.write(worker.finalize())
                writer.write(worker.tag)
            else:
                header = reader.read(len(MAGIC) + 12)
                remaining = source.stat().st_size - len(header) - 16
                if not header.startswith(MAGIC) or len(header) != len(MAGIC) + 12 or remaining < 0:
                    raise ValueError('Not a supported backup file.')
                reader.seek(-16, os.SEEK_END)
                tag = reader.read(16)
                reader.seek(len(header))
                worker = Cipher(algorithms.AES(key), modes.GCM(header[-12:], tag)).decryptor()
                worker.authenticate_additional_data(header)
                while remaining:
                    chunk = reader.read(min(CHUNK, remaining))
                    if not chunk:
                        raise ValueError('Truncated backup file.')
                    writer.write(worker.update(chunk))
                    remaining -= len(chunk)
                writer.write(worker.finalize())  # authentication must pass before publishing plaintext
        os.link(temporary, destination)  # refuse an output created concurrently
    finally:
        Path(temporary).unlink(missing_ok=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['encrypt', 'decrypt'])
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--key-file', type=Path, required=True)
    args = parser.parse_args()
    transform(args.mode, args.input, args.output, args.key_file)
