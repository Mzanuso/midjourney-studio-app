import sys
from PyQt5.QtWidgets import QApplication
from ui.main_window import MidjourneyStudioApp

def main():
    app = QApplication(sys.argv)
    window = MidjourneyStudioApp()
    window.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()