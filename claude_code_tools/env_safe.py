#!/usr/bin/env python3
"""
env-safe: A safe way to inspect .env files without exposing sensitive values.

This tool allows Claude Code and other automated tools to:
- List environment variable keys without showing values
- Check if specific keys exist
- Count the number of variables defined
- Validate .env file syntax

It specifically avoids displaying actual values to prevent accidental exposure
of secrets, API keys, and other sensitive information.
"""

import sys
import argparse
from pathlib import Path
from typing import List, Tuple
import re


def parse_env_file(filepath: Path) -> List[Tuple[str, bool]]:
    """
    Parse a .env file and extract variable names.
    
    Returns:
        List of tuples: (variable_name, has_value)
    """
    if not filepath.exists():
        raise FileNotFoundError(f"File not found: {filepath}")
    
    variables = []
    
    with open(filepath, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f, 1):
            # Skip empty lines and comments
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            
            # Match KEY=value pattern
            match = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$', line)
            if match:
                key = match.group(1)
                value = match.group(2).strip()
                has_value = bool(value and value != '""' and value != "''")
                variables.append((key, has_value))
            elif '=' in line:
                # Malformed line - has = but doesn't match pattern
                print(f"Warning: Line {line_num} appears malformed: {line[:50]}...", 
                      file=sys.stderr)
    
    return variables


def list_keys(filepath: Path, show_status: bool = False) -> None:
    """List all environment variable keys in the file."""
    try:
        variables = parse_env_file(filepath)
        
        if not variables:
            print("No environment variables found in file.")
            return
        
        if show_status:
            print(f"{'KEY':<30} {'STATUS':<10}")
            print("-" * 40)
            for key, has_value in sorted(variables):
                status = "defined" if has_value else "empty"
                print(f"{key:<30} {status:<10}")
        else:
            for key, _ in sorted(variables):
                print(key)
                
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error reading file: {e}", file=sys.stderr)
        sys.exit(1)


def check_key(filepath: Path, key_name: str) -> None:
    """Check if a specific key exists in the .env file."""
    try:
        variables = parse_env_file(filepath)
        
        for key, has_value in variables:
            if key == key_name:
                if has_value:
                    print(f"✓ {key_name} is defined with a value")
                else:
                    print(f"⚠ {key_name} is defined but empty")
                sys.exit(0)
        
        print(f"✗ {key_name} is not defined")
        sys.exit(1)
        
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error reading file: {e}", file=sys.stderr)
        sys.exit(1)


def count_variables(filepath: Path) -> None:
    """Count the number of variables defined."""
    try:
        variables = parse_env_file(filepath)
        total = len(variables)
        with_values = sum(1 for _, has_value in variables if has_value)
        empty = total - with_values
        
        print(f"Total variables: {total}")
        if total > 0:
            print(f"  With values: {with_values}")
            print(f"  Empty: {empty}")
            
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error reading file: {e}", file=sys.stderr)
        sys.exit(1)


def validate_syntax(filepath: Path) -> None:
    """Validate the syntax of the .env file."""
    try:
        if not filepath.exists():
            print(f"Error: File not found: {filepath}", file=sys.stderr)
            sys.exit(1)
            
        issues = []
        valid_lines = 0
        
        with open(filepath, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                
                # Skip empty lines and comments
                if not line or line.startswith('#'):
                    continue
                
                # Check for valid KEY=value pattern
                if not re.match(r'^[A-Za-z_][A-Za-z0-9_]*\s*=.*$', line):
                    if '=' in line:
                        issues.append(f"Line {line_num}: Invalid key format")
                    else:
                        issues.append(f"Line {line_num}: Missing '=' separator")
                else:
                    valid_lines += 1
        
        if issues:
            print(f"✗ Found {len(issues)} syntax issue(s):")
            for issue in issues[:10]:  # Show first 10 issues
                print(f"  {issue}")
            if len(issues) > 10:
                print(f"  ... and {len(issues) - 10} more")
            sys.exit(1)
        else:
            print(f"✓ Syntax valid ({valid_lines} variables defined)")
            
    except Exception as e:
        print(f"Error reading file: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Safely inspect .env files without exposing sensitive values",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  env-safe list                    # List all keys
  env-safe list --status           # List keys with defined/empty status
  env-safe check API_KEY           # Check if API_KEY exists
  env-safe count                   # Count variables
  env-safe validate                # Check syntax
  env-safe list --file config.env  # Use different file

This tool is designed to be safe for automated tools like Claude Code,
preventing accidental exposure of sensitive environment values.
        """
    )
    
    parser.add_argument(
        '--file', '-f',
        default='.env',
        help='Path to env file (default: .env)'
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Commands')
    
    # List command
    list_parser = subparsers.add_parser('list', help='List all environment keys')
    list_parser.add_argument(
        '--status', '-s',
        action='store_true',
        help='Show whether each key has a value'
    )
    
    # Check command
    check_parser = subparsers.add_parser('check', help='Check if a key exists')
    check_parser.add_argument('key', help='The key name to check')
    
    # Count command
    subparsers.add_parser('count', help='Count variables')
    
    # Validate command
    subparsers.add_parser('validate', help='Validate syntax')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    filepath = Path(args.file)
    
    if args.command == 'list':
        list_keys(filepath, args.status)
    elif args.command == 'check':
        check_key(filepath, args.key)
    elif args.command == 'count':
        count_variables(filepath)
    elif args.command == 'validate':
        validate_syntax(filepath)


if __name__ == '__main__':
    main()