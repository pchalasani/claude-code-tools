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
])
def test_allows_other_commands(command):
    assert not check_rm_command(command)[0]
