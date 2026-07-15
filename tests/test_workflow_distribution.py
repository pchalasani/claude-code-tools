"""Installed-distribution tests for the workflow dashboard."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest


PackageIdentity = tuple[str, str]


def _copy_build_source(repository: Path, destination: Path) -> None:
    """Copy only wheel inputs into an isolated build tree.

    Args:
        repository: Repository checkout containing package sources.
        destination: Temporary directory to populate.
    """
    destination.mkdir()
    for name in ("README.md", "hatch_build.py", "pyproject.toml"):
        shutil.copy2(repository / name, destination / name)
    for name in ("claude_code_tools", "node_ui"):
        shutil.copytree(
            repository / name,
            destination / name,
            ignore=shutil.ignore_patterns(
                "__pycache__",
                "*.pyc",
                "node_modules",
            ),
        )


def _write_fake_npm(bin_dir: Path) -> Path:
    """Create a hermetic npm replacement and return its invocation marker."""
    bin_dir.mkdir()
    marker = bin_dir / "npm-invocations.jsonl"
    helper = bin_dir / "fake_npm.py"
    helper.write_text(
        """import json
import os
import sys
from pathlib import Path

root = Path.cwd()
package_path = root / "package.json"
lock_path = root / "package-lock.json"
if not package_path.is_file() or not lock_path.is_file():
    raise SystemExit("missing package manifest or lockfile")
package_manifest = json.loads(package_path.read_text(encoding="utf-8"))
lock_manifest = json.loads(lock_path.read_text(encoding="utf-8"))
if lock_manifest.get("lockfileVersion") != 3:
    raise SystemExit("unsupported package-lock.json version")
lock_root = lock_manifest.get("packages", {}).get("")
if not isinstance(lock_root, dict):
    raise SystemExit("package-lock.json has no root package")
for field in (
    "name",
    "version",
    "bin",
    "engines",
    "dependencies",
    "devDependencies",
):
    if package_manifest.get(field) != lock_root.get(field):
        raise SystemExit(f"package-lock.json disagrees on {field}")
expected_args = [
    "ci",
    "--omit=dev",
    "--omit=optional",
    "--ignore-scripts",
    "--no-audit",
    "--no-fund",
]
if sys.argv[1:] != expected_args:
    raise SystemExit(f"unexpected npm arguments: {sys.argv[1:]!r}")
package = root / "node_modules" / "build-fixture"
package.mkdir(parents=True)
(package / "package.json").write_text(
    '{"name":"build-fixture","version":"1.0.0"}',
    encoding="utf-8",
)
with Path(os.environ["FAKE_NPM_MARKER"]).open(
    "a", encoding="utf-8"
) as marker:
    marker.write(
        json.dumps(
            {
                "args": sys.argv[1:],
                "cwd": str(root),
                "lockRoot": lock_root,
                "package": package_manifest,
            }
        )
        + "\\n"
    )
""",
        encoding="utf-8",
    )
    if os.name == "nt":
        launcher = bin_dir / "npm.cmd"
        launcher.write_text(
            f'@"{sys.executable}" "{helper}" %*\r\n',
            encoding="utf-8",
        )
    else:
        launcher = bin_dir / "npm"
        launcher.write_text(
            "#!/bin/sh\nexec "
            f'{shlex.quote(sys.executable)} {shlex.quote(str(helper))} "$@"\n',
            encoding="utf-8",
        )
        launcher.chmod(0o755)
    return marker


def _environment_executables(environment_dir: Path) -> tuple[Path, Path]:
    """Return the isolated Python and workflow entry-point paths.

    Args:
        environment_dir: Virtual-environment root.

    Returns:
        The environment's Python interpreter and ``codex-workflows`` script.
    """
    if os.name == "nt":
        scripts = environment_dir / "Scripts"
        return scripts / "python.exe", scripts / "codex-workflows.exe"
    scripts = environment_dir / "bin"
    return scripts / "python", scripts / "codex-workflows"


def _environment_site_packages(environment_dir: Path) -> Path:
    """Return the environment's platform-specific site-packages directory.

    Args:
        environment_dir: Virtual-environment root.

    Returns:
        The environment's site-packages directory.
    """
    if os.name == "nt":
        return environment_dir / "Lib" / "site-packages"
    matches = list(environment_dir.glob("lib/python*/site-packages"))
    assert len(matches) == 1, "expected one project site-packages directory"
    return matches[0]


def _copy_runtime_dependencies(destination: Path) -> None:
    """Copy only dashboard dependencies, excluding repository metadata.

    Args:
        destination: Import directory to populate.
    """
    source = _environment_site_packages(Path(sys.prefix))
    destination.mkdir()
    for package in ("click", "markdown_it", "mdurl", "pygments", "rich"):
        package_source = source / package
        package_destination = destination / package
        if package_source.is_dir():
            shutil.copytree(package_source, package_destination)
        else:
            shutil.copy2(package_source.with_suffix(".py"), destination)


def _locked_node_packages(
    lock_path: Path,
) -> tuple[dict[str, PackageIdentity], set[str], set[str]]:
    """Return locked production packages and excluded dependency paths.

    Args:
        lock_path: npm version-3 lockfile to inspect.

    Returns:
        Production package identities, dev paths, and optional paths.
    """
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    assert lock.get("lockfileVersion") == 3
    packages = lock.get("packages")
    assert isinstance(packages, dict)
    production: dict[str, PackageIdentity] = {}
    development: set[str] = set()
    optional: set[str] = set()
    for package_path, details in packages.items():
        assert isinstance(package_path, str)
        assert isinstance(details, dict)
        if not package_path.startswith("node_modules/"):
            continue
        is_dev = details.get("dev") is True
        is_optional = details.get("optional") is True
        is_dev_optional = details.get("devOptional") is True
        if is_dev or is_dev_optional:
            development.add(package_path)
        if is_optional or is_dev_optional:
            optional.add(package_path)
        if is_dev or is_optional or is_dev_optional:
            continue
        package_name = package_path.rsplit("node_modules/", maxsplit=1)[1]
        version = details.get("version")
        assert isinstance(version, str)
        production[package_path] = (package_name, version)
    return production, development, optional


def _wheel_node_packages(wheel: Path) -> dict[str, PackageIdentity]:
    """Read exact installed Node package identities from a built wheel.

    Args:
        wheel: Wheel containing the packaged ``node_ui`` dependency tree.

    Returns:
        Mapping from normalized lockfile path to package name and version.
    """
    prefix = "node_ui/"
    suffix = "/package.json"
    packages: dict[str, PackageIdentity] = {}
    with zipfile.ZipFile(wheel) as archive:
        for archive_path in archive.namelist():
            if not archive_path.startswith(prefix) or not archive_path.endswith(suffix):
                continue
            package_path = archive_path[len(prefix) : -len(suffix)]
            if not package_path.startswith("node_modules/"):
                continue
            manifest = json.loads(archive.read(archive_path))
            name = manifest.get("name")
            version = manifest.get("version")
            assert isinstance(name, str)
            assert isinstance(version, str)
            assert package_path not in packages
            packages[package_path] = (name, version)
    return packages


def test_node_ui_engine_matches_locked_ink_minimum() -> None:
    """The advertised Node engine must cover both locked Ink entry points."""
    repository = Path(__file__).resolve().parents[1]
    node_ui = repository / "node_ui"
    manifest = json.loads((node_ui / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((node_ui / "package-lock.json").read_text(encoding="utf-8"))
    required_engine = {"node": ">=18"}
    assert manifest.get("engines") == required_engine
    assert lock["packages"][""]["engines"] == required_engine
    assert lock["packages"]["node_modules/ink"]["engines"] == required_engine
    assert (
        lock["packages"]["node_modules/ink-select-input"]["engines"] == required_engine
    )


def test_fake_npm_rejects_inconsistent_real_manifests(tmp_path: Path) -> None:
    """The hermetic installer must enforce npm's manifest-lock contract."""
    repository = Path(__file__).resolve().parents[1]
    project = tmp_path / "node-ui"
    project.mkdir()
    for name in ("package.json", "package-lock.json"):
        shutil.copy2(repository / "node_ui" / name, project / name)
    manifest_path = project / "package.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["dependencies"]["chalk"] = "0.0.0"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    fake_bin = tmp_path / "fake-bin"
    marker = _write_fake_npm(fake_bin)
    environment = os.environ.copy()
    environment["FAKE_NPM_MARKER"] = str(marker)
    environment["PATH"] = os.pathsep.join((str(fake_bin), environment.get("PATH", "")))

    completed = subprocess.run(
        [
            "npm",
            "ci",
            "--omit=dev",
            "--omit=optional",
            "--ignore-scripts",
            "--no-audit",
            "--no-fund",
        ],
        cwd=project,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode != 0
    assert "package-lock.json disagrees on dependencies" in completed.stderr
    assert not marker.exists()


@pytest.mark.integration
def test_wheel_contains_exact_locked_production_node_closure(
    tmp_path: Path,
) -> None:
    """A real locked install packages only production Node dependencies."""
    repository = Path(__file__).resolve().parents[1]
    repository_python = Path(sys.executable)
    npm = shutil.which("npm")
    uv = shutil.which("uv")
    assert npm is not None, "the real packaging test requires npm"
    assert uv is not None, "the real packaging test requires uv"

    source = tmp_path / "source"
    _copy_build_source(repository, source)
    manifest_paths = (
        source / "node_ui" / "package.json",
        source / "node_ui" / "package-lock.json",
    )
    manifest_contents = {path: path.read_bytes() for path in manifest_paths}
    expected, development, optional = _locked_node_packages(manifest_paths[1])
    assert development, "the locked fixture must exercise dev omission"
    assert optional, "the locked fixture must exercise optional omission"

    distribution_dir = tmp_path / "dist"
    build_environment = os.environ.copy()
    build_environment.pop("PYTHONHOME", None)
    build_environment.pop("PYTHONPATH", None)
    build_environment["NPM_CONFIG_CACHE"] = str(tmp_path / "npm-cache")
    build_environment["NPM_CONFIG_UPDATE_NOTIFIER"] = "false"
    build_environment["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    build_environment["PIP_NO_INDEX"] = "1"
    build_environment["UV_NO_PROGRESS"] = "1"
    build = subprocess.run(
        [
            uv,
            "build",
            str(source),
            "--wheel",
            "--out-dir",
            str(distribution_dir),
            "--python",
            str(repository_python),
            "--no-build-isolation",
            "--no-cache",
            "--no-index",
        ],
        cwd=tmp_path,
        env=build_environment,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    assert not (source / "node_ui" / "node_modules").exists()
    assert {path: path.read_bytes() for path in manifest_paths} == manifest_contents

    wheels = list(distribution_dir.glob("claude_code_tools-*.whl"))
    assert len(wheels) == 1
    packaged = _wheel_node_packages(wheels[0])
    assert packaged == expected
    assert packaged.keys().isdisjoint(development)
    assert packaged.keys().isdisjoint(optional)
    assert "node_modules/react-devtools-core" in development
    assert "node_modules/react-devtools-core" in optional


def test_wheel_installs_workflow_entry_point(tmp_path: Path) -> None:
    """Build and execute ``codex-workflows`` from an installed wheel.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    repository = Path(__file__).resolve().parents[1]
    repository_python = Path(sys.executable)

    source = tmp_path / "source"
    _copy_build_source(repository, source)
    assert not (source / "node_ui" / "node_modules").exists()
    stale_dependency = source / "node_ui" / "node_modules" / "stale"
    stale_dependency.mkdir(parents=True)
    (stale_dependency / "broken.js").write_text("broken", encoding="utf-8")
    fake_bin = tmp_path / "fake-bin"
    npm_marker = _write_fake_npm(fake_bin)
    distribution_dir = tmp_path / "dist"
    build_environment = os.environ.copy()
    build_environment.pop("PYTHONHOME", None)
    build_environment.pop("PYTHONPATH", None)
    build_environment["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    build_environment["PIP_NO_INDEX"] = "1"
    build_environment["FAKE_NPM_MARKER"] = str(npm_marker)
    build_environment["UV_NO_PROGRESS"] = "1"
    build_environment["PATH"] = os.pathsep.join(
        (str(fake_bin), build_environment.get("PATH", ""))
    )
    uv = shutil.which("uv")
    assert uv is not None, "the release build requires uv"
    build = subprocess.run(
        [
            uv,
            "build",
            str(source),
            "--out-dir",
            str(distribution_dir),
            "--python",
            str(repository_python),
            "--no-build-isolation",
            "--no-cache",
            "--no-index",
        ],
        cwd=tmp_path,
        env=build_environment,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    npm_invocations = [
        json.loads(line) for line in npm_marker.read_text(encoding="utf-8").splitlines()
    ]
    assert len(npm_invocations) == 1
    npm_invocation = npm_invocations[0]
    assert npm_invocation["args"] == [
        "ci",
        "--omit=dev",
        "--omit=optional",
        "--ignore-scripts",
        "--no-audit",
        "--no-fund",
    ]
    assert Path(npm_invocation["cwd"]) != source / "node_ui"
    assert (stale_dependency / "broken.js").is_file()
    expected_package = json.loads(
        (source / "node_ui" / "package.json").read_text(encoding="utf-8")
    )
    expected_lock = json.loads(
        (source / "node_ui" / "package-lock.json").read_text(encoding="utf-8")
    )
    assert npm_invocation["package"] == expected_package
    assert npm_invocation["lockRoot"] == expected_lock["packages"][""]

    sdists = list(distribution_dir.glob("claude_code_tools-*.tar.gz"))
    assert len(sdists) == 1
    with tarfile.open(sdists[0], "r:gz") as archive:
        sdist_names = archive.getnames()
    required_sdist_suffixes = (
        "/hatch_build.py",
        "/pyproject.toml",
        "/node_ui/package.json",
        "/node_ui/package-lock.json",
        "/claude_code_tools/workflow_cli.py",
    )
    for suffix in required_sdist_suffixes:
        assert any(name.endswith(suffix) for name in sdist_names)
    assert not any("/node_ui/node_modules/" in name for name in sdist_names)

    wheels = list(distribution_dir.glob("claude_code_tools-*.whl"))
    assert len(wheels) == 1
    wheel = wheels[0]
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert "claude_code_tools/workflow_cli.py" in names
        assert "claude_code_tools/workflow_runs.py" in names
        assert "node_ui/package-lock.json" in names
        assert "node_ui/node_modules/build-fixture/package.json" in names
        assert "node_ui/node_modules/stale/broken.js" not in names
        entry_points_name = next(
            name for name in names if name.endswith(".dist-info/entry_points.txt")
        )
        entry_points = archive.read(entry_points_name).decode()
    assert "codex-workflows = claude_code_tools.workflow_cli:main" in entry_points

    environment_dir = tmp_path / "environment"
    create_environment = subprocess.run(
        [
            str(repository_python),
            "-m",
            "venv",
            str(environment_dir),
        ],
        env=build_environment,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert create_environment.returncode == 0, (
        create_environment.stdout + create_environment.stderr
    )
    environment_python, workflow_executable = _environment_executables(environment_dir)
    install = subprocess.run(
        [
            str(environment_python),
            "-m",
            "pip",
            "install",
            "--force-reinstall",
            "--no-deps",
            "--no-index",
            str(wheel),
        ],
        env=build_environment,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert install.returncode == 0, install.stdout + install.stderr
    assert workflow_executable.is_file()

    dependency_site = tmp_path / "runtime-dependencies"
    _copy_runtime_dependencies(dependency_site)
    assert not list(dependency_site.glob("*claude_code_tools*"))
    installed_environment = build_environment.copy()
    installed_environment["PYTHONPATH"] = str(dependency_site)

    help_result = subprocess.run(
        [str(workflow_executable), "--help"],
        cwd=tmp_path,
        env=installed_environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert help_result.returncode == 0, help_result.stdout + help_result.stderr
    assert "Observe local durable dynamic-workflow runs" in help_result.stdout

    workflow_home = tmp_path / "workflow-home"
    run_directory = workflow_home / "runs" / "installed-wheel-smoke"
    run_directory.mkdir(parents=True)
    timestamp = "2026-07-14T15:00:00Z"
    state: dict[str, object] = {
        "completedAt": timestamp,
        "concurrency": 1,
        "createdAt": timestamp,
        "cwd": str(tmp_path),
        "runId": run_directory.name,
        "status": "completed",
        "steps": {},
        "updatedAt": timestamp,
        "version": 1,
        "workflowHash": "installed-wheel-smoke-hash",
        "workflowPath": str(tmp_path / "smoke-workflow.js"),
    }
    (run_directory / "state.json").write_text(
        json.dumps(state),
        encoding="utf-8",
    )
    run_environment = installed_environment.copy()
    run_environment["CODEX_WORKFLOW_HOME"] = str(workflow_home)
    result = subprocess.run(
        [str(workflow_executable), "--json"],
        cwd=tmp_path,
        env=run_environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert [run["runId"] for run in payload["runs"]] == [run_directory.name]
    assert payload["truncated"] is False

    import_probe = subprocess.run(
        [
            str(environment_python),
            "-c",
            (
                "import json; "
                "from importlib.metadata import distribution; "
                "from claude_code_tools import workflow_cli; "
                "dist = distribution('claude-code-tools'); "
                "metadata = next(p for p in dist.files "
                "if str(p).endswith('.dist-info/METADATA')); "
                "print(json.dumps({'module': workflow_cli.__file__, "
                "'metadata': str(dist.locate_file(metadata)), "
                "'directUrl': dist.read_text('direct_url.json')}))"
            ),
        ],
        cwd=tmp_path,
        env=run_environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert import_probe.returncode == 0, import_probe.stdout + import_probe.stderr
    imported = json.loads(import_probe.stdout)
    imported_path = Path(imported["module"]).resolve()
    metadata_path = Path(imported["metadata"]).resolve()
    assert imported_path.is_relative_to(environment_dir.resolve())
    assert metadata_path.is_relative_to(environment_dir.resolve())
    direct_url = json.loads(imported["directUrl"])
    assert direct_url.get("dir_info", {}).get("editable") is not True
    assert str(repository.resolve()) not in direct_url.get("url", "")
