"""Focused socket security checks; no server, database or live messages."""

import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from runtime_socket import private_socket_path


class RuntimeSocketTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.socket = socket.socket(socket.AF_UNIX)
        self.addCleanup(self.socket.close)
        self.target = self.directory / "actual.sock"
        self.socket.bind(str(self.target))
        self.target.chmod(0o600)
        self.links = self.directory / "links"
        self.links.mkdir(mode=0o700)
        self.link = self.links / "codex.sock"
        self.link.symlink_to(self.target)

    def test_private_direct_and_linked_socket(self):
        self.assertEqual(private_socket_path(self.target), self.target)
        self.assertEqual(private_socket_path(self.link), self.target)

    def test_public_socket_rejected(self):
        self.target.chmod(0o660)
        for path in (self.target, self.link):
            with self.assertRaises(ValueError):
                private_socket_path(path)

    def test_public_link_directory_rejected(self):
        self.links.chmod(0o755)
        with self.assertRaises(ValueError):
            private_socket_path(self.link)

    def test_public_target_directory_rejected(self):
        self.directory.chmod(0o755)
        with self.assertRaises(ValueError):
            private_socket_path(self.link)

    def test_foreign_owner_rejected(self):
        uid = os.getuid()
        with patch("runtime_socket.os.getuid", return_value=uid + 1):
            for path in (self.target, self.link):
                with self.assertRaises(ValueError):
                    private_socket_path(path)

    def test_non_socket_and_broken_link_rejected(self):
        with self.assertRaises(ValueError):
            private_socket_path(self.links)
        self.target.unlink()
        with self.assertRaises(FileNotFoundError):
            private_socket_path(self.link)


if __name__ == "__main__":
    unittest.main()
