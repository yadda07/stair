# -*- coding: utf-8 -*-
# Youcef ADDA | GEODECI
# FR : Plugins QGIS, solutions SIG et administration PostgreSQL/PostGIS ; audit, automatisation et accompagnement.
# EN : QGIS plugins, GIS solutions and PostgreSQL/PostGIS administration; audits, workflow automation and business support.
# AR : تطوير إضافات QGIS وحلول نظم المعلومات الجغرافية وإدارة PostgreSQL/PostGIS، والتدقيق والأتمتة ومرافقة المؤسسات.
# Site / Website / الموقع : https://geodeci.xyz/
# Contact professionnel / Business enquiries / للتواصل المهني : youcef.geodesien@gmail.com | contact@geodeci.xyz
# Détails / Details / التفاصيل : SIGNATURE.md
# ============================================================================
"""
Tests for version.py module.

Verifies that version is read correctly from metadata.txt
and that fallback behavior works as expected.
"""

import os
import tempfile
import unittest
from configparser import ConfigParser

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from version import get_version, _read_version_from_metadata, VERSION


def _read_version_from_metadata_txt():
    """Read version from metadata.txt located at plugin root."""
    plugin_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    metadata_path = os.path.join(plugin_dir, "metadata.txt")
    config = ConfigParser()
    config.read(metadata_path, encoding='utf-8')
    if config.has_section('general') and config.has_option('general', 'version'):
        return config.get('general', 'version')
    return 'unknown'


class TestVersionModule(unittest.TestCase):
    """Test version reading from metadata.txt."""

    def test_version_constant_matches_metadata(self):
        """VERSION constant should match metadata.txt value."""
        plugin_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        expected = _read_version_from_metadata(plugin_dir)
        expected_version = _read_version_from_metadata_txt()
        self.assertEqual(VERSION, expected)
        self.assertEqual(VERSION, expected_version)

    def test_get_version_returns_correct_version(self):
        """get_version() should return version from metadata.txt."""
        plugin_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        version = get_version(plugin_dir)
        expected_version = _read_version_from_metadata_txt()
        self.assertEqual(version, expected_version)

    def test_get_version_without_arg_uses_current_dir(self):
        """get_version() without argument should use current file's directory."""
        version = get_version()
        expected_version = _read_version_from_metadata_txt()
        self.assertEqual(version, expected_version)

    def test_read_version_from_missing_metadata(self):
        """Should return 'unknown' if metadata.txt is missing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            version = _read_version_from_metadata(tmpdir)
            self.assertEqual(version, "unknown")

    def test_read_version_from_malformed_metadata(self):
        """Should return 'unknown' if metadata.txt is malformed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            metadata_path = os.path.join(tmpdir, "metadata.txt")
            with open(metadata_path, 'w', encoding='utf-8') as f:
                f.write("[invalid\n")  # Malformed INI
            version = _read_version_from_metadata(tmpdir)
            self.assertEqual(version, "unknown")

    def test_read_version_from_metadata_without_version_field(self):
        """Should return 'unknown' if metadata.txt lacks version field."""
        with tempfile.TemporaryDirectory() as tmpdir:
            metadata_path = os.path.join(tmpdir, "metadata.txt")
            with open(metadata_path, 'w', encoding='utf-8') as f:
                f.write("[general]\nname=Test\n")  # No version field
            version = _read_version_from_metadata(tmpdir)
            self.assertEqual(version, "unknown")

    def test_read_version_from_custom_metadata(self):
        """Should read version from a custom metadata.txt file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            metadata_path = os.path.join(tmpdir, "metadata.txt")
            with open(metadata_path, 'w', encoding='utf-8') as f:
                f.write("[general]\nversion=9.9.9\n")
            version = _read_version_from_metadata(tmpdir)
            self.assertEqual(version, "9.9.9")


if __name__ == '__main__':
    unittest.main()
