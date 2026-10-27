"""rm run through a wrapper command or by find is still rm."""
import json
import os
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
