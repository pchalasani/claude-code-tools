#!/usr/bin/env python3
"""Comprehensive pytest tests for dotenv_vault.py module."""

import subprocess
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, mock_open, patch

import pytest

from claude_code_tools.dotenv_vault import DotenvVault


# ============================================================================
# FIXTURES
# ============================================================================


@pytest.fixture
def mock_gpg_key():
    """Mock GPG key detection."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = Mock(
            stdout="sec   rsa3072/1234567890ABCDEF 2024-01-01\n",
            returncode=0
        )
        yield mock_run


@pytest.fixture
def vault(mock_gpg_key):
    """Create DotenvVault instance with mocked dependencies."""
    with patch("pathlib.Path.mkdir"):
        vault = DotenvVault()
        vault.gpg_key = "1234567890ABCDEF"
        return vault


@pytest.fixture
def mock_cwd():
    """Mock current working directory."""
    with patch("pathlib.Path.cwd") as mock:
        mock.return_value = Path("/fake/project/test-project")
        yield mock


@pytest.fixture
def mock_env_file():
    """Mock .env file existence and properties."""
    with patch("pathlib.Path.exists") as mock_exists, \
         patch("pathlib.Path.stat") as mock_stat:
        mock_exists.return_value = True
        mock_stat.return_value = Mock(st_mtime=1704067200.0)  # 2024-01-01
        yield mock_exists, mock_stat


# ============================================================================
# INITIALIZATION TESTS
# ============================================================================


class TestDotenvVaultInit:
    """Test DotenvVault initialization and GPG key detection."""

    def test_init_creates_vault_directory(self, mock_gpg_key):
        """Test that __init__ creates vault directory."""
        with patch("pathlib.Path.mkdir") as mock_mkdir:
            DotenvVault()
            mock_mkdir.assert_called_once_with(parents=True, exist_ok=True)

    def test_init_calls_ensure_gpg_key(self, mock_gpg_key):
        """Test that __init__ calls _ensure_gpg_key."""
        with patch("pathlib.Path.mkdir"):
            DotenvVault()
            mock_gpg_key.assert_called_once()

    def test_ensure_gpg_key_success(self):
        """Test successful GPG key detection."""
        with patch("subprocess.run") as mock_run, \
             patch("pathlib.Path.mkdir"):
            mock_run.return_value = Mock(
                stdout="sec   rsa3072/ABCDEF1234567890 2024-01-01\nuid   Test User\n",
                returncode=0
            )
            vault = DotenvVault()
            assert vault.gpg_key == "ABCDEF1234567890"

    def test_ensure_gpg_key_no_key_found(self):
        """Test GPG key detection when no key exists."""
        with patch("subprocess.run") as mock_run, \
             patch("pathlib.Path.mkdir"), \
             patch("click.echo") as mock_echo:
            mock_run.return_value = Mock(
                stdout="No keys found\n",
                returncode=0
            )
            with pytest.raises(SystemExit):
                DotenvVault()
            mock_echo.assert_called_once()
            assert "No GPG key found" in mock_echo.call_args[0][0]

    def test_ensure_gpg_key_gpg_not_found(self):
        """Test GPG key detection when gpg command fails."""
        with patch("subprocess.run") as mock_run, \
             patch("pathlib.Path.mkdir"), \
             patch("click.echo") as mock_echo:
            mock_run.side_effect = subprocess.CalledProcessError(1, "gpg")
            with pytest.raises(SystemExit):
                DotenvVault()
            mock_echo.assert_called_once()
            assert "GPG not found" in mock_echo.call_args[0][0]


# ============================================================================
# PROJECT NAME SANITIZATION TESTS
# ============================================================================


class TestProjectNameSanitization:
    """Test _project_name() method for security and sanitization."""

    def test_simple_project_name(self, vault, mock_cwd):
        """Test simple alphanumeric project name."""
        mock_cwd.return_value = Path("/fake/simple-project")
        assert vault._project_name() == "simple-project"

    def test_sanitize_forward_slash(self, vault, mock_cwd):
        """Test forward slash sanitization."""
        mock_cwd.return_value = Path("/fake/project/with/slashes")
        name = vault._project_name()
        assert "/" not in name
        assert name == "slashes"  # Path.name returns last component

    def test_sanitize_backslash(self, vault, mock_cwd):
        """Test backslash sanitization."""
        mock_cwd.return_value = Path("/fake/project\\with\\backslashes")
        name = vault._project_name()
        assert "\\" not in name

    def test_sanitize_parent_directory(self, vault, mock_cwd):
        """Test parent directory traversal sanitization."""
        mock_cwd.return_value = Path("/fake/../etc/passwd")
        name = vault._project_name()
        assert ".." not in name
        assert name == "passwd"  # Path.name returns last component

    def test_sanitize_special_characters(self, vault, mock_cwd):
        """Test special character sanitization."""
        mock_cwd.return_value = Path("/fake/project@#$%^&*()")
        name = vault._project_name()
        assert "@" not in name
        assert "#" not in name
        # @#$%^&*() = 9 characters
        assert name == "project_________"

    def test_preserve_allowed_characters(self, vault, mock_cwd):
        """Test that allowed characters are preserved."""
        mock_cwd.return_value = Path("/fake/project-name_v1.2")
        name = vault._project_name()
        assert name == "project-name_v1.2"

    def test_empty_project_name(self, vault, mock_cwd):
        """Test handling of empty project name."""
        # Create a mock path with empty name
        mock_path = Mock(spec=Path)
        mock_path.name = ""
        mock_cwd.return_value = mock_path
        name = vault._project_name()
        assert name == "unnamed_project"


# ============================================================================
# BACKUP PATH TESTS
# ============================================================================


class TestBackupPath:
    """Test _backup_path() method for path generation."""

    def test_backup_path_default_project(self, vault, mock_cwd):
        """Test backup path generation with default project name."""
        mock_cwd.return_value = Path("/fake/my-project")
        backup_path = vault._backup_path()
        assert backup_path.name == "my-project.env.encrypt"
        assert backup_path.parent == vault.vault_dir

    def test_backup_path_custom_project(self, vault):
        """Test backup path generation with custom project name."""
        backup_path = vault._backup_path(project="custom-project")
        assert backup_path.name == "custom-project.env.encrypt"
        assert backup_path.parent == vault.vault_dir

    def test_backup_path_sanitized_name(self, vault, mock_cwd):
        """Test backup path with sanitized project name."""
        mock_cwd.return_value = Path("/fake/project@special")
        backup_path = vault._backup_path()
        assert "@" not in backup_path.name
        assert backup_path.name.endswith(".env.encrypt")


# ============================================================================
# ENCRYPT METHOD TESTS
# ============================================================================


class TestEncrypt:
    """Test encrypt() method with various scenarios."""

    def test_encrypt_no_env_file(self, vault, mock_cwd):
        """Test encrypt when .env file doesn't exist."""
        with patch("pathlib.Path.exists", return_value=False), \
             patch("click.echo") as mock_echo:
            result = vault.encrypt()
            assert result is False
            mock_echo.assert_called_once()
            assert ".env not found" in mock_echo.call_args[0][0]

    def test_encrypt_success(self, vault, mock_cwd):
        """Test successful encryption."""
        mock_backup_path = Mock(spec=Path)
        mock_backup_path.exists.return_value = False

        with patch("pathlib.Path.exists", return_value=True), \
             patch.object(vault, "_backup_path", return_value=mock_backup_path), \
             patch("builtins.open", mock_open()), \
             patch("subprocess.run") as mock_run, \
             patch("click.echo") as mock_echo:
            mock_run.return_value = Mock(returncode=0)
            result = vault.encrypt()

            assert result is True
            mock_run.assert_called_once()
            cmd = mock_run.call_args[0][0]
            assert "sops" in cmd
            assert "--encrypt" in cmd
            assert "--pgp" in cmd
            assert vault.gpg_key in cmd
            mock_echo.assert_called()
            assert "Encrypted" in mock_echo.call_args[0][0]

    def test_encrypt_backup_exists_no_force(self, vault, mock_cwd):
        """Test encrypt when backup exists without force flag."""
        mock_backup_path = Mock(spec=Path)
        mock_backup_path.exists.return_value = True

        with patch("pathlib.Path.exists", return_value=True), \
             patch.object(vault, "_backup_path", return_value=mock_backup_path), \
             patch("click.echo") as mock_echo, \
             patch("click.confirm", return_value=False) as mock_confirm:
            result = vault.encrypt()

            assert result is False
            mock_echo.assert_called_once()
            assert "already exists" in mock_echo.call_args[0][0]
            mock_confirm.assert_called_once()

    def test_encrypt_backup_exists_with_confirmation(self, vault, mock_cwd):
        """Test encrypt when backup exists and user confirms overwrite."""
        mock_backup_path = Mock(spec=Path)
        mock_backup_path.exists.return_value = True
        mock_backup_path.name = "test.env.encrypt"

        with patch("pathlib.Path.exists", return_value=True), \
             patch.object(vault, "_backup_path", return_value=mock_backup_path), \
             patch("click.echo") as mock_echo, \
             patch("click.confirm", return_value=True), \
             patch("builtins.open", mock_open()), \
             patch("subprocess.run") as mock_run, \
             patch("datetime.datetime") as mock_datetime:

            mock_datetime.now.return_value.strftime.return_value = "20240101_120000"
            mock_run.return_value = Mock(returncode=0)

            result = vault.encrypt()

            assert result is True
            mock_backup_path.rename.assert_called_once()
            assert "backup-" in str(mock_backup_path.rename.call_args[0][0])

    def test_encrypt_with_force_flag(self, vault, mock_cwd):
        """Test encrypt with force flag bypasses confirmation."""
        mock_backup_path = Mock(spec=Path)
        mock_backup_path.exists.return_value = True

        with patch("pathlib.Path.exists", return_value=True), \
             patch.object(vault, "_backup_path", return_value=mock_backup_path), \
             patch("click.echo"), \
             patch("click.confirm") as mock_confirm, \
             patch("builtins.open", mock_open()), \
             patch("subprocess.run") as mock_run, \
             patch("datetime.datetime") as mock_datetime:

            mock_datetime.now.return_value.strftime.return_value = "20240101_120000"
            mock_run.return_value = Mock(returncode=0)

            result = vault.encrypt(force=True)

            assert result is True
            mock_confirm.assert_not_called()
            mock_backup_path.rename.assert_called_once()

    def test_encrypt_subprocess_failure(self, vault, mock_cwd):
        """Test encrypt when sops command fails."""
        mock_backup_path = Mock(spec=Path)
        mock_backup_path.exists.return_value = False

        with patch("pathlib.Path.exists", return_value=True), \
             patch.object(vault, "_backup_path", return_value=mock_backup_path), \
             patch("builtins.open", mock_open()), \
             patch("subprocess.run") as mock_run, \
             patch("click.echo") as mock_echo:
            mock_run.side_effect = subprocess.CalledProcessError(1, "sops")

            result = vault.encrypt()

            assert result is False
            mock_echo.assert_called()
            assert "Encryption failed" in mock_echo.call_args[0][0]


# ============================================================================
# DECRYPT METHOD TESTS
# ============================================================================


class TestDecrypt:
    """Test decrypt() method with various scenarios."""

    def test_decrypt_no_backup(self, vault, mock_cwd):
        """Test decrypt when backup doesn't exist."""
        with patch("pathlib.Path.exists", return_value=False), \
             patch("click.echo") as mock_echo:
            result = vault.decrypt()

            assert result is False
            mock_echo.assert_called_once()
            assert "not found" in mock_echo.call_args[0][0]

    def test_decrypt_success_no_existing_env(self, vault, mock_cwd):
        """Test successful decryption with no existing .env."""
        mock_backup_path = Mock(spec=Path)
        mock_backup_path.exists.return_value = True

        with patch("pathlib.Path.exists", return_value=False), \
             patch.object(vault, "_backup_path", return_value=mock_backup_path), \
             patch("builtins.open", mock_open()), \
             patch("subprocess.run") as mock_run, \
             patch("click.echo") as mock_echo:
            mock_run.return_value = Mock(returncode=0)

            result = vault.decrypt()

            assert result is True
            mock_run.assert_called_once()
            cmd = mock_run.call_args[0][0]
            assert "sops" in cmd
            assert "--decrypt" in cmd
            mock_echo.assert_called()
            assert "Decrypted" in mock_echo.call_args[0][0]

    def test_decrypt_backup_existing_env(self, vault, mock_cwd):
        """Test decryption backs up existing .env."""
        mock_backup_path = Mock(spec=Path)
        mock_backup_path.exists.return_value = True
        mock_backup_path.stat.return_value = Mock(st_mtime=1704067200.0)

        mock_env_path = Mock(spec=Path)
        mock_env_path.exists.return_value = True
        mock_env_path.stat.return_value = Mock(st_mtime=1704063600.0)  # Older

        with patch("pathlib.Path.exists", return_value=True), \
             patch.object(vault, "_backup_path", return_value=mock_backup_path), \
             patch("pathlib.Path.cwd") as mock_cwd_method, \
             patch("builtins.open", mock_open()), \
             patch("subprocess.run") as mock_run, \
             patch("click.echo") as mock_echo, \
             patch("datetime.datetime") as mock_datetime:

            mock_cwd_method.return_value = Path("/fake/project")
            mock_datetime.now.return_value.strftime.return_value = "20240101_120000"
            mock_run.return_value = Mock(returncode=0)

            with patch("pathlib.Path.__truediv__", return_value=mock_env_path):
                result = vault.decrypt()

            assert result is True
            mock_env_path.rename.assert_called_once()

    def test_decrypt_local_newer_no_confirmation(self, vault, mock_cwd):
        """Test decrypt when local .env is newer and user declines."""
        mock_backup_path = Mock(spec=Path)
        mock_backup_path.exists.return_value = True
        mock_backup_path.stat.return_value = Mock(st_mtime=1704063600.0)  # Older

        mock_env_path = Mock(spec=Path)
        mock_env_path.exists.return_value = True
        mock_env_path.stat.return_value = Mock(st_mtime=1704067200.0)  # Newer

        with patch.object(vault, "_backup_path", return_value=mock_backup_path), \
             patch("pathlib.Path.__truediv__", return_value=mock_env_path), \
             patch("click.echo") as mock_echo, \
             patch("click.confirm", return_value=False):
            result = vault.decrypt()

            assert result is False
            mock_echo.assert_called()
            assert "newer" in mock_echo.call_args[0][0]

    def test_decrypt_subprocess_failure(self, vault, mock_cwd):
        """Test decrypt when sops command fails."""
        mock_backup_path = Mock(spec=Path)
        mock_backup_path.exists.return_value = True

        with patch("pathlib.Path.exists", return_value=False), \
             patch.object(vault, "_backup_path", return_value=mock_backup_path), \
             patch("builtins.open", mock_open()), \
             patch("subprocess.run") as mock_run, \
             patch("click.echo") as mock_echo:
            mock_run.side_effect = subprocess.CalledProcessError(1, "sops")

            result = vault.decrypt()

            assert result is False
            mock_echo.assert_called()
            assert "Decryption failed" in mock_echo.call_args[0][0]


# ============================================================================
# LIST BACKUPS TESTS
# ============================================================================


class TestListBackups:
    """Test list_backups() method."""

    def test_list_backups_empty(self, vault):
        """Test list_backups when no backups exist."""
        with patch("pathlib.Path.glob", return_value=[]), \
             patch("click.echo") as mock_echo:
            vault.list_backups()

            mock_echo.assert_called_once()
            assert "No encrypted files" in mock_echo.call_args[0][0]

    def test_list_backups_with_files(self, vault):
        """Test list_backups with multiple backup files."""
        mock_backup1 = Mock(spec=Path)
        mock_backup1.name = "project1.env.encrypt"
        mock_backup1.stat.return_value = Mock(st_size=1024)
        mock_backup1.__lt__ = Mock(return_value=True)  # Make sortable

        mock_backup2 = Mock(spec=Path)
        mock_backup2.name = "project2.env.encrypt"
        mock_backup2.stat.return_value = Mock(st_size=2048)
        mock_backup2.__lt__ = Mock(return_value=False)  # Make sortable

        with patch("pathlib.Path.glob", return_value=[mock_backup1, mock_backup2]), \
             patch("click.echo") as mock_echo:
            vault.list_backups()

            assert mock_echo.call_count == 3  # Header + 2 files
            calls = [str(call) for call in mock_echo.call_args_list]
            assert any("project1" in str(call) for call in calls)
            assert any("project2" in str(call) for call in calls)
            assert any("1024 bytes" in str(call) for call in calls)


# ============================================================================
# STATUS METHOD TESTS
# ============================================================================


class TestStatus:
    """Test status() method for various sync states."""

    def test_status_neither_exists(self, vault, mock_cwd):
        """Test status when neither .env nor backup exists."""
        mock_backup_path = Mock(spec=Path)
        mock_backup_path.exists.return_value = False

        with patch("pathlib.Path.exists", return_value=False), \
             patch.object(vault, "_backup_path", return_value=mock_backup_path), \
             patch("click.echo") as mock_echo:
            result = vault.status()

            assert result == "neither"
            mock_echo.assert_called_once()
            assert "Neither" in mock_echo.call_args[0][0]

    def test_status_local_only(self, vault, mock_cwd):
        """Test status when only local .env exists."""
        mock_backup_path = Mock(spec=Path)
        mock_backup_path.exists.return_value = False

        with patch("pathlib.Path.exists", return_value=True), \
             patch.object(vault, "_backup_path", return_value=mock_backup_path), \
             patch("click.echo") as mock_echo:
            result = vault.status()

            assert result == "local_only"
            mock_echo.assert_called_once()
            assert "no backup" in mock_echo.call_args[0][0]

    def test_status_backup_only(self, vault, mock_cwd):
        """Test status when only backup exists."""
        mock_backup_path = Mock(spec=Path)
        mock_backup_path.exists.return_value = True

        with patch("pathlib.Path.exists", return_value=False), \
             patch.object(vault, "_backup_path", return_value=mock_backup_path), \
             patch("click.echo") as mock_echo:
            result = vault.status()

            assert result == "backup_only"
            mock_echo.assert_called_once()
            assert "no local .env" in mock_echo.call_args[0][0]

    def test_status_identical(self, vault, mock_cwd):
        """Test status when .env and backup have same timestamp."""
        mock_backup_path = Mock(spec=Path)
        mock_backup_path.exists.return_value = True
        mock_backup_path.stat.return_value = Mock(st_mtime=1704067200.0)

        mock_env_path = Mock(spec=Path)
        mock_env_path.exists.return_value = True
        mock_env_path.stat.return_value = Mock(st_mtime=1704067200.0)

        # Create a Path class mock that preserves cwd() method
        original_path = Path
        def path_side_effect(arg):
            if arg == ".env":
                return mock_env_path
            return original_path(arg)

        mock_path_class = Mock(side_effect=path_side_effect)
        mock_path_class.cwd = original_path.cwd

        with patch.object(vault, "_backup_path", return_value=mock_backup_path), \
             patch("claude_code_tools.dotenv_vault.Path", mock_path_class), \
             patch("click.echo") as mock_echo:
            result = vault.status()

            assert result == "identical"
            assert "identical" in str(mock_echo.call_args[0][0]).lower()

    def test_status_local_newer(self, vault, mock_cwd):
        """Test status when local .env is newer than backup."""
        mock_backup_path = Mock(spec=Path)
        mock_backup_path.exists.return_value = True
        mock_backup_path.stat.return_value = Mock(st_mtime=1704063600.0)  # Older

        mock_env_path = Mock(spec=Path)
        mock_env_path.exists.return_value = True
        mock_env_path.stat.return_value = Mock(st_mtime=1704067200.0)  # Newer

        # Create a Path class mock that preserves cwd() method
        original_path = Path
        def path_side_effect(arg):
            if arg == ".env":
                return mock_env_path
            return original_path(arg)

        mock_path_class = Mock(side_effect=path_side_effect)
        mock_path_class.cwd = original_path.cwd

        with patch.object(vault, "_backup_path", return_value=mock_backup_path), \
             patch("claude_code_tools.dotenv_vault.Path", mock_path_class), \
             patch("click.echo") as mock_echo, \
             patch("datetime.datetime") as mock_datetime:
            mock_datetime.fromtimestamp.side_effect = lambda ts: datetime.fromtimestamp(ts)

            result = vault.status()

            assert result == "local_newer"
            assert mock_echo.call_count == 3  # Status + 2 timestamps
            assert "NEWER" in str(mock_echo.call_args_list[0])

    def test_status_backup_newer(self, vault, mock_cwd):
        """Test status when backup is newer than local .env."""
        mock_backup_path = Mock(spec=Path)
        mock_backup_path.exists.return_value = True
        mock_backup_path.stat.return_value = Mock(st_mtime=1704067200.0)  # Newer

        mock_env_path = Mock(spec=Path)
        mock_env_path.exists.return_value = True
        mock_env_path.stat.return_value = Mock(st_mtime=1704063600.0)  # Older

        # Create a Path class mock that preserves cwd() method
        original_path = Path
        def path_side_effect(arg):
            if arg == ".env":
                return mock_env_path
            return original_path(arg)

        mock_path_class = Mock(side_effect=path_side_effect)
        mock_path_class.cwd = original_path.cwd

        with patch.object(vault, "_backup_path", return_value=mock_backup_path), \
             patch("claude_code_tools.dotenv_vault.Path", mock_path_class), \
             patch("click.echo") as mock_echo, \
             patch("datetime.datetime") as mock_datetime:
            mock_datetime.fromtimestamp.side_effect = lambda ts: datetime.fromtimestamp(ts)

            result = vault.status()

            assert result == "backup_newer"
            assert "NEWER" in str(mock_echo.call_args_list[0])


# ============================================================================
# SYNC METHOD TESTS
# ============================================================================


class TestSync:
    """Test sync() method with various scenarios and directions."""

    def test_sync_identical_no_action(self, vault):
        """Test sync when already in sync."""
        with patch.object(vault, "status", return_value="identical"), \
             patch("click.echo") as mock_echo:
            result = vault.sync()

            assert result is True
            mock_echo.assert_called()
            assert "Already in sync" in mock_echo.call_args[0][0]

    def test_sync_local_only_encrypts(self, vault):
        """Test sync encrypts when only local .env exists."""
        with patch.object(vault, "status", return_value="local_only"), \
             patch.object(vault, "encrypt", return_value=True) as mock_encrypt, \
             patch("click.echo"):
            result = vault.sync()

            assert result is True
            mock_encrypt.assert_called_once()

    def test_sync_backup_only_decrypts(self, vault):
        """Test sync decrypts when only backup exists."""
        with patch.object(vault, "status", return_value="backup_only"), \
             patch.object(vault, "decrypt", return_value=True) as mock_decrypt, \
             patch("click.echo"):
            result = vault.sync()

            assert result is True
            mock_decrypt.assert_called_once()

    def test_sync_local_newer_encrypts(self, vault):
        """Test sync encrypts when local is newer."""
        with patch.object(vault, "status", return_value="local_newer"), \
             patch.object(vault, "encrypt", return_value=True) as mock_encrypt, \
             patch("click.echo"):
            result = vault.sync()

            assert result is True
            mock_encrypt.assert_called_once()

    def test_sync_local_newer_pull_with_confirmation(self, vault):
        """Test sync with --pull when local is newer and user confirms."""
        with patch.object(vault, "status", return_value="local_newer"), \
             patch.object(vault, "decrypt", return_value=True) as mock_decrypt, \
             patch("click.echo"), \
             patch("click.confirm", return_value=True):
            result = vault.sync(direction="pull")

            assert result is True
            mock_decrypt.assert_called_once()

    def test_sync_local_newer_pull_no_confirmation(self, vault):
        """Test sync with --pull when local is newer and user declines."""
        with patch.object(vault, "status", return_value="local_newer"), \
             patch.object(vault, "decrypt") as mock_decrypt, \
             patch("click.echo"), \
             patch("click.confirm", return_value=False):
            result = vault.sync(direction="pull")

            assert result is None  # No return when user declines
            mock_decrypt.assert_not_called()

    def test_sync_backup_newer_decrypts(self, vault):
        """Test sync decrypts when backup is newer."""
        with patch.object(vault, "status", return_value="backup_newer"), \
             patch.object(vault, "decrypt", return_value=True) as mock_decrypt, \
             patch("click.echo"):
            result = vault.sync()

            assert result is True
            mock_decrypt.assert_called_once()

    def test_sync_backup_newer_push_with_confirmation(self, vault):
        """Test sync with --push when backup is newer and user confirms."""
        with patch.object(vault, "status", return_value="backup_newer"), \
             patch.object(vault, "encrypt", return_value=True) as mock_encrypt, \
             patch("click.echo"), \
             patch("click.confirm", return_value=True):
            result = vault.sync(direction="push")

            assert result is True
            mock_encrypt.assert_called_once()

    def test_sync_backup_newer_push_no_confirmation(self, vault):
        """Test sync with --push when backup is newer and user declines."""
        with patch.object(vault, "status", return_value="backup_newer"), \
             patch.object(vault, "encrypt") as mock_encrypt, \
             patch("click.echo"), \
             patch("click.confirm", return_value=False):
            result = vault.sync(direction="push")

            assert result is None  # No return when user declines
            mock_encrypt.assert_not_called()

    def test_sync_neither_exists_no_action(self, vault):
        """Test sync when neither file exists."""
        with patch.object(vault, "status", return_value="neither"), \
             patch("click.echo"):
            # Sync should return None (no action) when neither exists
            result = vault.sync()
            assert result is None


# ============================================================================
# INTEGRATION TESTS
# ============================================================================


class TestIntegration:
    """Integration tests combining multiple methods."""

    def test_full_encrypt_decrypt_cycle(self, vault, mock_cwd):
        """Test complete encryption and decryption cycle."""
        # Setup
        mock_backup_path_encrypt = Mock(spec=Path)
        mock_backup_path_encrypt.exists.return_value = False  # Backup doesn't exist for encrypt

        mock_backup_path_decrypt = Mock(spec=Path)
        mock_backup_path_decrypt.exists.return_value = True  # Backup exists for decrypt
        mock_backup_path_decrypt.stat.return_value = Mock(st_mtime=1704067200.0)

        mock_env_path_decrypt = Mock(spec=Path)
        mock_env_path_decrypt.exists.return_value = False  # .env doesn't exist for decrypt

        mock_cwd_obj = Mock(spec=Path)

        def path_side_effect_encrypt(arg):
            if arg == ".env":
                mock_env = Mock(spec=Path)
                mock_env.exists.return_value = True
                return mock_env
            return Path(arg)

        def path_side_effect_decrypt(arg):
            if arg == ".env":
                return mock_env_path_decrypt
            return Path(arg)

        with patch("builtins.open", mock_open()), \
             patch("subprocess.run") as mock_run, \
             patch("click.echo"):
            mock_run.return_value = Mock(returncode=0)

            # Encrypt with non-existing backup
            with patch.object(vault, "_backup_path", return_value=mock_backup_path_encrypt), \
                 patch("claude_code_tools.dotenv_vault.Path", side_effect=path_side_effect_encrypt):
                encrypt_result = vault.encrypt()
                assert encrypt_result is True

            # Decrypt with existing backup
            mock_cwd_obj.__truediv__ = Mock(return_value=mock_env_path_decrypt)
            with patch.object(vault, "_backup_path", return_value=mock_backup_path_decrypt), \
                 patch("claude_code_tools.dotenv_vault.Path.cwd", return_value=mock_cwd_obj):
                decrypt_result = vault.decrypt()
                assert decrypt_result is True

            # Should have called subprocess twice
            assert mock_run.call_count == 2

    def test_status_then_sync(self, vault):
        """Test checking status then performing sync."""
        with patch.object(vault, "status", return_value="local_newer") as mock_status, \
             patch.object(vault, "encrypt", return_value=True) as mock_encrypt, \
             patch("click.echo"):
            # Check status
            status = vault.status()
            assert status == "local_newer"

            # Perform sync
            result = vault.sync()
            assert result is True

            # Verify both were called
            assert mock_status.call_count == 2  # Once direct, once in sync
            mock_encrypt.assert_called_once()


# ============================================================================
# EDGE CASE TESTS
# ============================================================================


class TestEdgeCases:
    """Test edge cases and error conditions."""

    def test_vault_directory_creation_failure(self):
        """Test handling when vault directory cannot be created."""
        with patch("subprocess.run") as mock_run, \
             patch("pathlib.Path.mkdir") as mock_mkdir:
            mock_run.return_value = Mock(
                stdout="sec   rsa3072/1234567890ABCDEF 2024-01-01\n",
                returncode=0
            )
            mock_mkdir.side_effect = PermissionError("Permission denied")

            with pytest.raises(PermissionError):
                DotenvVault()

    def test_encrypt_with_very_long_project_name(self, vault, mock_cwd):
        """Test encryption with very long project name."""
        long_name = "a" * 300
        mock_cwd.return_value = Path(f"/fake/{long_name}")

        mock_backup_path = Mock(spec=Path)
        mock_backup_path.exists.return_value = False

        with patch("pathlib.Path.exists", return_value=True), \
             patch.object(vault, "_backup_path", return_value=mock_backup_path), \
             patch("builtins.open", mock_open()), \
             patch("subprocess.run") as mock_run, \
             patch("click.echo"):
            mock_run.return_value = Mock(returncode=0)

            result = vault.encrypt()
            assert result is True

    def test_subprocess_timeout(self, vault, mock_cwd):
        """Test handling of subprocess timeout."""
        mock_backup_path = Mock(spec=Path)
        mock_backup_path.exists.return_value = False

        with patch("pathlib.Path.exists", return_value=True), \
             patch.object(vault, "_backup_path", return_value=mock_backup_path), \
             patch("builtins.open", mock_open()), \
             patch("subprocess.run") as mock_run, \
             patch("click.echo") as mock_echo:
            mock_run.side_effect = subprocess.TimeoutExpired("sops", 30)

            with pytest.raises(subprocess.TimeoutExpired):
                vault.encrypt()

    def test_unicode_in_project_name(self, vault, mock_cwd):
        """Test project name with unicode characters."""
        mock_cwd.return_value = Path("/fake/project-español-日本語")
        name = vault._project_name()
        # Unicode characters should be sanitized to underscores
        assert "español" not in name or all(c.isascii() for c in name)
