import os
import pathlib as pl
import tempfile
from unittest.mock import patch


from nexus.cli import shell_completion


def test_detect_shell_from_env():
    with patch.dict(os.environ, {"SHELL": "/bin/bash"}):
        shell_info = shell_completion.detect_shell()
        assert shell_info is not None
        assert shell_info.name == "bash"
        assert shell_info.rc_path == pl.Path.home() / ".bashrc"


def test_detect_shell_zsh():
    with patch.dict(os.environ, {"SHELL": "/usr/bin/zsh"}):
        shell_info = shell_completion.detect_shell()
        assert shell_info is not None
        assert shell_info.name == "zsh"
        assert shell_info.rc_path == pl.Path.home() / ".zshrc"


def test_detect_shell_unsupported():
    with patch.dict(os.environ, {"SHELL": "/bin/fish"}):
        shell_info = shell_completion.detect_shell()
        assert shell_info is None


def test_get_completion_command_bash():
    cmd = shell_completion._get_completion_command("bash")
    assert "register-python-argcomplete nx" in cmd
    assert cmd == 'eval "$(register-python-argcomplete nx)"'


def test_get_completion_command_zsh():
    cmd = shell_completion._get_completion_command("zsh")
    assert "compinit" in cmd
    assert "register-python-argcomplete nx" in cmd


def test_is_completion_in_rc():
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".bashrc") as f:
        f.write("# Some config\n")
        f.write('eval "$(register-python-argcomplete nx)"\n')
        f.write("# More config\n")
        rc_path = pl.Path(f.name)

    try:
        shell_info = shell_completion.ShellInfo(
            name="bash", rc_path=rc_path, completion_command='eval "$(register-python-argcomplete nx)"'
        )
        assert shell_completion.is_completion_in_rc(shell_info)
    finally:
        rc_path.unlink()


def test_is_completion_not_in_rc():
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".bashrc") as f:
        f.write("# Some config\n")
        f.write("# More config\n")
        rc_path = pl.Path(f.name)

    try:
        shell_info = shell_completion.ShellInfo(
            name="bash", rc_path=rc_path, completion_command='eval "$(register-python-argcomplete nx)"'
        )
        assert not shell_completion.is_completion_in_rc(shell_info)
    finally:
        rc_path.unlink()


def test_install_completion():
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".bashrc") as f:
        f.write("# Existing config\n")
        rc_path = pl.Path(f.name)

    with tempfile.TemporaryDirectory() as temp_dir:
        flag_path = pl.Path(temp_dir) / ".completion_installed"

        try:
            shell_info = shell_completion.ShellInfo(
                name="bash", rc_path=rc_path, completion_command='eval "$(register-python-argcomplete nx)"'
            )

            with patch.object(shell_completion, "get_flag_path", return_value=flag_path):
                success, message = shell_completion.install_completion(shell_info)

            assert success
            assert "installed" in message

            content = rc_path.read_text()
            assert "register-python-argcomplete nx" in content
            assert "# Nexus CLI autocomplete" in content

            assert flag_path.exists()

        finally:
            rc_path.unlink()


def test_install_completion_already_in_rc():
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".bashrc") as f:
        f.write("# Existing config\n")
        f.write('eval "$(register-python-argcomplete nx)"\n')
        rc_path = pl.Path(f.name)

    with tempfile.TemporaryDirectory() as temp_dir:
        flag_path = pl.Path(temp_dir) / ".completion_installed"

        try:
            shell_info = shell_completion.ShellInfo(
                name="bash", rc_path=rc_path, completion_command='eval "$(register-python-argcomplete nx)"'
            )

            with patch.object(shell_completion, "get_flag_path", return_value=flag_path):
                success, message = shell_completion.install_completion(shell_info)

            assert success
            assert message == "already_installed"
            assert flag_path.exists()

        finally:
            rc_path.unlink()


def test_flag_path():
    flag_path = shell_completion.get_flag_path()
    assert flag_path == pl.Path.home() / ".nexus" / ".completion_installed"


def test_is_completion_installed():
    with tempfile.TemporaryDirectory() as temp_dir:
        flag_path = pl.Path(temp_dir) / ".completion_installed"

        with patch.object(shell_completion, "get_flag_path", return_value=flag_path):
            assert not shell_completion.is_completion_installed()

            flag_path.parent.mkdir(parents=True, exist_ok=True)
            flag_path.touch()

            assert shell_completion.is_completion_installed()


def test_set_completion_flag():
    with tempfile.TemporaryDirectory() as temp_dir:
        flag_path = pl.Path(temp_dir) / ".completion_installed"

        with patch.object(shell_completion, "get_flag_path", return_value=flag_path):
            shell_completion.set_completion_flag()
            assert flag_path.exists()
