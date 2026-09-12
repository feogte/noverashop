import runpy

# Bothost may start Python without automatically importing sitecustomize.py.
# Load it explicitly so the runtime fixes are always installed before main.py.
import sitecustomize  # noqa: F401

runpy.run_path("main.py", run_name="__main__")
