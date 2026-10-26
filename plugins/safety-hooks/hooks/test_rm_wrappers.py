"""rm run through a wrapper command or by find is still rm."""
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
