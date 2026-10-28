"""rm run through a wrapper command or by find is still rm."""
import json
import os
import shlex
from pathlib import Path
import subprocess
import sys

import pytest
from rm_block_hook import check_rm_command


@pytest.mark.parametrize("command", [
    "ls | xargs rm",
    "ls | xargs -0 rm -f",
    "fd x | xargs -I {} rm {}",
    "sudo rm foo",
    "sudo -u root rm foo",
    "env A=1 rm foo",
    "command rm foo",
    "nohup rm foo",
    "time rm foo",
    "nice -n 10 rm foo",
    "find . -name x -exec rm {} \\;",
    "find . -name x -exec /bin/rm -f {} +",
    "find . -name x -execdir rm {} +",
    "find . -name x -delete",
    "fd -e zip -x rm",
    "fd -e zip -X rm -f",
    "fd -e zip --exec rm {}",
    "fd -e zip --exec-batch /bin/rm",
    "fdfind -e zip -x rm",
    # A flag of one wrapper is not a value option of another.
    "sudo -n rm foo",
    "sudo -E rm foo",
    "sudo -s rm foo",
    "sudo -S rm foo",
    "sudo -nu root rm foo",
    "env -- rm foo",
    # fd exec values attached to the option, and short clusters.
    "fdfind . --exec=rm",
    "fd . --exec-batch=rm",
    "fd . -xrm",
    "fd . -Xrm",
    "fd -Hx rm",
    # Wrappers inside the command that find or fd runs.
    "find . -exec env rm {} +",
    "find . -exec sudo -n rm {} +",
    "fd -x sudo rm",
    # A redirection attached to the command word.
    "/bin/rm>out.log victim",
    "rm>out.log victim",
    "rm<in.txt victim",
])
def test_blocks_rm_through_a_wrapper_or_find(command):
    assert check_rm_command(command)[0]


@pytest.mark.parametrize("command", [
    "xargs echo rm",
    "echo rm",
    "git rm --cached foo",
    "find . -name '*.rm'",
    "fd -x echo rm",
    "fd -e rm",
    "fd -delete",
    "sudo ls",
    "env A=1 ls",
    # command -v and -V look rm up. They do not run it.
    "command -v rm",
    "command -V rm",
    # In fd -ex, x is the extension that -e takes.
    "fd -ex rm",
    # rm is the user that -u takes.
    "sudo -u rm ls",
    "rmdir>out.log foo",
])
def test_allows_other_commands(command):
    assert not check_rm_command(command)[0]


@pytest.mark.parametrize("command", [
    "sudo -uroot rm victim",
    "env 2>/dev/null rm victim", "printf victim | xargs 2>/dev/null rm",
    "sudo -u 2>/dev/null root rm victim",
    'sudo -p ">" rm victim', 'env 2>"out file" rm victim',
    "sudo -nuroot rm victim",
    "sudo -groot rm victim",
    'sudo -p "Password: " rm victim',
    'sudo -p "Password: " /bin/rm victim',
    'sudo "/bin/rm" victim',
    '"/bin/rm" victim',
    'find . -exec sudo -p "Password: " rm {} +',
    'find . -exec sudo -p "Password: " rm {} \\;',
    'fd -x sudo -p "Password: " rm',
    "find . -exec echo -delete {} + -delete",
    "find . -name -delete -exec rm {} +",
    "fd --extension -xrm -x rm",
    "fd --strip-cwd-prefix -xrm",
    '"/tmp/path with spaces/rm" victim',
])
def test_blocks_deletion_after_option_operands(command: str) -> None:
    """Attached/quoted option values cannot consume the executed command."""
    assert check_rm_command(command)[0]


@pytest.mark.parametrize("command", [
    "sudo -uroot echo rm",
    'env "2>log" echo rm', 'sudo -p "2>log" echo rm',
    "sudo -nu rm echo safe",
    'sudo -p "rm victim" echo safe',
    "find . -name -delete",
    "find . -path -delete",
    "find . -fprintf output -delete",
    "find . -exec echo -delete {} +",
    "find . -exec echo -exec rm {} +",
    "find . -execdir echo -delete {} +",
    "find . -ok echo -delete {} +",
    "fd -x echo -xrm",
    "fd --exec echo --exec=rm",
    "fd --extension -xrm",
    "fd -e -xrm",
    "fd -exec echo safe",
])
def test_allows_deletion_words_used_as_operands(command: str) -> None:
    """Find/fd option operands and exec arguments are data, not options."""
    assert not check_rm_command(command)[0]


@pytest.mark.parametrize(("command", "decision"), [
    ("sudo -uroot rm victim", "block"),
    ("env -S 'rm -f victim'", "block"),
    ("env - rm victim", "block"),
    ("doas -a passwd rm victim", "block"),
    ("doas -C config rm victim", "approve"),
    ("sudo -e rm", "approve"),
    ("sudo --edit rm", "approve"),
    ("sudo -h remote rm victim", "block"),
    ("sudo --host remote rm victim", "block"),
    ("xargs -iI rm victim", "block"),
    ("xargs -eE rm victim", "block"),
    ("xargs --process-slot-var SLOT rm victim", "block"),
    ("xargs --process-slot-var rm echo victim", "approve"),
    ("env -a safe rm victim", "block"),
    ("env --argv0 safe rm victim", "block"),
    ("sudo -l rm victim", "approve"),
    ("sudo --list rm victim", "approve"),
    ("sudo -ulocal rm victim", "block"),
    ("xargs -J {} rm victim", "block"),
    ("xargs -I {} -R 3 rm victim", "block"),
    ("xargs -I {} -S 255 rm victim", "block"),
    ("xargs -J rm echo safe", "approve"),
    ("env --split-string='sudo -n rm victim'", "block"),
    ("xargs --replace rm {}", "block"),
    ("xargs --max-lines rm victim", "block"),
    ("command -pv rm", "approve"),
    ("command -p rm victim", "block"),
    ('sudo -p "Password: " rm victim', "block"),
    ('sudo "/bin/rm" victim', "block"),
    ("find . -name -delete", "approve"),
    ("find . -exec echo -delete {} +", "approve"),
    ("fd -x echo -xrm", "approve"),
    ("find . -exec echo -delete {} + -delete", "block"),
])
def test_hook_json_decisions(
    command: str, decision: str, tmp_path: Path
) -> None:
    """The real hook entry point applies parsing without running the command."""
    victim = tmp_path / "victim"
    victim.write_text("untouched")
    hook = Path(__file__).with_name("rm_block_hook.py")
    result = subprocess.run(
        [sys.executable, str(hook)],
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": command}}),
        text=True,
        capture_output=True,
        cwd=tmp_path,
        env={**os.environ, "SHELL": "/bin/false"},
        timeout=10,
        check=True,
    )
    assert json.loads(result.stdout)["decision"] == decision
    assert victim.read_text() == "untouched"


@pytest.mark.parametrize("command", [
    "env -S 'rm -f victim'",
    "env - rm victim",
    "doas -a passwd rm victim",
    "doas -aC rm victim",
    "sudo -h remote rm victim",
    "sudo --host remote rm victim",
    "sudo -hremote rm victim",
    "xargs -iI rm victim",
    "xargs -eE rm victim",
    "xargs -0iI rm victim",
    "xargs -0eE rm victim",
    "xargs -l2 rm victim",
    "xargs --process-slot-var SLOT rm victim",
    "env -a safe rm victim",
    "env --argv0 safe rm victim",
    "env -aSomething rm victim",
    "env --argv0=Something rm victim",
    "sudo -ulocal rm victim",
    "sudo -nuroot rm victim",
    "env -i - rm victim",
    "sudo env - rm victim",
    "xargs -J {} rm victim",
    "xargs -I {} -R 3 rm victim",
    "xargs -I {} -S 255 rm victim",
    "env --split-string 'rm -f victim'",
    "env --split-string='rm -f victim'",
    "env -S'rm -f victim'",
    "env -iS'rm -f victim'",
    "env -S '' rm victim",
    "env --split-string='' rm victim",
    'env -S "env -S \'sudo -n rm victim\'"',
    "env -S 'sudo -n rm victim'",
    "env -S 'A=1 sudo -n rm victim'",
    "sudo env -S 'rm -f victim'",
    "find . -exec env -S 'rm -f' {} +",
    "fd -x env -S 'rm -f'",
    "xargs --replace rm {}",
    "xargs --max-lines rm victim",
    "xargs --replace={} rm {}",
    "xargs --max-lines=2 rm victim",
    "xargs -I {} rm {}",
    "xargs -L 2 rm victim",
    "command -p rm victim",
])
def test_blocks_wrapper_option_execution(command: str) -> None:
    """Split commands and optional arguments retain the actual executable."""
    assert check_rm_command(command)[0]


@pytest.mark.parametrize("command", [
    "env -S 'echo rm victim'",
    "env - echo rm victim",
    "doas -a rm echo safe",
    "doas -C config rm victim",
    "doas -nCconfig rm victim",
    "sudo -e rm",
    "sudo --edit rm",
    "sudo -Se rm",
    "sudo -h remote -e rm",
    "sudo -h rm echo safe",
    "sudo --host rm echo safe",
    "xargs -iI echo rm",
    "xargs -eE echo rm",
    "xargs -l2 echo rm",
    "xargs --process-slot-var rm echo victim",
    "env -a rm echo safe",
    "env --argv0 rm echo safe",
    "sudo -l rm victim",
    "sudo --list rm victim",
    "sudo -nl rm victim",
    "sudo -ll rm victim",
    "sudo -u root -l rm victim",
    "env -- - rm victim",
    "xargs -J rm echo safe",
    "xargs -I {} -R 3 echo rm",
    "xargs -I {} -S 255 echo rm",
    "env --split-string='echo rm victim'",
    "env -S '-u rm echo safe'",
    "env -uS echo rm",
    "xargs --replace echo rm",
    "xargs --max-lines echo rm",
    "xargs -I rm echo safe",
    "xargs -L 2 echo rm",
    "command -pv rm",
    "command -pV rm",
    "command -vp rm",
    "command -Vpv rm",
])
def test_allows_wrapper_option_lookup_and_arguments(command: str) -> None:
    """Lookup clusters and arguments named rm do not execute it."""
    assert not check_rm_command(command)[0]


@pytest.mark.parametrize(("split_value", "blocked"), [
    (r"rm\_-f victim", True),
    (r"env\_rm\_victim", True),
    (r'"rm"\_-f victim', True),
    (r"rm\c ignored", True),
    (r"echo\_rm victim", False),
    (r'"rm\_-f" victim', False),
    (r"'rm\_-f' victim", False),
    (r"echo\c rm victim", False),
    (r"rm\\_-f victim", False),
    (r"# rm victim", False),
])
def test_env_split_string_escape_rules(split_value: str, blocked: bool) -> None:
    """env split-string escapes retain their own quoting and word boundaries."""
    command = "env -S " + shlex.quote(split_value)
    assert check_rm_command(command)[0] is blocked
