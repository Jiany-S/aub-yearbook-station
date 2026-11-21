"""PyInstaller entry point; imports collect the desktop backend and Excel readers."""
from yearbook.gui import main

if __name__ == "__main__":
    raise SystemExit(main())
