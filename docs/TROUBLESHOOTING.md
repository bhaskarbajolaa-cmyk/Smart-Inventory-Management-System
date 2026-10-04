Troubleshooting

First check two things: your terminal is in the project root, and .venv is active. Always run Python and pip through the same python command so packages land in the environment you are using.

Expected right now (not a setup mistake)

The project is a scaffold, so these errors are normal until the owning teammate finishes their part.

Startup says DatabaseManager has TODO methods

Why: the database singleton/connection contract is not implemented yet.
Fix: Person 1 implements and tests src/inventory/persistence/database.py. Until then, neither interface will launch.

A method raises NotImplementedError

Why: the method is still a stub, or it depends on a teammate's unfinished work.
Fix: search for the method's TODO, implement it in its owning module, and add a focused test. Do not hide the error with a broad exception handler.
Setup problems

No module named inventory

Why: the package is not installed in the active environment, or you are outside the project root.
Fix: from the root, activate .venv and run python -m pip install -e ".[dev]".

No module named pytest

Why: the dev extra is missing from this environment.
Fix: activate .venv, run python -m pip install -e ".[dev]", then check with python -m pytest --version.

No module named flask

Why: the optional web extra is not installed.
Fix: python -m pip install -e ".[web]". The CLI does not need Flask.

Scripts such as inventory-system are not found

Fix: reinstall with python -m pip install -e ., or use the module form from the project root, such as python -m inventory.app.main --mode cli.
PowerShell won't activate the virtual environment

PowerShell may block local activation scripts. For the current terminal only:

powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1

Or skip activation and call the environment's Python directly:

powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
Development problems

A test fails after a method is implemented

Why: the implementation may not match the shared contract or edge-case expectations.
Fix: run only the failing test with python -m pytest path/to/test_file.py -q, then compare the method signature and behavior notes in its TODO and readme.txt.

PermissionError, or HTTP 401 / 403

Why: no valid session, an expired token, or the user's role lacks the permission or table grant.
Fix: log in again, then check the seeded role, permission code, and table-specific grant.

Address already in use on port 5000

Why: another process is using that port.
Fix: pick another port, for example --port 5001.

PostgreSQL connection refused

Why: PostgreSQL is not running, or host/port/database settings don't match your local server.
Fix: start PostgreSQL and verify the connection settings. The in-memory backend works while the PostgreSQL adapter is being developed.

Merge conflicts in shared interfaces

Why: several branches changed the same contract, or a dependency changed without coordination.
Fix: Person 1 owns the Repository contract. Merge it first, rebase dependent branches, and resolve conflicts with the relevant module owner.