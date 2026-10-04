# Start Here

This guide gets a teammate set up and running the tests. Start in the project root, the folder containing `pyproject.toml`.

## 1. Create the environment

In PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

If PowerShell blocks activation, see [Troubleshooting](TROUBLESHOOTING.md#powershell-wont-activate-the-virtual-environment). You can also use `.venv\Scripts\python.exe` directly without activating.

## 2. Run the tests

```powershell
python -m pytest -q
```

The starter test checks that the package imports. Create a focused test file for the class or behavior you implement, then run just that file while working. For example, after creating `tests/test_catalog.py`:

```powershell
python -m pytest tests/test_catalog.py -q
```

For example, a product stock test should create a `Product`, call `update_stock(-2)`, and assert the quantity decreased by two. The method currently raises `NotImplementedError`, so that behavior test becomes green only after the method is implemented.

## 3. Work in your assigned files

Use the role map in the [README](../README.md#find-your-work) and the detailed [team split](TEAM_SPLIT.md). Before writing a repository subclass, coordinate with Person 1 on `src/inventory/persistence/repository.py`.

## Run the application later

The class methods are intentionally TODOs, so application startup currently stops when it reaches `DatabaseManager`. After the foundation is implemented, launch the CLI:

```powershell
python -m inventory.app.main --mode cli
```

For the backend API, install Flask and start server mode:

```powershell
python -m pip install -e ".[web]"
python -m inventory.app.main --mode server --host 127.0.0.1 --port 5000
```

The standalone equivalent is `python -m inventory.app.server`. For common setup and development problems, see [Troubleshooting](TROUBLESHOOTING.md).