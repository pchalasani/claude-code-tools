#!/usr/bin/env python3
"""
Comprehensive pytest tests for env_safe.py

Tests cover:
- Main functionality (parse_env_file, list_keys, check_key, count_variables, validate_syntax)
- Error handling (FileNotFoundError, malformed files, invalid syntax)
- Edge cases (empty files, comments, empty values, special characters)
- Input validation (invalid key names, missing separators)
"""

import pytest
import sys
from pathlib import Path
import os

# Import the module under test
from claude_code_tools.env_safe import (
    parse_env_file,
    list_keys,
    check_key,
    count_variables,
    validate_syntax,
    main
)


class TestParseEnvFile:
    """Tests for parse_env_file function"""

    def test_parse_valid_env_file(self, tmp_path):
        """Test parsing a valid .env file with various formats"""
        env_content = """
# Comment line
API_KEY=secret123
DATABASE_URL=postgres://localhost:5432/db
EMPTY_VAR=
QUOTED_EMPTY=""
SINGLE_QUOTED=''
PORT=8080
DEBUG=true
"""
        env_file = tmp_path / ".env"
        env_file.write_text(env_content)

        result = parse_env_file(env_file)

        # Check that all keys are parsed
        keys = [key for key, _ in result]
        assert "API_KEY" in keys
        assert "DATABASE_URL" in keys
        assert "EMPTY_VAR" in keys
        assert "QUOTED_EMPTY" in keys
        assert "SINGLE_QUOTED" in keys
        assert "PORT" in keys
        assert "DEBUG" in keys

        # Check has_value status
        result_dict = dict(result)
        assert result_dict["API_KEY"] is True  # Has value
        assert result_dict["DATABASE_URL"] is True  # Has value
        assert result_dict["EMPTY_VAR"] is False  # Empty
        assert result_dict["QUOTED_EMPTY"] is False  # Empty quotes
        assert result_dict["SINGLE_QUOTED"] is False  # Empty quotes
        assert result_dict["PORT"] is True  # Has value
        assert result_dict["DEBUG"] is True  # Has value

    def test_parse_empty_file(self, tmp_path):
        """Test parsing an empty .env file"""
        env_file = tmp_path / ".env"
        env_file.write_text("")

        result = parse_env_file(env_file)

        assert result == []

    def test_parse_comments_only(self, tmp_path):
        """Test parsing a file with only comments"""
        env_content = """
# This is a comment
# Another comment
  # Indented comment
"""
        env_file = tmp_path / ".env"
        env_file.write_text(env_content)

        result = parse_env_file(env_file)

        assert result == []

    def test_parse_with_spaces(self, tmp_path):
        """Test parsing with various whitespace"""
        env_content = """
KEY1 = value1
KEY2= value2
KEY3 =value3
KEY4=value4
"""
        env_file = tmp_path / ".env"
        env_file.write_text(env_content)

        result = parse_env_file(env_file)

        keys = [key for key, _ in result]
        assert len(keys) == 4
        assert all(has_value for _, has_value in result)

    def test_parse_special_characters_in_values(self, tmp_path):
        """Test parsing values with special characters"""
        env_content = """
URL=http://example.com/path?param=value&other=123
JSON={"key": "value", "nested": {"data": true}}
SPECIAL_CHARS=!@#$%^&*()_+-=[]{}|;:,.<>?
"""
        env_file = tmp_path / ".env"
        env_file.write_text(env_content)

        result = parse_env_file(env_file)

        keys = [key for key, _ in result]
        assert "URL" in keys
        assert "JSON" in keys
        assert "SPECIAL_CHARS" in keys
        assert all(has_value for _, has_value in result)

    def test_parse_malformed_lines(self, tmp_path, capsys):
        """Test parsing with malformed lines (should warn but continue)"""
        env_content = """
VALID_KEY=value
123INVALID=value
-INVALID=value
VALID_KEY2=value2
"""
        env_file = tmp_path / ".env"
        env_file.write_text(env_content)

        result = parse_env_file(env_file)

        # Should parse valid keys and warn about invalid ones
        keys = [key for key, _ in result]
        assert "VALID_KEY" in keys
        assert "VALID_KEY2" in keys
        assert "123INVALID" not in keys
        assert "-INVALID" not in keys

        # Check that warnings were printed
        captured = capsys.readouterr()
        assert "Warning" in captured.err

    def test_parse_underscore_and_numbers_in_keys(self, tmp_path):
        """Test parsing valid keys with underscores and numbers"""
        env_content = """
VAR_NAME=value
VAR_NAME_2=value
_PRIVATE_VAR=value
VAR123=value
VAR_123_ABC=value
"""
        env_file = tmp_path / ".env"
        env_file.write_text(env_content)

        result = parse_env_file(env_file)

        keys = [key for key, _ in result]
        assert "VAR_NAME" in keys
        assert "VAR_NAME_2" in keys
        assert "_PRIVATE_VAR" in keys
        assert "VAR123" in keys
        assert "VAR_123_ABC" in keys

    def test_parse_file_not_found(self):
        """Test that FileNotFoundError is raised for non-existent file"""
        non_existent = Path("/tmp/non_existent_env_file_12345.env")

        with pytest.raises(FileNotFoundError):
            parse_env_file(non_existent)

    def test_parse_multiline_values(self, tmp_path):
        """Test that multiline values are handled (first line only)"""
        env_content = """
SINGLE_LINE=value1
POTENTIAL_MULTILINE=line1
line2_should_be_ignored
NEXT_VAR=value3
"""
        env_file = tmp_path / ".env"
        env_file.write_text(env_content)

        result = parse_env_file(env_file)

        keys = [key for key, _ in result]
        # Should parse SINGLE_LINE, POTENTIAL_MULTILINE, and NEXT_VAR
        assert "SINGLE_LINE" in keys
        assert "POTENTIAL_MULTILINE" in keys
        assert "NEXT_VAR" in keys


class TestListKeys:
    """Tests for list_keys function"""

    def test_list_keys_basic(self, tmp_path, capsys):
        """Test basic key listing"""
        env_content = """
API_KEY=secret
DATABASE_URL=postgres://localhost
PORT=8080
"""
        env_file = tmp_path / ".env"
        env_file.write_text(env_content)

        list_keys(env_file, show_status=False)

        captured = capsys.readouterr()
        assert "API_KEY" in captured.out
        assert "DATABASE_URL" in captured.out
        assert "PORT" in captured.out
        # Should be sorted
        lines = captured.out.strip().split('\n')
        assert lines == sorted(lines)

    def test_list_keys_with_status(self, tmp_path, capsys):
        """Test key listing with status"""
        env_content = """
WITH_VALUE=something
EMPTY_VALUE=
ANOTHER_VALUE=data
"""
        env_file = tmp_path / ".env"
        env_file.write_text(env_content)

        list_keys(env_file, show_status=True)

        captured = capsys.readouterr()
        assert "KEY" in captured.out
        assert "STATUS" in captured.out
        assert "WITH_VALUE" in captured.out
        assert "defined" in captured.out
        assert "EMPTY_VALUE" in captured.out
        assert "empty" in captured.out

    def test_list_keys_empty_file(self, tmp_path, capsys):
        """Test listing keys from empty file"""
        env_file = tmp_path / ".env"
        env_file.write_text("")

        list_keys(env_file, show_status=False)

        captured = capsys.readouterr()
        assert "No environment variables found" in captured.out

    def test_list_keys_file_not_found(self, tmp_path, capsys):
        """Test listing keys from non-existent file exits with error"""
        non_existent = tmp_path / "non_existent.env"

        with pytest.raises(SystemExit) as excinfo:
            list_keys(non_existent, show_status=False)

        assert excinfo.value.code == 1
        captured = capsys.readouterr()
        assert "Error" in captured.err

    def test_list_keys_sorting(self, tmp_path, capsys):
        """Test that keys are sorted alphabetically"""
        env_content = """
ZEBRA=value
ALPHA=value
CHARLIE=value
BRAVO=value
"""
        env_file = tmp_path / ".env"
        env_file.write_text(env_content)

        list_keys(env_file, show_status=False)

        captured = capsys.readouterr()
        lines = captured.out.strip().split('\n')
        assert lines == ["ALPHA", "BRAVO", "CHARLIE", "ZEBRA"]


class TestCheckKey:
    """Tests for check_key function"""

    def test_check_key_exists_with_value(self, tmp_path, capsys):
        """Test checking a key that exists with a value"""
        env_content = """
API_KEY=secret123
DATABASE_URL=postgres://localhost
"""
        env_file = tmp_path / ".env"
        env_file.write_text(env_content)

        with pytest.raises(SystemExit) as excinfo:
            check_key(env_file, "API_KEY")

        assert excinfo.value.code == 0
        captured = capsys.readouterr()
        assert "✓" in captured.out
        assert "API_KEY is defined with a value" in captured.out

    def test_check_key_exists_but_empty(self, tmp_path, capsys):
        """Test checking a key that exists but is empty"""
        env_content = """
EMPTY_KEY=
ANOTHER_KEY=value
"""
        env_file = tmp_path / ".env"
        env_file.write_text(env_content)

        with pytest.raises(SystemExit) as excinfo:
            check_key(env_file, "EMPTY_KEY")

        assert excinfo.value.code == 0
        captured = capsys.readouterr()
        assert "⚠" in captured.out
        assert "EMPTY_KEY is defined but empty" in captured.out

    def test_check_key_not_found(self, tmp_path, capsys):
        """Test checking a key that doesn't exist"""
        env_content = """
API_KEY=secret
DATABASE_URL=postgres://localhost
"""
        env_file = tmp_path / ".env"
        env_file.write_text(env_content)

        with pytest.raises(SystemExit) as excinfo:
            check_key(env_file, "NON_EXISTENT_KEY")

        assert excinfo.value.code == 1
        captured = capsys.readouterr()
        assert "✗" in captured.out
        assert "NON_EXISTENT_KEY is not defined" in captured.out

    def test_check_key_file_not_found(self, tmp_path, capsys):
        """Test checking key in non-existent file"""
        non_existent = tmp_path / "non_existent.env"

        with pytest.raises(SystemExit) as excinfo:
            check_key(non_existent, "ANY_KEY")

        assert excinfo.value.code == 1
        captured = capsys.readouterr()
        assert "Error" in captured.err

    def test_check_key_case_sensitive(self, tmp_path, capsys):
        """Test that key checking is case sensitive"""
        env_content = """
api_key=secret
API_KEY=secret
"""
        env_file = tmp_path / ".env"
        env_file.write_text(env_content)

        # Check lowercase
        with pytest.raises(SystemExit) as excinfo:
            check_key(env_file, "api_key")
        assert excinfo.value.code == 0

        # Check uppercase
        with pytest.raises(SystemExit) as excinfo:
            check_key(env_file, "API_KEY")
        assert excinfo.value.code == 0

        # Check different case - should not exist
        with pytest.raises(SystemExit) as excinfo:
            check_key(env_file, "Api_Key")
        assert excinfo.value.code == 1


class TestCountVariables:
    """Tests for count_variables function"""

    def test_count_variables_basic(self, tmp_path, capsys):
        """Test counting variables in a normal file"""
        env_content = """
KEY1=value1
KEY2=value2
KEY3=value3
"""
        env_file = tmp_path / ".env"
        env_file.write_text(env_content)

        count_variables(env_file)

        captured = capsys.readouterr()
        assert "Total variables: 3" in captured.out
        assert "With values: 3" in captured.out
        assert "Empty: 0" in captured.out

    def test_count_variables_mixed(self, tmp_path, capsys):
        """Test counting with mix of empty and filled variables"""
        env_content = """
KEY1=value1
KEY2=
KEY3=value3
KEY4=
KEY5=value5
"""
        env_file = tmp_path / ".env"
        env_file.write_text(env_content)

        count_variables(env_file)

        captured = capsys.readouterr()
        assert "Total variables: 5" in captured.out
        assert "With values: 3" in captured.out
        assert "Empty: 2" in captured.out

    def test_count_variables_empty_file(self, tmp_path, capsys):
        """Test counting in empty file"""
        env_file = tmp_path / ".env"
        env_file.write_text("")

        count_variables(env_file)

        captured = capsys.readouterr()
        assert "Total variables: 0" in captured.out
        # Should not show breakdown for 0 variables
        assert "With values:" not in captured.out

    def test_count_variables_comments_ignored(self, tmp_path, capsys):
        """Test that comments are not counted"""
        env_content = """
# Comment line
KEY1=value1
# Another comment
KEY2=value2
"""
        env_file = tmp_path / ".env"
        env_file.write_text(env_content)

        count_variables(env_file)

        captured = capsys.readouterr()
        assert "Total variables: 2" in captured.out

    def test_count_variables_file_not_found(self, tmp_path, capsys):
        """Test counting in non-existent file"""
        non_existent = tmp_path / "non_existent.env"

        with pytest.raises(SystemExit) as excinfo:
            count_variables(non_existent)

        assert excinfo.value.code == 1
        captured = capsys.readouterr()
        assert "Error" in captured.err


class TestValidateSyntax:
    """Tests for validate_syntax function"""

    def test_validate_valid_syntax(self, tmp_path, capsys):
        """Test validating a file with valid syntax"""
        env_content = """
# Valid .env file
API_KEY=secret
DATABASE_URL=postgres://localhost
PORT=8080
DEBUG=true
"""
        env_file = tmp_path / ".env"
        env_file.write_text(env_content)

        validate_syntax(env_file)

        captured = capsys.readouterr()
        assert "✓ Syntax valid" in captured.out
        assert "4 variables defined" in captured.out

    def test_validate_invalid_key_names(self, tmp_path, capsys):
        """Test validating file with invalid key names"""
        env_content = """
VALID_KEY=value
123INVALID=value
-INVALID=value
VALID_KEY2=value
"""
        env_file = tmp_path / ".env"
        env_file.write_text(env_content)

        with pytest.raises(SystemExit) as excinfo:
            validate_syntax(env_file)

        assert excinfo.value.code == 1
        captured = capsys.readouterr()
        assert "✗" in captured.out
        assert "syntax issue" in captured.out
        assert "Invalid key format" in captured.out

    def test_validate_missing_equals(self, tmp_path, capsys):
        """Test validating file with missing equals signs"""
        env_content = """
VALID_KEY=value
MISSING_EQUALS
ANOTHER_VALID=value
"""
        env_file = tmp_path / ".env"
        env_file.write_text(env_content)

        with pytest.raises(SystemExit) as excinfo:
            validate_syntax(env_file)

        assert excinfo.value.code == 1
        captured = capsys.readouterr()
        assert "✗" in captured.out
        assert "Missing '=' separator" in captured.out

    def test_validate_empty_file(self, tmp_path, capsys):
        """Test validating empty file"""
        env_file = tmp_path / ".env"
        env_file.write_text("")

        validate_syntax(env_file)

        captured = capsys.readouterr()
        assert "✓ Syntax valid (0 variables defined)" in captured.out

    def test_validate_comments_only(self, tmp_path, capsys):
        """Test validating file with only comments"""
        env_content = """
# This is a comment
# Another comment
  # Indented comment
"""
        env_file = tmp_path / ".env"
        env_file.write_text(env_content)

        validate_syntax(env_file)

        captured = capsys.readouterr()
        assert "✓ Syntax valid (0 variables defined)" in captured.out

    def test_validate_file_not_found(self, tmp_path, capsys):
        """Test validating non-existent file"""
        non_existent = tmp_path / "non_existent.env"

        with pytest.raises(SystemExit) as excinfo:
            validate_syntax(non_existent)

        assert excinfo.value.code == 1
        captured = capsys.readouterr()
        assert "Error: File not found" in captured.err

    def test_validate_many_issues_truncated(self, tmp_path, capsys):
        """Test that validation shows max 10 issues"""
        # Create file with 15 invalid lines
        invalid_lines = [f"INVALID{i}\n" for i in range(15)]
        env_content = "".join(invalid_lines)
        env_file = tmp_path / ".env"
        env_file.write_text(env_content)

        with pytest.raises(SystemExit) as excinfo:
            validate_syntax(env_file)

        assert excinfo.value.code == 1
        captured = capsys.readouterr()
        assert "Found 15 syntax issue(s)" in captured.out
        assert "... and 5 more" in captured.out


class TestMain:
    """Tests for main CLI function"""

    def test_main_list_command(self, tmp_path, monkeypatch, capsys):
        """Test main with list command"""
        env_content = "API_KEY=secret\nPORT=8080\n"
        env_file = tmp_path / ".env"
        env_file.write_text(env_content)

        monkeypatch.setattr(sys, 'argv', ['env-safe', '--file', str(env_file), 'list'])

        main()

        captured = capsys.readouterr()
        assert "API_KEY" in captured.out
        assert "PORT" in captured.out

    def test_main_list_with_status(self, tmp_path, monkeypatch, capsys):
        """Test main with list --status command"""
        env_content = "API_KEY=secret\nEMPTY=\n"
        env_file = tmp_path / ".env"
        env_file.write_text(env_content)

        monkeypatch.setattr(sys, 'argv', ['env-safe', '--file', str(env_file), 'list', '--status'])

        main()

        captured = capsys.readouterr()
        assert "STATUS" in captured.out
        assert "defined" in captured.out
        assert "empty" in captured.out

    def test_main_check_command(self, tmp_path, monkeypatch, capsys):
        """Test main with check command"""
        env_content = "API_KEY=secret\n"
        env_file = tmp_path / ".env"
        env_file.write_text(env_content)

        monkeypatch.setattr(sys, 'argv', ['env-safe', '--file', str(env_file), 'check', 'API_KEY'])

        with pytest.raises(SystemExit) as excinfo:
            main()

        assert excinfo.value.code == 0
        captured = capsys.readouterr()
        assert "API_KEY is defined with a value" in captured.out

    def test_main_count_command(self, tmp_path, monkeypatch, capsys):
        """Test main with count command"""
        env_content = "KEY1=value\nKEY2=value\n"
        env_file = tmp_path / ".env"
        env_file.write_text(env_content)

        monkeypatch.setattr(sys, 'argv', ['env-safe', '--file', str(env_file), 'count'])

        main()

        captured = capsys.readouterr()
        assert "Total variables: 2" in captured.out

    def test_main_validate_command(self, tmp_path, monkeypatch, capsys):
        """Test main with validate command"""
        env_content = "KEY1=value\nKEY2=value\n"
        env_file = tmp_path / ".env"
        env_file.write_text(env_content)

        monkeypatch.setattr(sys, 'argv', ['env-safe', '--file', str(env_file), 'validate'])

        main()

        captured = capsys.readouterr()
        assert "✓ Syntax valid" in captured.out

    def test_main_no_command(self, monkeypatch, capsys):
        """Test main with no command shows help"""
        monkeypatch.setattr(sys, 'argv', ['env-safe'])

        with pytest.raises(SystemExit) as excinfo:
            main()

        assert excinfo.value.code == 1
        captured = capsys.readouterr()
        # Help should be shown
        assert "usage:" in captured.out or "Safely inspect .env files" in captured.out

    def test_main_default_file_location(self, tmp_path, monkeypatch, capsys):
        """Test that default file is .env in current directory"""
        # Change to tmp directory
        original_cwd = os.getcwd()
        os.chdir(tmp_path)

        try:
            # Create .env in current directory
            env_file = tmp_path / ".env"
            env_file.write_text("TEST_KEY=value\n")

            # Run without --file argument
            monkeypatch.setattr(sys, 'argv', ['env-safe', 'list'])

            main()

            captured = capsys.readouterr()
            assert "TEST_KEY" in captured.out
        finally:
            os.chdir(original_cwd)

    def test_main_custom_file_short_flag(self, tmp_path, monkeypatch, capsys):
        """Test using -f short flag for custom file"""
        env_content = "CUSTOM_KEY=value\n"
        env_file = tmp_path / "custom.env"
        env_file.write_text(env_content)

        monkeypatch.setattr(sys, 'argv', ['env-safe', '-f', str(env_file), 'list'])

        main()

        captured = capsys.readouterr()
        assert "CUSTOM_KEY" in captured.out


class TestEdgeCases:
    """Tests for edge cases and unusual scenarios"""

    def test_unicode_in_values(self, tmp_path):
        """Test handling Unicode characters in values"""
        env_content = """
EMOJI=🚀🎉
CHINESE=你好世界
ARABIC=مرحبا
MIXED=Hello世界🌍
"""
        env_file = tmp_path / ".env"
        env_file.write_text(env_content, encoding='utf-8')

        result = parse_env_file(env_file)

        keys = [key for key, _ in result]
        assert "EMOJI" in keys
        assert "CHINESE" in keys
        assert "ARABIC" in keys
        assert "MIXED" in keys

    def test_very_long_values(self, tmp_path):
        """Test handling very long values"""
        long_value = "a" * 10000
        env_content = f"LONG_KEY={long_value}\n"
        env_file = tmp_path / ".env"
        env_file.write_text(env_content)

        result = parse_env_file(env_file)

        assert len(result) == 1
        assert result[0][0] == "LONG_KEY"
        assert result[0][1] is True  # Has value

    def test_many_variables(self, tmp_path):
        """Test handling many variables"""
        env_content = "\n".join([f"VAR{i}=value{i}" for i in range(1000)])
        env_file = tmp_path / ".env"
        env_file.write_text(env_content)

        result = parse_env_file(env_file)

        assert len(result) == 1000

    def test_equals_in_value(self, tmp_path):
        """Test that equals signs in values are handled correctly"""
        env_content = """
CONNECTION_STRING=Server=localhost;Database=test;uid=admin;pwd=pass=word
EQUATION=2+2=4
URL=http://example.com?param1=value1&param2=value2
"""
        env_file = tmp_path / ".env"
        env_file.write_text(env_content)

        result = parse_env_file(env_file)

        keys = [key for key, _ in result]
        assert "CONNECTION_STRING" in keys
        assert "EQUATION" in keys
        assert "URL" in keys
        assert all(has_value for _, has_value in result)

    def test_mixed_line_endings(self, tmp_path):
        """Test handling mixed line endings (CRLF and LF)"""
        # Create file with mixed line endings
        env_content = "KEY1=value1\r\nKEY2=value2\nKEY3=value3\r\n"
        env_file = tmp_path / ".env"
        env_file.write_bytes(env_content.encode('utf-8'))

        result = parse_env_file(env_file)

        keys = [key for key, _ in result]
        assert len(keys) == 3
        assert "KEY1" in keys
        assert "KEY2" in keys
        assert "KEY3" in keys

    def test_empty_lines_between_variables(self, tmp_path):
        """Test that empty lines don't affect parsing"""
        env_content = """

KEY1=value1


KEY2=value2

# Comment

KEY3=value3


"""
        env_file = tmp_path / ".env"
        env_file.write_text(env_content)

        result = parse_env_file(env_file)

        keys = [key for key, _ in result]
        assert len(keys) == 3

    def test_tabs_and_spaces_in_whitespace(self, tmp_path):
        """Test handling of tabs and spaces"""
        env_content = "KEY1\t=\tvalue1\nKEY2  =  value2\nKEY3=value3\n"
        env_file = tmp_path / ".env"
        env_file.write_text(env_content)

        result = parse_env_file(env_file)

        # Should handle tabs and spaces gracefully
        keys = [key for key, _ in result]
        assert len(keys) >= 1  # At least some should parse

    def test_quoted_values_with_spaces(self, tmp_path):
        """Test handling of quoted values with spaces"""
        env_content = """
SINGLE_QUOTED='value with spaces'
DOUBLE_QUOTED="another value with spaces"
UNQUOTED=no spaces here
"""
        env_file = tmp_path / ".env"
        env_file.write_text(env_content)

        result = parse_env_file(env_file)

        result_dict = dict(result)
        # All should have values (quotes are part of the value)
        assert result_dict["SINGLE_QUOTED"] is True
        assert result_dict["DOUBLE_QUOTED"] is True
        assert result_dict["UNQUOTED"] is True


class TestSecurityAndSafety:
    """Tests to ensure sensitive data is never exposed"""

    def test_no_value_in_list_output(self, tmp_path, capsys):
        """Test that list command never shows actual values"""
        env_content = """
API_KEY=super_secret_key_12345
PASSWORD=my_password_here
"""
        env_file = tmp_path / ".env"
        env_file.write_text(env_content)

        list_keys(env_file, show_status=False)

        captured = capsys.readouterr()
        assert "super_secret_key" not in captured.out
        assert "my_password_here" not in captured.out
        assert "API_KEY" in captured.out
        assert "PASSWORD" in captured.out

    def test_no_value_in_check_output(self, tmp_path, capsys):
        """Test that check command never shows actual values"""
        env_content = "SECRET_TOKEN=very_secret_token_value\n"
        env_file = tmp_path / ".env"
        env_file.write_text(env_content)

        with pytest.raises(SystemExit):
            check_key(env_file, "SECRET_TOKEN")

        captured = capsys.readouterr()
        assert "very_secret_token_value" not in captured.out
        assert "SECRET_TOKEN" in captured.out

    def test_no_value_in_validate_output(self, tmp_path, capsys):
        """Test that validate command never shows actual values"""
        env_content = """
API_KEY=secret123
DATABASE_PASSWORD=db_pass_456
"""
        env_file = tmp_path / ".env"
        env_file.write_text(env_content)

        validate_syntax(env_file)

        captured = capsys.readouterr()
        assert "secret123" not in captured.out
        assert "db_pass_456" not in captured.out

    def test_no_value_in_error_messages(self, tmp_path, capsys):
        """Test that error messages don't leak values"""
        env_content = "SECRET_KEY=super_secret\nINVALID LINE WITHOUT EQUALS\n"
        env_file = tmp_path / ".env"
        env_file.write_text(env_content)

        # Validation should warn but not show the secret value
        with pytest.raises(SystemExit):
            validate_syntax(env_file)

        captured = capsys.readouterr()
        assert "super_secret" not in captured.out
        assert "super_secret" not in captured.err


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
