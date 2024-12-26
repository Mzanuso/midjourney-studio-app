import sys
import os
from PyQt5.QtWidgets import QApplication
from ui.main_window import MidjourneyStudioApp

def setup_environment():
    """Setup base environment and directories"""
    base_dir = "D:\\AI_Art_Studio"
    directories = [
        os.path.join(base_dir, "midjourney_output"),
        os.path.join(base_dir, "midjourney_output", "01_ANALYSIS"),
        os.path.join(base_dir, "midjourney_output", "CARD"),
        os.path.join(base_dir, "system", "logs")
    ]
    
    for directory in directories:
        os.makedirs(directory, exist_ok=True)

def main():
    """Main application entry point"""
    # Setup environment
    setup_environment()
    
    # Initialize application
    app = QApplication(sys.argv)
    window = MidjourneyStudioApp()
    window.show()
    
    # Start application loop
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()