"""Application entry point. Run with: python -m app.main"""

import sys

from PySide6.QtWidgets import QApplication

from app.ui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("T2MIS Encoder")
    app.setOrganizationName("T2MIS Encoder")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
